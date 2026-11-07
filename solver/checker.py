"""بررسی قطعی صحت برنامه: هم خروجی سولور (verify_rows) و هم برنامه‌ی ذخیره‌شده در دیتابیس (check_semester)."""
from collections import defaultdict
from typing import Dict, List

import config
from db import repo
from db.validate import sessions_needed
from solver.problem import Problem

T = len(config.TIME_SLOTS)


def verify_rows(problem: Problem, rows: List[dict]) -> List[str]:
    v: List[str] = []
    owner = {(*a, g.course_code): g for g in problem.groups for a in g.audiences}
    room_type = {r["id"]: r["room_type"] for r in problem.rooms}
    inst_s, room_s, mt_s = defaultdict(set), defaultdict(set), defaultdict(set)
    for f in problem.fixed:
        inst_s[f.instructor_id].add(f.slot); room_s[f.classroom_id].add(f.slot)
        mt_s[(f.major_id, f.term)].add(f.slot)

    per = defaultdict(lambda: defaultdict(list))    # group.key → session_no → rows
    for r in rows:
        g = owner.get((r["major_id"], r["target_term"], r["course_code"]))
        if g is None:
            v.append(f"جلسه‌ی ناشناخته {r['course_code']}"); continue
        per[g.key][r["session_no"]].append(r)

    load: Dict[int, int] = defaultdict(int, problem.fixed_units)
    for g in problem.groups:
        sess = per.get(g.key)
        if not sess:
            continue
        insts = set()
        days = []
        for sn in g.session_nos:
            lst = sess.get(sn, [])
            places = {(x["slot"], x["instructor_id"], x["classroom_id"]) for x in lst}
            if len(places) != 1 or {(x["major_id"], x["target_term"]) for x in lst} != set(g.audiences):
                v.append(f"جلسه‌ی {sn} از {g.name} ناقص یا ناسازگار است"); continue
            slot, inst, room = next(iter(places))
            insts.add(inst); days.append(slot // T)
            ok_inst = g.instructors if g.fixed_instructor is None else [g.fixed_instructor]
            if inst not in ok_inst:
                v.append(f"استاد غیرمجاز برای {g.name}")
            if room_type.get(room) != g.room_type:
                v.append(f"نوع فضا برای {g.name} نامطابق است")
            for s, k, what in ((inst_s, inst, "استاد"), (room_s, room, "فضا")):
                if slot in s[k]:
                    v.append(f"تداخل {what} در {g.name}")
                s[k].add(slot)
            for a in g.audiences:
                if slot in mt_s[a]:
                    v.append(f"تداخل دانشجویان رشته {a[0]} ترم {a[1]} در {g.name}")
                mt_s[a].add(slot)
        if len(insts) > 1:
            v.append(f"درس {g.name} بیش از یک استاد دارد")
        if len(set(days)) != len(days):
            v.append(f"جلسه‌های {g.name} در یک روز هستند")
        if g.fixed_instructor is None and insts:
            load[next(iter(insts))] += g.units
    for i, u in load.items():
        if u > problem.max_units.get(i, 0):
            v.append(f"سقف واحد استاد {i} رد شده ({u})")
    return v


def check_semester(semester: str) -> List[str]:
    """بررسی برنامه‌ی ذخیره‌شده: تداخل‌ها، نوع فضا، صلاحیت استاد، سقف واحد و تعداد جلسه‌ها."""
    sess = repo.list_sessions(semester)
    comp = repo.competencies()
    max_units = {i["id"]: i["max_units"] for i in repo.list_instructors()}
    courses = {c["code"]: c for c in repo.list_courses()}
    v: List[str] = []
    inst_at, room_at, mt_at = {}, {}, {}
    for s in sess:
        slot = (s["day_of_week"], s["time_slot"])
        sec = (s["course_code"], s["session_no"])
        for table, k, same, kind in (
                (inst_at, (s["instructor_id"], slot), (sec, s["classroom_id"]), "استاد"),
                (room_at, (s["classroom_id"], slot), (sec, s["instructor_id"]), "فضا")):
            if k in table and table[k][0] != same:
                v.append(f"تداخل {kind}: {s['course_name']} با {table[k][1]} در {slot[0]} {slot[1]}")
            table.setdefault(k, (same, s["course_name"]))
        k = ((s["major_id"], s["target_term"]), slot)
        if k in mt_at:
            v.append(f"تداخل دانشجویان: {s['course_name']} با {mt_at[k]} در {slot[0]} {slot[1]}")
        mt_at[k] = s["course_name"]
        if s["room_type"] != courses[s["course_code"]]["default_room_type"]:
            v.append(f"نوع فضا نامطابق: {s['course_name']} در {s['room_code']}")
        if s["instructor_id"] not in comp.get(s["course_code"], []):
            v.append(f"استاد {s['instructor_name']} صلاحیت درس {s['course_name']} را ندارد")
    per = defaultdict(list)
    for s in sess:
        per[(s["major_id"], s["target_term"], s["course_code"])].append(s)
    load = defaultdict(set)
    for (_, _, code), lst in per.items():
        if len(lst) > sessions_needed(courses[code]["weekly_hours"]):
            v.append(f"تعداد جلسه‌های {lst[0]['course_name']} از حد مجاز بیشتر است")
        if len({x["day_of_week"] for x in lst}) != len(lst):
            v.append(f"جلسه‌های {lst[0]['course_name']} در یک روز قرار گرفته‌اند")
        load[lst[0]["instructor_id"]].add(code)
    for i, codes in load.items():
        u = sum(courses[c]["units"] for c in codes)
        if u > max_units.get(i, 0):
            v.append(f"بار استاد شناسه {i}: {u} واحد، بیش از سقف {max_units[i]}")
    return list(dict.fromkeys(v))
