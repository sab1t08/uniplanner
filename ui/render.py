"""تولید HTML جدول هفتگی و خروجی‌های Excel/CSV. بدون وابستگی به Streamlit (قابل تست)."""
import csv
import html
import io
import re
from collections import defaultdict
from typing import Dict, List, Optional

import config
from agent.formatters import merge_sections
from db import repo

CSS = """
<style>
.tt-wrap { overflow-x: auto; direction: rtl; }
table.tt { border-collapse: separate; border-spacing: 4px; width: 100%; direction: rtl;
           font-family: Vazirmatn, Vazir, Tahoma, "Segoe UI", sans-serif; }
table.tt th { padding: 8px; font-size: 14px; font-weight: 700; border-radius: 8px;
              background: rgba(128,128,128,.16); text-align: center; }
table.tt th.t { white-space: nowrap; width: 92px; }
table.tt td { vertical-align: top; min-width: 130px; height: 84px; padding: 4px;
              border: 1px dashed rgba(128,128,128,.35); border-radius: 8px; }
.tt .c { border-radius: 8px; padding: 7px 9px; margin-bottom: 4px; font-size: 13px; line-height: 1.6;
         border-right: 4px solid; background: rgba(59,130,246,.16); border-color: #3b82f6; }
.tt .c.LAB { background: rgba(168,85,247,.16); border-color: #a855f7; }
.tt .c.SPORTS { background: rgba(16,185,129,.16); border-color: #10b981; }
.tt .c.locked { outline: 2px solid #f59e0b; }
.tt .c b { display: block; font-size: 14px; }
.tt .c span { display: block; opacity: .85; font-size: 12px; }
.tt .lock { font-size: 11px; color: #f59e0b; font-weight: 700; }
.tt .e { opacity: .35; font-size: 12px; display: block; text-align: center; padding-top: 24px; }
</style>
"""


def _e(x) -> str:
    return html.escape(str(x))


def _entries(sessions: List[dict], view: str, majors: Dict[int, str]) -> Dict[tuple, List[dict]]:
    cells: Dict[tuple, List[dict]] = defaultdict(list)
    for s in merge_sections(sessions, majors):
        aud = "، ".join(s["audiences"])
        if view == "instructor":
            lines = [s["course_name"], aud, s["room_code"]]
        elif view == "room":
            lines = [s["course_name"], s["instructor_name"], aud]
        else:
            lines = [s["course_name"], s["instructor_name"], s["room_code"]]
        cells[(s["day_of_week"], s["time_slot"])].append({"lines": lines, "type": s["room_type"], "locked": s["is_locked"]})
    return cells


def grid_html(sessions: List[dict], view: str = "major") -> str:
    majors = {m["id"]: m["name"] for m in repo.list_majors()}
    cells = _entries(sessions, view, majors)
    head = "".join(f"<th>{_e(d)}</th>" for d in config.DAYS)
    body = []
    for slot in config.TIME_SLOTS:
        tds = []
        for day in config.DAYS:
            items = cells.get((day, slot), [])
            if not items:
                tds.append("<td><span class='e'>—</span></td>")
                continue
            div = "".join(
                f"<div class='c {_e(i['type'])}{' locked' if i['locked'] else ''}'>"
                f"<b>{_e(i['lines'][0])}</b><span>{_e(i['lines'][1])}</span><span>{_e(i['lines'][2])}</span>"
                f"{'<span class=lock>قفل‌شده</span>' if i['locked'] else ''}</div>" for i in items)
            tds.append(f"<td>{div}</td>")
        body.append(f"<tr><th class='t'>{_e(slot)}</th>{''.join(tds)}</tr>")
    return (CSS + "<div class='tt-wrap'><table class='tt'><thead><tr><th></th>" + head +
            "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


# ───────────── خروجی فایل ─────────────

_HEAD = ["رشته", "ترم", "کد درس", "درس", "جلسه", "روز", "ساعت", "استاد", "کلاس", "قفل"]


def _flat(semester: str) -> List[list]:
    majors = {m["id"]: m["name"] for m in repo.list_majors()}
    rows = repo.list_sessions(semester)
    rows.sort(key=lambda s: (s["major_id"], s["target_term"], config.DAYS.index(s["day_of_week"]),
                             config.TIME_SLOTS.index(s["time_slot"])))
    return [[majors[s["major_id"]], s["target_term"], s["course_code"], s["course_name"], s["session_no"],
             s["day_of_week"], s["time_slot"], s["instructor_name"], s["room_code"], "بله" if s["is_locked"] else ""]
            for s in rows]


def export_csv(semester: str) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_HEAD)
    w.writerows(_flat(semester))
    return ("\ufeff" + buf.getvalue()).encode("utf-8")      # BOM برای نمایش درست فارسی در Excel


