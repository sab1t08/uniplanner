"""انتخاب خودکار موتور: CP-SAT در صورت نصب‌بودن، با پشتیبان ابتکاری و تأیید خروجی."""
from typing import Optional

import config
from solver.checker import verify_rows
from solver.cpsat_model import solve_cpsat
from solver.heuristic import solve_heuristic
from solver.problem import Problem, Solution


def solve(problem: Problem, time_limit: Optional[float] = None) -> Solution:
    tl = time_limit or config.SOLVER_TIME_LIMIT
    best = solve_heuristic(problem, time_limit=min(10.0, tl / 3))
    notes = []
    try:
        cp = solve_cpsat(problem, time_limit=tl, hint=best)
        bad = verify_rows(problem, cp.rows) if cp.rows else []
        if bad:
            notes.append("خروجی CP-SAT در بازبینی رد شد؛ چیدمان ابتکاری استفاده شد: " + bad[0])
        elif cp.rows and cp.score >= best.score:
            best = cp
    except ImportError:
        notes.append("ortools نصب نیست؛ از چیدمان ابتکاری استفاده شد (pip install ortools).")
    except Exception as e:  # خطای غیرمنتظره‌ی سولور نباید برنامه را بکشد
        notes.append(f"خطای CP-SAT: {e}")
    best.notes = notes
    return best
