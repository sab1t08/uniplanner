"""تنها لایه‌ای که SQL می‌نویسد. خروجی‌ها dict یا لیستی از dict هستند."""
import json
import sqlite3
from typing import Any, Dict, List, Optional

import config
from db.conn import base_conn, output_conn
from db.schema import normalize_fa

Row = Dict[str, Any]


def _all(cur) -> List[Row]:
    return [dict(r) for r in cur.fetchall()]


# ───────────── خواندن از دیتابیس پایه ─────────────

def list_majors() -> List[Row]:
    with base_conn() as c:
        return _all(c.execute("SELECT id, name, code, department_id FROM majors ORDER BY id"))


def find_majors_by_alias(text: str) -> List[Row]:
    """تطبیق دقیق (پس از نرمال‌سازی) روی نام مستعار. نتیجه‌ی خالی = ناشناخته."""
    with base_conn() as c:
        return _all(c.execute(
            "SELECT m.id, m.name FROM major_aliases a JOIN majors m ON m.id = a.major_id WHERE a.alias = ?",
            (normalize_fa(text),)))


def list_major_aliases() -> List[Row]:
    with base_conn() as c:
        return _all(c.execute("SELECT alias, major_id FROM major_aliases"))


def list_departments() -> List[Row]:
    with base_conn() as c:
        depts = _all(c.execute("SELECT id, name, building FROM departments ORDER BY id"))
        for d in depts:
            d["majors"] = [r["name"] for r in c.execute(
                "SELECT name FROM majors WHERE department_id = ? ORDER BY id", (d["id"],))]
        return depts


def get_curriculum(major_id: Optional[int] = None, term: Optional[int] = None) -> List[Row]:
    sql = "SELECT * FROM v_curriculum_full WHERE 1=1"
    p: list = []
    if major_id is not None:
        sql += " AND major_id = ?"; p.append(major_id)
    if term is not None:
        sql += " AND term = ?"; p.append(term)
    with base_conn() as c:
        return _all(c.execute(sql + " ORDER BY major_id, term, units DESC, course_code", p))


def list_instructors(major_id: Optional[int] = None) -> List[Row]:
    with base_conn() as c:
        if major_id is None:
            return _all(c.execute("SELECT * FROM instructors ORDER BY rank_priority, id"))
        return _all(c.execute(
            "SELECT * FROM v_instructors_by_major WHERE major_id = ? ORDER BY instructor_name", (major_id,)))


def competencies() -> Dict[str, List[int]]:
    """course_code → شناسه‌ی اساتید مجاز (به ترتیب رتبه)."""
    with base_conn() as c:
        out: Dict[str, List[int]] = {}
        for r in c.execute("""SELECT ic.course_code, ic.instructor_id FROM instructor_competencies ic
                              JOIN instructors i ON i.id = ic.instructor_id
                              ORDER BY i.rank_priority, i.id"""):
            out.setdefault(r["course_code"], []).append(r["instructor_id"])
        return out


def list_rooms() -> List[Row]:
    with base_conn() as c:
        return _all(c.execute("SELECT id, room_code, capacity, room_type FROM classrooms ORDER BY id"))


# ───────────── دیتابیس خروجی ─────────────

_SESSION_SELECT = """
    SELECT s.id, s.semester, s.major_id, s.target_term, s.course_code, s.session_no,
           c.name AS course_name, s.instructor_id, i.name AS instructor_name,
           s.classroom_id, r.room_code, r.room_type,
           s.day_of_week, s.time_slot, s.is_locked
    FROM schedules s
    JOIN base.courses    c ON c.code = s.course_code
    JOIN base.instructors i ON i.id  = s.instructor_id
    JOIN base.classrooms  r ON r.id  = s.classroom_id
"""


