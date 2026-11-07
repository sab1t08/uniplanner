"""بررسی سلامت و امکان‌سنجی داده‌ها پیش از اجرای سولور."""
import math
from typing import Dict, List

import config
from db import repo
from db.conn import base_conn

SLOTS_PER_WEEK = len(config.DAYS) * len(config.TIME_SLOTS)


def sessions_needed(weekly_hours: int) -> int:
    return max(1, math.ceil(weekly_hours / config.SLOT_HOURS))


def validate_base_data() -> Dict[str, List[str]]:
    """خروجی: {'error': [...], 'warning': [...], 'info': [...]}"""
    out: Dict[str, List[str]] = {"error": [], "warning": [], "info": []}
    comp = repo.competencies()
    curr = repo.get_curriculum()
    rooms = repo.list_rooms()
    with base_conn() as c:
        courses = {r["code"]: dict(r) for r in c.execute("SELECT * FROM courses")}
        bad_ref = c.execute("""SELECT COUNT(*) FROM curriculum WHERE course_code NOT IN (SELECT code FROM courses)
                               OR major_id NOT IN (SELECT id FROM majors)""").fetchone()[0]
    used = {r["course_code"] for r in curr}

    if bad_ref:
        out["error"].append(f"{bad_ref} ردیف چارت به درس/رشته‌ی ناموجود اشاره دارد.")
    for code in sorted(used):
        if not comp.get(code):
            out["error"].append(f"درس {code} ({courses[code]['name']}) هیچ استاد مجازی ندارد.")
    single = [c for c in used if len(comp.get(c, [])) == 1]
    if single:
        out["warning"].append(f"{len(single)} درس فقط یک استاد مجاز دارند (ریسک بن‌بست).")

    room_count = {t: sum(1 for r in rooms if r["room_type"] == t) for t in config.ROOM_TYPES}
    for t in {courses[c]["default_room_type"] for c in used}:
        if room_count.get(t, 0) == 0:
            out["error"].append(f"برای نوع فضای «{t}» هیچ کلاسی تعریف نشده است.")

    # ظرفیت فضا: جلسات کل (هر رشته/ترم جدا) در برابر اسلات‌های موجود
    for parity, label in ((1, "ترم‌های فرد"), (0, "ترم‌های زوج")):
        demand: Dict[str, int] = {}
        for r in curr:
            if r["term"] % 2 == parity:
                demand[r["room_type"]] = demand.get(r["room_type"], 0) + sessions_needed(r["weekly_hours"])
        for t, n in sorted(demand.items()):
            cap = room_count.get(t, 0) * SLOTS_PER_WEEK
            lvl = "error" if n > cap else "info"
            out[lvl].append(f"{label} | فضای {t}: نیاز {n} جلسه از ظرفیت {cap} ({n / cap:.0%})" if cap
                            else f"{label} | فضای {t}: نیاز {n} جلسه، ظرفیت صفر")

    # بار استادان: واحدهای یکتا در برابر مجموع سقف واحد
    total_cap = sum(i["max_units"] for i in repo.list_instructors())
    for parity, label in ((1, "ترم‌های فرد"), (0, "ترم‌های زوج")):
        codes = {r["course_code"] for r in curr if r["term"] % 2 == parity}
        units = sum(courses[c]["units"] for c in codes)
        lvl = "error" if units > total_cap else "info"
        out[lvl].append(f"{label} | واحدهای یکتا {units} در برابر مجموع سقف اساتید {total_cap}")

    # استادانی که فقط با درس‌های «تک‌استاده»شان از سقف واحد خود می‌گذرند: با هیچ سولوری قابل حل نیست
    insts = {i["id"]: i for i in repo.list_instructors()}
    for parity, label in ((1, "ترم‌های فرد"), (0, "ترم‌های زوج")):
        forced: Dict[int, set] = {}
        for r in curr:
            if r["term"] % 2 == parity and len(comp.get(r["course_code"], [])) == 1:
                forced.setdefault(comp[r["course_code"]][0], set()).add(r["course_code"])
        for i, codes in sorted(forced.items()):
            u = sum(courses[c]["units"] for c in codes)
            if u > insts[i]["max_units"]:
                out["error"].append(f"{label} | {insts[i]['name']}: درس‌های تک‌استاده‌اش {u} واحد است ولی سقف او "
                                    f"{insts[i]['max_units']} واحد (حداقل {u - insts[i]['max_units']} واحد چیده نمی‌شود)")
    return out


if __name__ == "__main__":
    for lvl, msgs in validate_base_data().items():
        for m in msgs:
            print(f"[{lvl}] {m}")
