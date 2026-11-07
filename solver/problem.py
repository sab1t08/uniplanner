"""تبدیل داده‌های دیتابیس به یک «مسئله»‌ی خالص (بدون وابستگی به OR-Tools)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config
from db import repo
from db.validate import sessions_needed

N_SLOTS = len(config.DAYS) * len(config.TIME_SLOTS)


def slot_index(day: str, slot: str) -> int:
    return config.DAYS.index(day) * len(config.TIME_SLOTS) + config.TIME_SLOTS.index(slot)


def slot_name(idx: int) -> Tuple[str, str]:
    return config.DAYS[idx // len(config.TIME_SLOTS)], config.TIME_SLOTS[idx % len(config.TIME_SLOTS)]


def terms_for_semester(semester: str) -> List[int]:
    """نیمسال اول = ترم‌های فرد، نیمسال دوم = ترم‌های زوج."""
    if semester.endswith("-1"):
        return [1, 3, 5, 7]
    if semester.endswith("-2"):
        return [2, 4, 6, 8]
    return list(range(1, 9))


@dataclass
class Group:
    """
    یک «بخش» درسی: یک درس با n جلسه‌ی هفتگی و یک استاد.
    اگر درس بین چند رشته/ترم مشترک باشد، یک بخش با چند مخاطب (audiences) است و فقط یک‌بار منابع مصرف می‌کند.
    """
    course_code: str
    audiences: List[Tuple[int, int]]       # (major_id, term)
    name: str
    units: int
    room_type: str
    session_nos: List[int]                 # جلسه‌هایی که باید چیده شوند (قفل‌شده‌ها نیستند)
    instructors: List[int]                 # اساتید مجاز
    fixed_instructor: Optional[int] = None  # اگر بخشی از جلسه‌ها قفل است

    @property
    def key(self):
        return (self.course_code, tuple(self.audiences))

    @property
    def prev_key_prefix(self):
        return (*self.audiences[0], self.course_code)


@dataclass
class Fixed:
    """جلسه‌ای که تغییر نمی‌کند ولی منابع را اشغال می‌کند (قفل‌شده یا خارج از محدوده)."""
    major_id: int
    term: int
    course_code: str
    instructor_id: int
    classroom_id: int
    slot: int


@dataclass
class Problem:
    semester: str
    major_ids: List[int]
    terms: List[int]
    groups: List[Group]
    rooms: List[dict]
    max_units: Dict[int, int]
    fixed: List[Fixed] = field(default_factory=list)
    fixed_units: Dict[int, int] = field(default_factory=dict)   # بار واحد اساتید از جلسه‌های ثابت
    previous: Dict[Tuple[int, int, str, int], int] = field(default_factory=dict)  # برنامه‌ی قبلی (برای کم‌کردن جابه‌جایی)


def build_problem(semester: str, major_ids: Optional[List[int]] = None,
                  terms: Optional[List[int]] = None, merge_shared: bool = True) -> Problem:
    major_ids = major_ids or [m["id"] for m in repo.list_majors()]
    terms = terms or terms_for_semester(semester)
    comp = repo.competencies()
    rooms = repo.list_rooms()
    instructors = {i["id"]: i for i in repo.list_instructors()}
    course_units = {c["code"]: c["units"] for c in repo.list_courses()}

    seen_aud, by_course = set(), {}
    for r in repo.get_curriculum():
        if r["major_id"] in major_ids and r["term"] in terms and (r["major_id"], r["term"], r["course_code"]) not in seen_aud:
            seen_aud.add((r["major_id"], r["term"], r["course_code"]))
            by_course.setdefault(r["course_code"], []).append(r)

    in_scope = lambda s: s["major_id"] in major_ids and s["target_term"] in terms
    fixed, previous, locked_by_key = [], {}, {}
    for s in repo.list_sessions(semester):
        slot = slot_index(s["day_of_week"], s["time_slot"])
        if s["is_locked"] or not in_scope(s):
            fixed.append(Fixed(s["major_id"], s["target_term"], s["course_code"],
                               s["instructor_id"], s["classroom_id"], slot))
            if in_scope(s):
                locked_by_key.setdefault((s["major_id"], s["target_term"], s["course_code"]), []).append(s)
        else:
            previous[(s["major_id"], s["target_term"], s["course_code"], s["session_no"])] = slot

    groups = []
    for code, rs in by_course.items():
        r0 = rs[0]
        n = sessions_needed(r0["weekly_hours"])
        auds = [(r["major_id"], r["term"]) for r in rs]
        any_locked = any((*a, code) in locked_by_key for a in auds)
        parts = [auds] if (merge_shared and len(auds) > 1 and not any_locked) else [[a] for a in auds]
        for part in parts:
            locked = locked_by_key.get((*part[0], code), []) if len(part) == 1 else []
            todo = [k for k in range(1, n + 1) if k not in {s["session_no"] for s in locked}]
            if todo:
                groups.append(Group(code, part, r0["course_name"], r0["units"], r0["room_type"], todo,
                                    comp.get(code, []), locked[0]["instructor_id"] if locked else None))

    fixed_units: Dict[int, int] = {}
    seen = set()
    for f in fixed:
        if (f.course_code, f.instructor_id) not in seen:
            seen.add((f.course_code, f.instructor_id))
            fixed_units[f.instructor_id] = fixed_units.get(f.instructor_id, 0) + course_units.get(f.course_code, 0)

    return Problem(semester, major_ids, terms, groups, rooms,
                   {i: v["max_units"] for i, v in instructors.items()}, fixed, fixed_units, previous)


def expand_rows(problem: Problem, rows: List[dict]) -> List[dict]:
    """(ابزار کمکی) فعلاً ردیف‌های سولور از قبل به‌ازای هر مخاطب هستند؛ این تابع برای سازگاری آینده است."""
    return rows


@dataclass
class Solution:
    rows: List[dict]                     # کلیدها: major_id, target_term, course_code, session_no, instructor_id, classroom_id, slot
    unscheduled: List[dict]              # major_id, term, course_code, name, reason
    engine: str = ""
    status: str = ""
    score: float = 0.0
    elapsed: float = 0.0
    notes: List[str] = field(default_factory=list)
