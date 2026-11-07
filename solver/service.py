"""منطق کاربردی برنامه: چیدمان، جابه‌جایی، تعویض، قفل و Undo. تمام اعتبارسنجی‌ها اینجاست."""
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config
from db import repo
from db.validate import sessions_needed
from solver import engine
from solver.checker import verify_rows
from solver.problem import build_problem, slot_name

T = len(config.TIME_SLOTS)


# ───────────── چیدمان ─────────────

@dataclass
class ScheduleResult:
    saved: int
    unscheduled: List[dict]
    engine: str
    elapsed: float
    score: float
    notes: List[str] = field(default_factory=list)


def schedule(semester: str, major_ids: Optional[List[int]] = None, terms: Optional[List[int]] = None,
             time_limit: Optional[float] = None, label: str = "") -> ScheduleResult:
    problem = build_problem(semester, major_ids, terms)
    sol = engine.solve(problem, time_limit)
    bad = verify_rows(problem, sol.rows)
    if bad:  # خط دفاعی نهایی؛ نباید رخ دهد
        raise RuntimeError("برنامه‌ی تولیدشده معتبر نیست: " + bad[0])
    rows = []
    for r in sol.rows:
        d, t = slot_name(r["slot"])
        rows.append({**{k: v for k, v in r.items() if k != "slot"}, "day_of_week": d, "time_slot": t})
    n = repo.replace_sessions(semester, rows, major_ids=problem.major_ids, terms=problem.terms,
                              action=f"چیدمان خودکار {label}".strip())
    return ScheduleResult(n, sol.unscheduled, sol.engine, sol.elapsed, sol.score, sol.notes)


# ───────────── ابزارهای کمکی بررسی تداخل ─────────────

@dataclass
class OpResult:
    ok: bool
    message: str
    suggestions: List[dict] = field(default_factory=list)


def _slot(s: dict) -> Tuple[str, str]:
    return (s["day_of_week"], s["time_slot"])


def section_rows(session: dict, all_rows: Optional[List[dict]] = None) -> List[dict]:
    """همه‌ی ردیف‌های یک بخش (درس مشترک بین چند رشته/ترم یک جلسه‌ی واحد است)."""
    all_rows = all_rows if all_rows is not None else repo.list_sessions(session["semester"])
    key = ("course_code", "session_no", "day_of_week", "time_slot", "classroom_id", "instructor_id")
    return [x for x in all_rows if all(x[k] == session[k] for k in key)]


def section_key(s: dict) -> tuple:
    return tuple(s[k] for k in ("course_code", "session_no", "day_of_week", "time_slot", "classroom_id", "instructor_id"))


def _busy(all_rows: List[dict], exclude: set):
    inst, room, mt = defaultdict(set), defaultdict(set), defaultdict(set)
    for x in all_rows:
        if x["id"] in exclude:
            continue
        inst[x["instructor_id"]].add(_slot(x)); room[x["classroom_id"]].add(_slot(x))
        mt[(x["major_id"], x["target_term"])].add(_slot(x))
    return inst, room, mt


def _course_days(all_rows: List[dict], sec: List[dict], exclude: set) -> Dict[Tuple[int, int], set]:
    out = defaultdict(set)
    for x in all_rows:
        if x["id"] not in exclude and x["course_code"] == sec[0]["course_code"]:
            out[(x["major_id"], x["target_term"])].add(x["day_of_week"])
    return out


def _check_place(sec, day, slot, room_id, instructor_id, all_rows, exclude) -> Optional[str]:
    """None = مجاز؛ در غیر این صورت دلیل رد."""
    inst, room, mt = _busy(all_rows, exclude)
    if (day, slot) in inst[instructor_id]:
        return "استاد در این زمان کلاس دیگری دارد"
    if (day, slot) in room[room_id]:
        return "کلاس در این زمان پر است"
    days = _course_days(all_rows, sec, exclude)
    for x in sec:
        a = (x["major_id"], x["target_term"])
        if (day, slot) in mt[a]:
            return f"دانشجویان رشته/ترم مربوط ({x['major_id']}-{x['target_term']}) در این زمان درس دیگری دارند"
        if day in days[a]:
            return "جلسه‌ی دیگر همین درس در همان روز است"
    return None


def _free_rooms(room_type: str, day: str, slot: str, all_rows, exclude) -> List[dict]:
    _, room, _ = _busy(all_rows, exclude)
    return [r for r in repo.list_rooms() if r["room_type"] == room_type and (day, slot) not in room[r["id"]]]