def _sheet_name(base: str, used: set) -> str:
    name = re.sub(r"[\\/*?:\[\]]", "", base)[:28] or "sheet"
    n, i = name, 2
    while n in used:
        n = f"{name[:25]}_{i}"; i += 1
    used.add(n)
    return n


def export_xlsx(semester: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    used: set = set()
    head_fill = PatternFill("solid", fgColor="DDE7F5")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def fix(ws, widths):
        ws.sheet_view.rightToLeft = True
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

    ws = wb.active
    ws.title = _sheet_name("همه جلسات", used)
    ws.append(_HEAD)
    for r in _flat(semester):
        ws.append(r)
    for c in ws[1]:
        c.font, c.fill, c.alignment = Font(bold=True), head_fill, center
    fix(ws, [22, 6, 10, 28, 6, 12, 13, 18, 20, 6])
    ws.freeze_panes = "A2"

    majors = {m["id"]: m for m in repo.list_majors()}
    groups = defaultdict(list)
    for s in repo.list_sessions(semester):
        groups[(s["major_id"], s["target_term"])].append(s)
    for (mid, term), sess in sorted(groups.items()):
        w = wb.create_sheet(_sheet_name(f"{majors[mid]['code']}_T{term}", used))
        w.append(["ساعت", *config.DAYS])
        cells = defaultdict(list)
        for s in sess:
            cells[(s["day_of_week"], s["time_slot"])].append(f"{s['course_name']}\n{s['instructor_name']} | {s['room_code']}")
        for slot in config.TIME_SLOTS:
            w.append([slot, *("\n".join(cells.get((d, slot), [])) for d in config.DAYS)])
        for row in w.iter_rows():
            for c in row:
                c.alignment = center
        for c in w[1]:
            c.font, c.fill = Font(bold=True), head_fill
        for r in range(2, len(config.TIME_SLOTS) + 2):
            w.row_dimensions[r].height = 62
        fix(w, [13] + [26] * len(config.DAYS))
        w.cell(row=len(config.TIME_SLOTS) + 3, column=1, value=f"{majors[mid]['name']} - ترم {term}")

    courses = {c["code"]: c["units"] for c in repo.list_courses()}
    taught = defaultdict(set)
    for s in repo.list_sessions(semester):
        taught[s["instructor_id"]].add(s["course_code"])
    w = wb.create_sheet(_sheet_name("بار اساتید", used))
    w.append(["استاد", "واحد تدریس", "سقف واحد"])
    for i in repo.list_instructors():
        w.append([i["name"], sum(courses[c] for c in taught.get(i["id"], ())), i["max_units"]])
    for c in w[1]:
        c.font, c.fill = Font(bold=True), head_fill
    fix(w, [24, 12, 12])

    from solver.service import unscheduled_report
    miss = unscheduled_report(semester)
    w = wb.create_sheet(_sheet_name("چیده‌نشده‌ها", used))
    w.append(["رشته", "ترم", "درس", "جلسه‌ی ناقص", "دلیل"])
    for u in miss:
        w.append([u["major"], u["term"], u["name"], u["missing"], u["reason"]])
    for c in w[1]:
        c.font, c.fill = Font(bold=True), head_fill
    fix(w, [24, 6, 28, 10, 40])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
