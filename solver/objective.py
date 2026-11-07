"""تابع هدف مشترک؛ هم برای انتخاب بهترین جواب و هم برای گزارش کیفیت استفاده می‌شود."""
from collections import defaultdict
from typing import Dict

import config
from solver.problem import Problem, slot_name

T = len(config.TIME_SLOTS)


def evaluate(problem: Problem, rows) -> Dict[str, float]:
    owner = {(*a, g.course_code): g for g in problem.groups for a in g.audiences}
    placements = {}   # (group.key, session_no) → slot (هر بخش مشترک یک‌بار شمرده می‌شود)
    for r in rows:
        g = owner.get((r["major_id"], r["target_term"], r["course_code"]))
        if g is not None:
            placements[(g.key, r["session_no"])] = (g, r["slot"])
    per_group = defaultdict(int)
    day_cnt = defaultdict(int)
    slot_cost = kept = 0
    for (gk, sn), (g, slot) in placements.items():
        per_group[gk] += 1
        slot_cost += config.SLOT_COST[slot_name(slot)[1]]
        for a in g.audiences:
            day_cnt[(*a, slot // T)] += 1
        if problem.previous.get((*g.prev_key_prefix, sn)) == slot:
            kept += 1
    for f in problem.fixed:
        day_cnt[(f.major_id, f.term, f.slot // T)] += 1
    overload = sum(max(0, c - config.DAY_SOFT_LIMIT) for c in day_cnt.values())
    done_units = sum(g.units for g in problem.groups if per_group.get(g.key) == len(g.session_nos))
    score = (config.BIG_REWARD * done_units - slot_cost
             - config.DAY_OVERLOAD_COST * overload + config.MOVE_COST * kept)
    return {"score": score, "units": done_units, "slot_cost": slot_cost, "overload": overload, "kept": kept}
