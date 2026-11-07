"""مدل CP-SAT (OR-Tools) برای چیدمان سراسری. نیازمند: pip install ortools"""
import time
from collections import defaultdict
from typing import Optional

import config
from solver.heuristic import REASON_CAPACITY
from solver.objective import evaluate
from solver.problem import N_SLOTS, Problem, Solution, slot_name

T = len(config.TIME_SLOTS)


def solve_cpsat(problem: Problem, time_limit: float = 30.0, workers: int = 8,
                hint: Optional[Solution] = None) -> Solution:
    from ortools.sat.python import cp_model  # ImportError به لایه‌ی بالاتر می‌رسد

    t0 = time.time()
    mdl = cp_model.CpModel()
    rooms_by_type = defaultdict(list)
    for r in problem.rooms:
        rooms_by_type[r["room_type"]].append(r["id"])

    busy_inst, busy_room, busy_mt = defaultdict(set), defaultdict(set), defaultdict(set)
    fixed_day = defaultdict(int)
    for f in problem.fixed:
        busy_inst[f.instructor_id].add(f.slot)
        busy_room[f.classroom_id].add(f.slot)
        busy_mt[(f.major_id, f.term)].add(f.slot)
        fixed_day[(f.major_id, f.term, f.slot // T)] += 1

    unscheduled, usable = [], []
    for g in problem.groups:
        insts = [g.fixed_instructor] if g.fixed_instructor is not None else g.instructors
        why = None
        if not insts:
            why = "فاقد استاد مجاز"
        elif not rooms_by_type.get(g.room_type):
            why = f"فضای مناسب (نوع {g.room_type}) تعریف نشده"
        if why:
            unscheduled.extend({"major_id": a[0], "term": a[1], "course_code": g.course_code,
                                "name": g.name, "reason": why} for a in g.audiences)
        else:
            usable.append(g)

    inst_use, room_use, mt_use = defaultdict(list), defaultdict(list), defaultdict(list)
    day_ys = defaultdict(list)
    load_terms = defaultdict(list)
    obj = []
    G, A, Y, B = {}, {}, {}, {}

    for gi, g in enumerate(usable):
        mts = g.audiences
        insts = [g.fixed_instructor] if g.fixed_instructor is not None else list(g.instructors)
        G[gi] = mdl.NewBoolVar(f"G{gi}")
        A[gi] = {i: mdl.NewBoolVar(f"A{gi}_{i}") for i in insts}
        mdl.Add(sum(A[gi].values()) == G[gi])
        if g.fixed_instructor is None:
            for i in insts:
                load_terms[i].append((A[gi][i], g.units))
        obj.append(config.BIG_REWARD * g.units * G[gi])
        same_day = defaultdict(list)
        for sn in g.session_nos:
            ys = {t: mdl.NewBoolVar(f"y{gi}_{sn}_{t}") for t in range(N_SLOTS)
                  if not any(t in busy_mt[x] for x in mts)}
            bs = {r: mdl.NewBoolVar(f"b{gi}_{sn}_{r}") for r in rooms_by_type[g.room_type]}
            Y[gi, sn], B[gi, sn] = ys, bs
            mdl.Add(sum(ys.values()) == G[gi])
            mdl.Add(sum(bs.values()) == G[gi])
            prev = problem.previous.get((*g.prev_key_prefix, sn))
            for t, y in ys.items():
                same_day[t // T].append(y)
                for x in mts:
                    mt_use[(x, t)].append(y)
                    day_ys[(*x, t // T)].append(y)
                obj.append(-config.SLOT_COST[slot_name(t)[1]] * y)
                if prev == t:
                    obj.append(config.MOVE_COST * y)
                for i, a in A[gi].items():          # p ≥ a ∧ y
                    if t in busy_inst[i]:
                        mdl.AddBoolOr([a.Not(), y.Not()])
                    else:
                        p = mdl.NewBoolVar("")
                        mdl.AddBoolOr([a.Not(), y.Not(), p]); inst_use[(i, t)].append(p)
                for r, b in bs.items():              # q ≥ b ∧ y
                    if t in busy_room[r]:
                        mdl.AddBoolOr([b.Not(), y.Not()])
                    else:
                        q = mdl.NewBoolVar("")
                        mdl.AddBoolOr([b.Not(), y.Not(), q]); room_use[(r, t)].append(q)
        if len(g.session_nos) > 1:                   # جلسه‌های یک درس در روزهای متفاوت
            for lst in same_day.values():
                mdl.AddAtMostOne(lst)

    for use in (inst_use, room_use, mt_use):
        for lst in use.values():
            if len(lst) > 1:
                mdl.AddAtMostOne(lst)
    for i, terms in load_terms.items():
        cap = max(0, problem.max_units.get(i, 0) - problem.fixed_units.get(i, 0))
        mdl.Add(sum(v * u for v, u in terms) <= cap)
    for (mj, tm, d), ys in day_ys.items():
        base = fixed_day[(mj, tm, d)]
        if len(ys) + base > config.DAY_SOFT_LIMIT:
            o = mdl.NewIntVar(0, len(ys) + base, "")
            mdl.Add(o >= sum(ys) + base - config.DAY_SOFT_LIMIT)
            obj.append(-config.DAY_OVERLOAD_COST * o)
    mdl.Maximize(sum(obj))

    if hint:
        h = {(r["major_id"], r["target_term"], r["course_code"], r["session_no"]): r for r in hint.rows}
        for gi, g in enumerate(usable):
            hk = lambda sn: (*g.prev_key_prefix, sn)
            mdl.AddHint(G[gi], int(all(hk(sn) in h for sn in g.session_nos)))
            inst = next((h[hk(sn)]["instructor_id"] for sn in g.session_nos if hk(sn) in h), None)
            for i, a in A[gi].items():
                mdl.AddHint(a, int(i == inst))
            for sn in g.session_nos:
                r = h.get(hk(sn))
                for t, y in Y[gi, sn].items():
                    mdl.AddHint(y, int(bool(r) and r["slot"] == t))
                for rid, b in B[gi, sn].items():
                    mdl.AddHint(b, int(bool(r) and r["classroom_id"] == rid))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = 1
    status = solver.Solve(mdl)
    name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solution([], unscheduled, "cpsat", name, float("-inf"), time.time() - t0)

    rows = []
    for gi, g in enumerate(usable):
        if not solver.Value(G[gi]):
            unscheduled.extend({"major_id": a[0], "term": a[1], "course_code": g.course_code,
                                "name": g.name, "reason": REASON_CAPACITY} for a in g.audiences)
            continue
        inst = next(i for i, a in A[gi].items() if solver.Value(a))
        for sn in g.session_nos:
            t = next(t for t, y in Y[gi, sn].items() if solver.Value(y))
            r = next(r for r, b in B[gi, sn].items() if solver.Value(b))
            rows.extend({"major_id": x[0], "target_term": x[1], "course_code": g.course_code,
                         "session_no": sn, "instructor_id": inst, "classroom_id": r, "slot": t} for x in g.audiences)
    return Solution(rows, unscheduled, "cpsat", name, evaluate(problem, rows)["score"], time.time() - t0)
