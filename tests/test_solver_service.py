import unittest

from db import repo
from db.validate import validate_base_data
from solver import reports, service
from solver.checker import check_semester, verify_rows
from solver.heuristic import solve_heuristic
from solver.problem import build_problem
from tests._env import EnvTest

SEM = "1405-1"


class SolverTests(EnvTest):
    def test_full_semester_is_valid(self):
        p = build_problem(SEM)
        s = solve_heuristic(p, time_limit=5)
        self.assertEqual(verify_rows(p, s.rows), [])
        self.assertGreater(len(s.rows), 200)

    def test_shared_courses_are_merged(self):
        p = build_problem(SEM)
        self.assertTrue(any(len(g.audiences) > 1 for g in p.groups))
        s = solve_heuristic(p, time_limit=2)
        by = {}
        for r in s.rows:
            by.setdefault((r["course_code"], r["session_no"]), set()).add((r["slot"], r["instructor_id"], r["classroom_id"]))
        self.assertTrue(all(len(v) == 1 for v in by.values()))   # مخاطبان یک درس مشترک، یک زمان/استاد/کلاس

    def test_data_problems_are_reported(self):
        rep = validate_base_data()
        self.assertTrue(any("تک‌استاده" in m for m in rep["error"]))

    def test_cpsat_if_available(self):
        try:
            import ortools  # noqa: F401
        except ImportError:
            self.skipTest("ortools نصب نیست")
        from solver.cpsat_model import solve_cpsat
        p = build_problem(SEM, [1], [1])
        s = solve_cpsat(p, time_limit=10)
        self.assertEqual(verify_rows(p, s.rows), [])


class ServiceTests(EnvTest):
    def setUp(self):
        super().setUp()
        service.schedule(SEM, [1], [1], time_limit=5)

    def test_schedule_is_clean_and_complete(self):
        self.assertEqual(check_semester(SEM), [])
        self.assertEqual(reports.quality_report(SEM, [1], [1])["coverage"], 1.0)

    def test_reschedule_keeps_locked_and_is_stable(self):
        s0 = repo.list_sessions(SEM, 1, 1)
        keep = s0[0]
        repo.set_locked([keep["id"]], True)
        service.schedule(SEM, [1], [1], time_limit=5)
        after = {(x["course_code"], x["session_no"]): x for x in repo.list_sessions(SEM, 1, 1)}
        k = after[(keep["course_code"], keep["session_no"])]
        self.assertEqual((k["day_of_week"], k["time_slot"], k["classroom_id"]),
                         (keep["day_of_week"], keep["time_slot"], keep["classroom_id"]))
        self.assertEqual(check_semester(SEM), [])

    def test_move_undo_and_conflict(self):
        s = repo.list_sessions(SEM, 1, 1)[0]
        free = service.free_slots(s["id"])
        self.assertTrue(free)
        res = service.move_session(s["id"], free[0]["day"], free[0]["slot"])
        self.assertTrue(res.ok, res.message)
        self.assertEqual(check_semester(SEM), [])
        # انتقال به زمان یک درس دیگر همان ترم باید رد شود
        other = next(x for x in repo.list_sessions(SEM, 1, 1) if x["id"] != s["id"])
        bad = service.move_session(s["id"], other["day_of_week"], other["time_slot"])
        self.assertFalse(bad.ok)
        self.assertTrue(service.undo(SEM).ok)
        back = repo.get_session(s["id"])
        self.assertEqual((back["day_of_week"], back["time_slot"]), (s["day_of_week"], s["time_slot"]))

    def test_locked_session_cannot_move(self):
        s = repo.list_sessions(SEM, 1, 1)[0]
        service.set_lock([s["id"]], True)
        self.assertFalse(service.move_session(s["id"], "شنبه", "09:30-11:30").ok)

    def test_swap(self):
        rows = repo.list_sessions(SEM, 1, 1)
        for a in rows:
            for b in rows:
                if a["id"] < b["id"] and a["course_code"] != b["course_code"]:
                    r = service.swap_sessions(a["id"], b["id"])
                    if r.ok:
                        na, nb = repo.get_session(a["id"]), repo.get_session(b["id"])
                        self.assertEqual((na["day_of_week"], na["time_slot"]), (b["day_of_week"], b["time_slot"]))
                        self.assertEqual((nb["day_of_week"], nb["time_slot"]), (a["day_of_week"], a["time_slot"]))
                        self.assertEqual(check_semester(SEM), [])
                        return
        self.fail("هیچ جفتی قابل تعویض نبود")

    def test_change_room_validates_type(self):
        s = next(x for x in repo.list_sessions(SEM, 1, 1) if x["room_type"] == "THEORY")
        lab = next(r for r in repo.list_rooms() if r["room_type"] == "LAB")
        self.assertFalse(service.change_room(s["id"], lab["id"]).ok)

    def test_change_instructor_requires_competence(self):
        s = repo.list_sessions(SEM, 1, 1)[0]
        comp = repo.competencies()[s["course_code"]]
        outsider = next(i["id"] for i in repo.list_instructors() if i["id"] not in comp)
        self.assertFalse(service.change_instructor(s["id"], outsider).ok)
        self.assertEqual(check_semester(SEM), [])


if __name__ == "__main__":
    unittest.main()