def free_slots(session_id: int, limit: int = 8) -> List[dict]:
    s = repo.get_session(session_id)
    if not s:
        return []
    all_rows = repo.list_sessions(s["semester"])
    sec = section_rows(s, all_rows)
    ex = {x["id"] for x in sec}
    out = []
    for d in config.DAYS:
        for t in config.TIME_SLOTS:
            if (d, t) == _slot(s):
                continue
            rooms = _free_rooms(s["room_type"], d, t, all_rows, ex)
            keep = next((r for r in rooms if r["id"] == s["classroom_id"]), None)
            room = keep or (rooms[0] if rooms else None)
            if room and _check_place(sec, d, t, room["id"], s["instructor_id"], all_rows, ex) is None:
                out.append({"day": d, "slot": t, "room_id": room["id"], "room_code": room["room_code"],
                            "cost": config.SLOT_COST[t]})
    out.sort(key=lambda x: (x["cost"], config.DAYS.index(x["day"])))
    return out[:limit]


# ───────────── عملیات ویرایش ─────────────

def move_session(session_id: int, day: str, slot: str) -> OpResult:
    s = repo.get_session(session_id)
    if not s:
        return OpResult(False, "جلسه‌ای با این مشخصات پیدا نشد.")
    if day not in config.DAYS or slot not in config.TIME_SLOTS:
        return OpResult(False, "روز یا ساعت نامعتبر است.")
    all_rows = repo.list_sessions(s["semester"])
    sec = section_rows(s, all_rows)
    if any(x["is_locked"] for x in sec):
        return OpResult(False, "این جلسه قفل است؛ ابتدا قفل را باز کنید.")
    ex = {x["id"] for x in sec}
    rooms = _free_rooms(s["room_type"], day, slot, all_rows, ex)
    keep = next((r for r in rooms if r["id"] == s["classroom_id"]), None)
    room = keep or (rooms[0] if rooms else None)
    why = "هیچ کلاس هم‌نوع خالی نیست" if room is None else \
        _check_place(sec, day, slot, room["id"], s["instructor_id"], all_rows, ex)
    if why:
        return OpResult(False, f"جابه‌جایی ممکن نیست: {why}.", free_slots(session_id, 5))
    try:
        repo.update_sessions([x["id"] for x in sec], f"جابه‌جایی {s['course_name']}",
                             day_of_week=day, time_slot=slot, classroom_id=room["id"])
    except sqlite3.IntegrityError as e:
        return OpResult(False, f"جابه‌جایی ممکن نیست: {e}")
    extra = "" if room["id"] == s["classroom_id"] else f" (کلاس به {room['room_code']} تغییر کرد)"
    return OpResult(True, f"«{s['course_name']}» به {day} {slot} منتقل شد{extra}.")


def swap_sessions(id_a: int, id_b: int) -> OpResult:
    a, b = repo.get_session(id_a), repo.get_session(id_b)
    if not a or not b:
        return OpResult(False, "یکی از جلسه‌ها پیدا نشد.")
    all_rows = repo.list_sessions(a["semester"])
    sa, sb = section_rows(a, all_rows), section_rows(b, all_rows)
    if any(x["is_locked"] for x in sa + sb):
        return OpResult(False, "یکی از جلسه‌ها قفل است.")
    ex = {x["id"] for x in sa + sb}
    for sec, s, other in ((sa, a, b), (sb, b, a)):
        why = _check_place(sec, other["day_of_week"], other["time_slot"], s["classroom_id"],
                           s["instructor_id"], all_rows, ex)
        if why:
            return OpResult(False, f"جابه‌جایی دوطرفه ممکن نیست ({s['course_name']}): {why}.")
    repo.swap_slots([x["id"] for x in sa], [x["id"] for x in sb], f"تعویض {a['course_name']} و {b['course_name']}")
    return OpResult(True, f"زمان «{a['course_name']}» و «{b['course_name']}» با هم عوض شد.")


def change_room(session_id: int, room_id: int) -> OpResult:
    s = repo.get_session(session_id)
    room = next((r for r in repo.list_rooms() if r["id"] == room_id), None)
    if not s or not room:
        return OpResult(False, "جلسه یا کلاس پیدا نشد.")
    if room["room_type"] != s["room_type"]:
        return OpResult(False, f"نوع کلاس «{room['room_code']}» با درس هم‌خوان نیست.")
    all_rows = repo.list_sessions(s["semester"])
    sec = section_rows(s, all_rows)
    if any(x["is_locked"] for x in sec):
        return OpResult(False, "این جلسه قفل است.")
    ex = {x["id"] for x in sec}
    if _check_place(sec, s["day_of_week"], s["time_slot"], room_id, s["instructor_id"], all_rows, ex):
        return OpResult(False, f"کلاس «{room['room_code']}» در این زمان پر است.")
    repo.update_sessions([x["id"] for x in sec], f"تغییر کلاس {s['course_name']}", classroom_id=room_id)
    return OpResult(True, f"کلاس «{s['course_name']}» به {room['room_code']} تغییر کرد.")