def list_sessions(semester: str, major_id: Optional[int] = None, term: Optional[int] = None,
                  instructor_id: Optional[int] = None, classroom_id: Optional[int] = None) -> List[Row]:
    sql, p = _SESSION_SELECT + " WHERE s.semester = ?", [semester]
    for col, val in (("s.major_id", major_id), ("s.target_term", term),
                     ("s.instructor_id", instructor_id), ("s.classroom_id", classroom_id)):
        if val is not None:
            sql += f" AND {col} = ?"; p.append(val)
    with output_conn() as c:
        return _all(c.execute(sql + " ORDER BY s.day_of_week, s.time_slot, s.id", p))


def get_session(session_id: int) -> Optional[Row]:
    with output_conn() as c:
        r = c.execute(_SESSION_SELECT + " WHERE s.id = ?", (session_id,)).fetchone()
        return dict(r) if r else None


def list_courses() -> List[Row]:
    with base_conn() as c:
        return _all(c.execute("SELECT code, name, units, weekly_hours, default_room_type FROM courses ORDER BY code"))


# ───────────── نوشتن (همه تراکنشی و قابل Undo) ─────────────

_RAW = ("semester, major_id, target_term, course_code, session_no, instructor_id, "
        "classroom_id, day_of_week, time_slot, is_locked")
_INSERT = f"""INSERT INTO schedules ({_RAW}) VALUES
    (:semester, :major_id, :target_term, :course_code, :session_no, :instructor_id,
     :classroom_id, :day_of_week, :time_slot, :is_locked)"""


_INSERT_WITH_ID = f"""INSERT INTO schedules (id, {_RAW}) VALUES
    (:id, :semester, :major_id, :target_term, :course_code, :session_no, :instructor_id,
     :classroom_id, :day_of_week, :time_slot, :is_locked)"""


def _log(con, semester: str, action: str) -> None:
    rows = _all(con.execute(f"SELECT id, {_RAW} FROM schedules WHERE semester = ?", (semester,)))
    con.execute("INSERT INTO schedule_log (semester, action, snapshot) VALUES (?, ?, ?)",
                (semester, action, json.dumps(rows, ensure_ascii=False)))
    con.execute("""DELETE FROM schedule_log WHERE semester = ? AND id NOT IN
                   (SELECT id FROM schedule_log WHERE semester = ? ORDER BY id DESC LIMIT ?)""",
                (semester, semester, config.UNDO_DEPTH))


def replace_sessions(semester: str, rows: List[Row], major_ids: Optional[List[int]] = None,
                     terms: Optional[List[int]] = None, action: str = "چیدمان خودکار") -> int:
    """حذف جلسات قفل‌نشده‌ی محدوده + درج ردیف‌های جدید در یک تراکنش. هر خطا = بدون هیچ تغییر."""
    sql, p = "DELETE FROM schedules WHERE semester = ? AND is_locked = 0", [semester]
    for col, vals in (("major_id", major_ids), ("target_term", terms)):
        if vals is not None:
            sql += f" AND {col} IN ({','.join('?' * len(vals))})"; p.extend(vals)
    with output_conn(attach_base=False) as c:
        _log(c, semester, action)
        c.execute(sql, p)
        for r in rows:
            c.execute(_INSERT, {"is_locked": 0, **r, "semester": semester})
    return len(rows)


def update_sessions(session_ids: List[int], action: str, **fields) -> None:
    """
    تغییر day_of_week / time_slot / classroom_id / instructor_id چند جلسه (مثلاً همه‌ی مخاطبان یک درس مشترک).
    با حذف و درج مجدد (همان شناسه) انجام می‌شود تا بررسی تداخل روی وضعیت نهایی باشد. تداخل = IntegrityError.
    """
    allowed = {"day_of_week", "time_slot", "classroom_id", "instructor_id"}
    if not fields or set(fields) - allowed:
        raise ValueError("فیلد نامعتبر")
    ph = ",".join("?" * len(session_ids))
    with output_conn(attach_base=False) as c:
        rows = [dict(x) for x in c.execute(f"SELECT id, {_RAW} FROM schedules WHERE id IN ({ph})", session_ids)]
        if not rows:
            raise LookupError("جلسه‌ای یافت نشد.")
        _log(c, rows[0]["semester"], action)
        c.execute(f"DELETE FROM schedules WHERE id IN ({ph})", session_ids)
        for row in rows:
            row.update(fields)
            c.execute(f"INSERT INTO schedules (id, {_RAW}) VALUES (:id, :semester, :major_id, :target_term, "
                      ":course_code, :session_no, :instructor_id, :classroom_id, :day_of_week, :time_slot, :is_locked)", row)


