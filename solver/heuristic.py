"""چیدمان ابتکاری با چند شروع تصادفی (پشتیبان CP-SAT و نقطه‌ی شروع آن)."""
import random
import time
from collections import defaultdict
from typing import Optional

import config
from solver.objective import evaluate
from solver.problem import N_SLOTS, Problem, Solution, slot_name

T = len(config.TIME_SLOTS)
REASON_CAPACITY = "ظرفیت زمانی/فضا یا سقف واحد استاد کافی نبود"


def _attempt(problem: Problem, rng: random.Random, noise: float):
    inst_busy, room_busy, mt_busy = defaultdict(set), defaultdict(set), defaultdict(set)
    day_cnt = defaultdict(int)
    load = defaultdict(int, problem.fixed_units)
    for f in problem.fixed:
        inst_busy[f.instructor_id].add(f.slot)
        room_busy[f.classroom_id].add(f.slot)
        mt_busy[(f.major_id, f.term)].add(f.slot)
        day_cnt[(f.major_id, f.term, f.slot // T)] += 1
    rooms_by_type = defaultdict(list)
    for r in problem.rooms:
        rooms_by_type[r["room_type"]].append(r["id"])

    order = sorted(problem.groups, key=lambda g: (
        min(len(g.instructors), 2) if g.fixed_instructor is None else 0,
        -(g.units / len(g.session_nos)) - 0.3 * len(g.audiences) + rng.random() * noise))

    rows, unscheduled = [], []
    for g in order:
        mts = g.audiences
        if g.fixed_instructor is None and not g.instructors:
            unscheduled.append((g, "فاقد استاد مجاز")); continue
        if not rooms_by_type.get(g.room_type):
            unscheduled.append((g, f"فضای مناسب (نوع {g.room_type}) تعریف نشده")); continue
        cands = [g.fixed_instructor] if g.fixed_instructor is not None else list(g.instructors)
        if g.fixed_instructor is None:
            cands = [i for i in cands if load[i] + g.units <= problem.max_units.get(i, 0)]
            if not cands:
                unscheduled.append((g, "سقف واحد اساتید مجاز پر شده")); continue
        best = None
        for inst in cands:
            used_days, local_cnt, plan, cost = set(), defaultdict(int), [], 0.0
            ok = True
            for sn in g.session_nos:
                pick = None
                for t in range(N_SLOTS):
                    d = t // T
                    if d in used_days or t in inst_busy[inst] or any(t in mt_busy[x] for x in mts):
                        continue
                    room = next((r for r in rooms_by_type[g.room_type] if t not in room_busy[r]), None)
                    if room is None:
                        continue
                    cnt = max(day_cnt[(*x, d)] for x in mts) + local_cnt[d]
                    c = config.SLOT_COST[slot_name(t)[1]] + 0.3 * cnt + rng.random() * noise * 0.2
                    if cnt >= config.DAY_SOFT_LIMIT:
                        c += config.DAY_OVERLOAD_COST
                    if problem.previous.get((*g.prev_key_prefix, sn)) == t:
                        c -= config.MOVE_COST
                    if pick is None or c < pick[0]:
                        pick = (c, t, room)
                if pick is None:
                    ok = False; break
                cost += pick[0]; used_days.add(pick[1] // T); local_cnt[pick[1] // T] += 1
                plan.append((sn, pick[1], pick[2]))
            if ok and (best is None or cost + 0.01 * load[inst] < best[0]):
                best = (cost + 0.01 * load[inst], inst, plan)
        if best is None:
            unscheduled.append((g, REASON_CAPACITY)); continue
        _, inst, plan = best
        for sn, t, room in plan:
            inst_busy[inst].add(t); room_busy[room].add(t)
            for x in mts:
                mt_busy[x].add(t); day_cnt[(*x, t // T)] += 1
                rows.append({"major_id": x[0], "target_term": x[1], "course_code": g.course_code,
                             "session_no": sn, "instructor_id": inst, "classroom_id": room, "slot": t})
        if g.fixed_instructor is None:
            load[inst] += g.units
    return rows, unscheduled


def solve_heuristic(problem: Problem, time_limit: float = 10.0, seed: int = 0,
                    max_attempts: int = 40) -> Solution:
    t0 = time.time()
    rng = random.Random(seed)
    best: Optional[Solution] = None
    for k in range(max_attempts):
        rows, uns = _attempt(problem, rng, 0.0 if k == 0 else 1.5)
        score = evaluate(problem, rows)["score"]
        if best is None or score > best.score:
            best = Solution(rows, [{"major_id": a[0], "term": a[1], "course_code": g.course_code,
                                    "name": g.name, "reason": why} for g, why in uns for a in g.audiences],
                            "heuristic", "FEASIBLE", score)
        if time.time() - t0 > time_limit:
            break
    best.elapsed = time.time() - t0
    return best
