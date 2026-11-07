"""گزارش کیفیت برنامه."""
from collections import defaultdict
from typing import List, Optional

import config
from db import repo
from db.validate import sessions_needed
from solver.checker import check_semester
from solver.problem import terms_for_semester
from solver.service import unscheduled_report

T = len(config.TIME_SLOTS)


def quality_report(semester: str, major_ids: Optional[List[int]] = None,
                   terms: Optional[List[int]] = None) -> dict:
    major_ids = major_ids or [m["id"] for m in repo.list_majors()]
    terms = terms or terms_for_semester(semester)
    courses = {c["code"]: c for c in repo.list_courses()}
    expected = sum(sessions_needed(courses[r["course_code"]]["weekly_hours"])
                   for r in repo.get_curriculum() if r["major_id"] in major_ids and r["term"] in terms)
    rows = [s for s in repo.list_sessions(semester) if s["major_id"] in major_ids and s["target_term"] in terms]
    n = len(rows)
    coverage = min(1.0, n / expected) if expected else 0.0

    per_day = defaultdict(int)
    for s in rows:
        per_day[(s["major_id"], s["target_term"], s["day_of_week"])] += 1
    overload = sum(max(0, c - config.DAY_SOFT_LIMIT) for c in per_day.values())
    distribution = 1 - overload / n if n else 0.0
    mean_cost = sum(config.SLOT_COST[s["time_slot"]] for s in rows) / n if n else 0
    preference = 1 - mean_cost / max(config.SLOT_COST.values())
    score = round(100 * (0.7 * coverage + 0.2 * distribution + 0.1 * preference), 1)

    rooms = repo.list_rooms()
    all_rows = repo.list_sessions(semester)
    util = {}
    for typ in sorted({r["room_type"] for r in rooms}):
        ids = {r["id"] for r in rooms if r["room_type"] == typ}
        used = {(x["classroom_id"], x["day_of_week"], x["time_slot"]) for x in all_rows if x["classroom_id"] in ids}
        util[typ] = len(used) / (len(ids) * len(config.DAYS) * T)

    insts = {i["id"]: i for i in repo.list_instructors()}
    taught = defaultdict(set)
    for x in all_rows:
        taught[x["instructor_id"]].add(x["course_code"])
    load = sorted(({"name": insts[i]["name"], "units": sum(courses[c]["units"] for c in cs),
                    "max_units": insts[i]["max_units"]} for i, cs in taught.items()),
                  key=lambda d: -d["units"] / d["max_units"])
    return {"expected": expected, "scheduled": n, "coverage": coverage, "score": score,
            "distribution": distribution, "preference": preference, "overload_sessions": overload,
            "early_sessions": sum(1 for s in rows if s["time_slot"] == config.TIME_SLOTS[0]),
            "room_utilization": util, "instructor_load": load,
            "unscheduled": unscheduled_report(semester, major_ids, terms),
            "violations": check_semester(semester)}