def swap_slots(ids_a: List[int], ids_b: List[int], action: str = "جابه‌جایی دوطرفه") -> None:
    """تبادل زمان دو بخش (هر کدام یک یا چند ردیف هم‌زمان). حذف و درج مجدد با همان شناسه‌ها."""
    ids = list(ids_a) + list(ids_b)
    ph = ",".join("?" * len(ids))
    with output_conn(attach_base=False) as c:
        rows = {x["id"]: dict(x) for x in c.execute(f"SELECT id, {_RAW} FROM schedules WHERE id IN ({ph})", ids)}
        if len(rows) != len(ids):
            raise LookupError("جلسه‌ای یافت نشد.")
        a0, b0 = rows[ids_a[0]], rows[ids_b[0]]
        _log(c, a0["semester"], action)
        c.execute(f"DELETE FROM schedules WHERE id IN ({ph})", ids)
        for group, other in ((ids_a, b0), (ids_b, a0)):
            for i in group:
                row = dict(rows[i])
                row["day_of_week"], row["time_slot"] = other["day_of_week"], other["time_slot"]
                c.execute(f"INSERT INTO schedules (id, {_RAW}) VALUES (:id, :semester, :major_id, :target_term, "
                          ":course_code, :session_no, :instructor_id, :classroom_id, :day_of_week, :time_slot, :is_locked)", row)


def set_locked(session_ids: List[int], locked: bool) -> int:
    if not session_ids:
        return 0
    ph = ",".join("?" * len(session_ids))
    with output_conn(attach_base=False) as c:
        rows = c.execute(f"SELECT DISTINCT semester FROM schedules WHERE id IN ({ph})", session_ids).fetchall()
        if not rows:
            raise LookupError("جلسه‌ای یافت نشد.")
        _log(c, rows[0]["semester"], "قفل کردن" if locked else "بازکردن قفل")
        return c.execute(f"UPDATE schedules SET is_locked = ? WHERE id IN ({ph})",
                         (int(locked), *session_ids)).rowcount


def undo_last(semester: str) -> Optional[str]:
    """بازگرداندن آخرین تغییر نیمسال. خروجی: شرح عملی که لغو شد، یا None اگر چیزی نبود."""
    with output_conn(attach_base=False) as c:
        r = c.execute("SELECT id, action, snapshot FROM schedule_log WHERE semester = ? "
                      "ORDER BY id DESC LIMIT 1", (semester,)).fetchone()
        if not r:
            return None
        c.execute("DELETE FROM schedules WHERE semester = ?", (semester,))
        for row in json.loads(r["snapshot"]):
            c.execute(_INSERT_WITH_ID, row)
        c.execute("DELETE FROM schedule_log WHERE id = ?", (r["id"],))
        return r["action"]


def undo_depth(semester: str) -> int:
    with output_conn(attach_base=False) as c:
        return c.execute("SELECT COUNT(*) FROM schedule_log WHERE semester = ?", (semester,)).fetchone()[0]


def kpis(semester: str, major_id: int, term: int) -> Row:
    with output_conn(attach_base=False) as c:
        n, profs = c.execute(
            "SELECT COUNT(*), COUNT(DISTINCT instructor_id) FROM schedules "
            "WHERE semester = ? AND major_id = ? AND target_term = ?", (semester, major_id, term)).fetchone()
        locked = c.execute("SELECT COUNT(*) FROM schedules WHERE semester = ? AND is_locked = 1",
                           (semester,)).fetchone()[0]
    return {"scheduled_classes": n, "active_professors": profs, "total_locked": locked}
