"""ساخت جدول‌ها و migration. همه‌ی عملیات idempotent هستند."""
import re

import config
from db.conn import base_conn, output_conn

SCHEMA_VERSION = 2


def _in_list(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _schedules_ddl() -> str:
    return f"""
    CREATE TABLE schedules (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        semester      TEXT    NOT NULL,
        major_id      INTEGER NOT NULL,
        target_term   INTEGER NOT NULL CHECK (target_term BETWEEN 1 AND 8),
        course_code   TEXT    NOT NULL,
        session_no    INTEGER NOT NULL DEFAULT 1 CHECK (session_no >= 1),
        instructor_id INTEGER NOT NULL,
        classroom_id  INTEGER NOT NULL,
        day_of_week   TEXT    NOT NULL CHECK (day_of_week IN ({_in_list(config.DAYS)})),
        time_slot     TEXT    NOT NULL CHECK (time_slot IN ({_in_list(config.TIME_SLOTS)})),
        is_locked     INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0, 1)),
        created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        -- تداخل دانشجویان و تکرار جلسه با UNIQUE؛ تداخل استاد/فضا با تریگر (برای مجاز بودن درس‌های مشترک)
        UNIQUE (semester, major_id, target_term, day_of_week, time_slot),
        UNIQUE (semester, major_id, target_term, course_code, session_no)
    )"""


_TRIGGER = """
    CREATE TRIGGER IF NOT EXISTS trg_schedules_no_conflict
    BEFORE INSERT ON schedules
    WHEN EXISTS (
        SELECT 1 FROM schedules
        WHERE semester = NEW.semester AND day_of_week = NEW.day_of_week AND time_slot = NEW.time_slot
          AND ((instructor_id = NEW.instructor_id AND
                (course_code != NEW.course_code OR session_no != NEW.session_no OR classroom_id != NEW.classroom_id))
            OR (classroom_id = NEW.classroom_id AND
                (course_code != NEW.course_code OR session_no != NEW.session_no OR instructor_id != NEW.instructor_id)))
    )
    BEGIN SELECT RAISE(ABORT, 'تداخل استاد یا فضا'); END"""

_LOG_DDL = """
    CREATE TABLE IF NOT EXISTS schedule_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        semester   TEXT NOT NULL,
        action     TEXT NOT NULL,
        snapshot   TEXT NOT NULL,          -- JSON: وضعیت کل نیمسال «پیش از» تغییر
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )"""


def init_output_db() -> None:
    """ساخت جدول‌های schedules و schedule_log. نسخه‌ی قدیمی با ساختار دیگر به schedules_legacy تغییر نام می‌دهد."""
    with output_conn(attach_base=False) as con:
        ver = con.execute("PRAGMA user_version").fetchone()[0]
        if ver >= SCHEMA_VERSION:
            return
        if ver < 1:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone()
            if exists:
                cols = {r["name"] for r in con.execute("PRAGMA table_info(schedules)")}
                if "session_no" not in cols:
                    con.execute("DROP TABLE IF EXISTS schedules_legacy")
                    con.execute("ALTER TABLE schedules RENAME TO schedules_legacy")
                    exists = None
            if not exists:
                con.execute(_schedules_ddl())
        con.execute(_LOG_DDL)
        con.execute(_TRIGGER)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


_FA_NORM = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " ",
                          **{c: str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")},
                          **{c: str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}})


def normalize_fa(text: str) -> str:
    """یکسان‌سازی حروف عربی/فارسی، نیم‌فاصله و فاصله‌ها برای تطبیق نام‌ها."""
    return re.sub(r"\s+", " ", text.translate(_FA_NORM)).strip().lower()


def init_base_db() -> None:
    """افزودن ایندکس‌ها و جدول major_aliases به دیتابیس پایه (فقط افزودنی، بدون تغییر داده‌های موجود)."""
    with base_conn(write=True) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS major_aliases (
                alias    TEXT PRIMARY KEY,
                major_id INTEGER NOT NULL REFERENCES majors(id)
            );
            CREATE INDEX IF NOT EXISTS ix_curr_major_term ON curriculum(major_id, recommended_term);
            CREATE INDEX IF NOT EXISTS ix_curr_course     ON curriculum(course_code);
            CREATE INDEX IF NOT EXISTS ix_comp_course     ON instructor_competencies(course_code);
            CREATE INDEX IF NOT EXISTS ix_comp_instructor ON instructor_competencies(instructor_id);
        """)
        for m in con.execute("SELECT id, name, code FROM majors").fetchall():
            aliases = {m["name"], m["code"], m["name"].replace("مهندسی", "").strip()}
            for a in aliases:
                # INSERT OR IGNORE: اگر نام مستعار برای دو رشته یکی شد، اولین ثبت می‌ماند
                con.execute(
                    "INSERT OR IGNORE INTO major_aliases(alias, major_id) VALUES (?, ?)",
                    (normalize_fa(a), m["id"]),
                )


def init_all() -> None:
    init_base_db()
    init_output_db()