def change_instructor(session_id: int, instructor_id: int) -> OpResult:
    """استاد را برای همه‌ی جلسه‌های همین درس (و همه‌ی مخاطبان آن) عوض می‌کند."""
    s = repo.get_session(session_id)
    inst = next((i for i in repo.list_instructors() if i["id"] == instructor_id), None)
    if not s or not inst:
        return OpResult(False, "جلسه یا استاد پیدا نشد.")
    if instructor_id not in repo.competencies().get(s["course_code"], []):
        return OpResult(False, f"{inst['name']} صلاحیت تدریس «{s['course_name']}» را ندارد.")
    all_rows = repo.list_sessions(s["semester"])
    mine = [x for x in all_rows if x["course_code"] == s["course_code"] and x["instructor_id"] == s["instructor_id"]]
    if any(x["is_locked"] for x in mine):
        return OpResult(False, "بخشی از جلسه‌های این درس قفل است.")
    ex = {x["id"] for x in mine}
    inst_busy, _, _ = _busy(all_rows, ex)
    for x in mine:
        if _slot(x) in inst_busy[instructor_id]:
            return OpResult(False, f"{inst['name']} در {x['day_of_week']} {x['time_slot']} کلاس دیگری دارد.")
    courses = {c["code"]: c["units"] for c in repo.list_courses()}
    taught = {x["course_code"] for x in all_rows if x["instructor_id"] == instructor_id}
    load = sum(courses[c] for c in taught) + courses[s["course_code"]]
    if load > inst["max_units"]:
        return OpResult(False, f"با این درس بار {inst['name']} به {load} واحد می‌رسد (سقف {inst['max_units']}).")
    repo.update_sessions([x["id"] for x in mine], f"تعویض استاد {s['course_name']}", instructor_id=instructor_id)
    return OpResult(True, f"استاد «{s['course_name']}» به {inst['name']} تغییر کرد.")


def set_lock(session_ids: List[int], locked: bool) -> OpResult:
    n = repo.set_locked(session_ids, locked)
    return OpResult(True, f"{n} جلسه {'قفل' if locked else 'آزاد'} شد.")


def undo(semester: str) -> OpResult:
    act = repo.undo_last(semester)
    return OpResult(bool(act), f"عملیات «{act}» لغو شد." if act else "چیزی برای بازگرداندن وجود ندارد.")


# ───────────── گزارش چیده‌نشده‌ها ─────────────

def unscheduled_report(semester: str, major_ids: Optional[List[int]] = None,
                       terms: Optional[List[int]] = None) -> List[dict]:
    from solver.problem import terms_for_semester
    major_ids = major_ids or [m["id"] for m in repo.list_majors()]
    terms = terms or terms_for_semester(semester)
    comp = repo.competencies()
    rooms_of = {r["room_type"] for r in repo.list_rooms()}
    courses = {c["code"]: c for c in repo.list_courses()}
    max_units = {i["id"]: i["max_units"] for i in repo.list_instructors()}
    rows = repo.list_sessions(semester)
    have = defaultdict(int)
    taught = defaultdict(set)
    for x in rows:
        have[(x["major_id"], x["target_term"], x["course_code"])] += 1
        taught[x["instructor_id"]].add(x["course_code"])
    load = {i: sum(courses[c]["units"] for c in cs) for i, cs in taught.items()}
    names = {m["id"]: m["name"] for m in repo.list_majors()}
    out = []
    for r in repo.get_curriculum():
        if r["major_id"] not in major_ids or r["term"] not in terms:
            continue
        c = courses[r["course_code"]]
        need, got = sessions_needed(c["weekly_hours"]), have[(r["major_id"], r["term"], r["course_code"])]
        if got >= need:
            continue
        ins = comp.get(r["course_code"], [])
        if not ins:
            why = "فاقد استاد مجاز"
        elif c["default_room_type"] not in rooms_of:
            why = "کلاس هم‌نوع تعریف نشده"
        elif all(load.get(i, 0) + c["units"] > max_units[i] for i in ins):
            why = "سقف واحد اساتید مجاز پر است"
        else:
            why = "ظرفیت زمانی/کلاس کافی نبود"
        out.append({"major_id": r["major_id"], "major": names[r["major_id"]], "term": r["term"],
                    "course_code": r["course_code"], "name": c["name"], "missing": need - got, "reason": why})
    return out
