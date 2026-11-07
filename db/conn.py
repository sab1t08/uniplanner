"""مدیریت اتصال‌ها: دیتابیس پایه فقط‌خواندنی، دیتابیس خروجی با ATTACH پایه."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config


def _uri(path: Path, mode: str) -> str:
    return f"{Path(path).resolve().as_uri()}?mode={mode}"


def _finish(con: sqlite3.Connection, ok: bool):
    try:
        con.commit() if ok else con.rollback()
    finally:
        con.close()


@contextmanager
def base_conn(write: bool = False):
    """پیش‌فرض فقط‌خواندنی؛ write=True فقط برای migration."""
    con = sqlite3.connect(_uri(config.BASE_DB, "rw" if write else "ro"), uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    ok = False
    try:
        yield con
        ok = True
    finally:
        _finish(con, ok)


@contextmanager
def output_conn(attach_base: bool = True):
    """اتصال دیتابیس خروجی؛ پایه به‌صورت فقط‌خواندنی با نام `base` الصاق می‌شود."""
    con = sqlite3.connect(str(config.OUTPUT_DB), uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if attach_base:
        con.execute("ATTACH DATABASE ? AS base", (_uri(config.BASE_DB, "ro"),))
    ok = False
    try:
        yield con
        ok = True
    finally:
        _finish(con, ok)
