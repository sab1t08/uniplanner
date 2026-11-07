import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import config
from db import repo
from db.conn import base_conn
from db.schema import init_all


def _row(**kw):
    d = dict(major_id=1, target_term=1, course_code=None, session_no=1,
             instructor_id=1, classroom_id=1, day_of_week="شنبه", time_slot="09:30-11:30")
    d.update(kw)
    return d


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (config.BASE_DB, config.OUTPUT_DB)
        shutil.copy(self._orig[0], self.tmp / "university.db")
        config.BASE_DB, config.OUTPUT_DB = self.tmp / "university.db", self.tmp / "out.db"
        init_all()
        with base_conn() as c:
            self.a, self.b = [r[0] for r in c.execute("SELECT code FROM courses LIMIT 2")]

    def tearDown(self):
        config.BASE_DB, config.OUTPUT_DB = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_idempotent(self):
        init_all(); init_all()

    def test_base_readonly(self):
        with self.assertRaises(sqlite3.OperationalError):
            with base_conn() as c:
                c.execute("DELETE FROM courses")

    def test_alias_resolution(self):
        self.assertEqual([m["id"] for m in repo.find_majors_by_alias("کامپیوتر")], [1])
        self.assertEqual([m["id"] for m in repo.find_majors_by_alias("علوم کامپیوتر")], [6])
        self.assertEqual(repo.find_majors_by_alias("نامعلوم"), [])

    def test_instructor_conflict(self):
        repo.replace_sessions("1405-1", [_row(course_code=self.a)])
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_sessions("1405-1", [_row(course_code=self.b, major_id=2, classroom_id=2)], major_ids=[2])

    def test_room_and_student_conflict(self):
        repo.replace_sessions("1405-1", [_row(course_code=self.a)])
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_sessions("1405-1", [_row(course_code=self.b, major_id=2, instructor_id=2)], major_ids=[2])
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_sessions("1405-1", [_row(course_code=self.b, instructor_id=2, classroom_id=2)], major_ids=[2])

    def test_atomic_replace_keeps_locked(self):
        repo.replace_sessions("1405-1", [_row(course_code=self.a)])
        sid = repo.list_sessions("1405-1")[0]["id"]
        repo.set_locked([sid], True)
        bad = [_row(course_code=self.b, day_of_week="یکشنبه", instructor_id=2, classroom_id=2),
               _row(course_code=self.b, session_no=2, instructor_id=3, classroom_id=3)]  # تداخل با جلسه‌ی قفل
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_sessions("1405-1", bad, major_ids=[1], terms=[1])
        s = repo.list_sessions("1405-1")
        self.assertEqual((len(s), s[0]["is_locked"]), (1, 1))

    def test_invalid_day(self):
        with self.assertRaises(sqlite3.IntegrityError):
            repo.replace_sessions("1405-1", [_row(course_code=self.a, day_of_week="جمعه")])

    def test_update_and_missing(self):
        repo.replace_sessions("1405-1", [_row(course_code=self.a)])
        sid = repo.list_sessions("1405-1")[0]["id"]
        repo.update_sessions([sid], "x", day_of_week="دوشنبه", time_slot="13:00-15:00")
        self.assertEqual(repo.get_session(sid)["day_of_week"], "دوشنبه")
        with self.assertRaises(LookupError):
            repo.update_sessions([9999], "x", day_of_week="شنبه")

    def test_shared_section_allowed(self):
        rows = [_row(course_code=self.a, major_id=1), _row(course_code=self.a, major_id=2)]  # همان استاد/اتاق/زمان
        repo.replace_sessions("1405-1", rows)
        self.assertEqual(len(repo.list_sessions("1405-1")), 2)

    def test_undo(self):
        repo.replace_sessions("1405-1", [_row(course_code=self.a)])
        repo.replace_sessions("1405-1", [])
        self.assertEqual(repo.list_sessions("1405-1"), [])
        self.assertEqual(repo.undo_last("1405-1"), "چیدمان خودکار")
        self.assertEqual(len(repo.list_sessions("1405-1")), 1)


if __name__ == "__main__":
    unittest.main()
