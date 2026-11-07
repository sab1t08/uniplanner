"""قالب‌بندی پاسخ‌ها به‌صورت قطعی (بدون مدل زبانی)."""
from collections import defaultdict
from typing import List

import config

ROOM_FA = {"THEORY": "تئوری", "LAB": "آزمایشگاه/کارگاه", "SPORTS": "ورزشی"}


def table(headers: List[str], rows: List[List[str]]) -> str:
    esc = lambda x: str(x).replace("|", "/")
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def merge_sections(sessions: List[dict], majors: dict) -> List[dict]:
    """ادغام ردیف‌های یک جلسه‌ی مشترک (چند مخاطب) در یک ردیف."""
    seen = {}
    for s in sessions:
        k = (s["course_code"], s["session_no"], s["day_of_week"], s["time_slot"], s["classroom_id"], s["instructor_id"])
        e = seen.setdefault(k, {**s, "audiences": []})
        e["audiences"].append(f"{majors.get(s['major_id'], s['major_id'])} ترم {s['target_term']}")
    return list(seen.values())


def sessions_table(sessions: List[dict], majors: dict) -> str:
    rows = sorted(merge_sections(sessions, majors),
                  key=lambda s: (config.DAYS.index(s["day_of_week"]), config.TIME_SLOTS.index(s["time_slot"])))
    if not rows:
        return "جلسه‌ای ثبت نشده است."
    return table(["روز", "ساعت", "درس", "استاد", "کلاس", "قفل"],
                 [[s["day_of_week"], s["time_slot"],
                   s["course_name"] + (f" ({'، '.join(s['audiences'])})" if len(s["audiences"]) > 1 else ""),
                   s["instructor_name"], s["room_code"], "بله" if s["is_locked"] else ""] for s in rows])


def unscheduled_list(items: List[dict], limit: int = 10) -> str:
    if not items:
        return "همه‌ی درس‌ها چیده شده‌اند."
    lines = [f"- {u.get('name')} ({u.get('major', u.get('major_id'))}، ترم {u.get('term')}): {u['reason']}"
             for u in items[:limit]]
    if len(items) > limit:
        lines.append(f"- و {len(items) - limit} مورد دیگر")
    return "\n".join(lines)


def schedule_result(res, label: str, majors: dict) -> str:
    msg = [f"برنامه‌ی {label} چیده شد: {res.saved} جلسه ({res.engine}، {res.elapsed:.1f} ثانیه)."]
    if res.unscheduled:
        by = defaultdict(list)
        for u in res.unscheduled:
            by[u["reason"]].append(u)
        msg.append(f"{len(res.unscheduled)} درس چیده نشد:")
        for why, lst in by.items():
            names = "، ".join(dict.fromkeys(u["name"] for u in lst))
            msg.append(f"- {why}: {names}")
    msg += [f"توجه: {n}" for n in res.notes]
    msg.append("برای بازگشت به وضعیت قبل بگویید «برگردون».")
    return "\n".join(msg)


def quality(q: dict) -> str:
    lines = [f"**نمره‌ی کیفیت: {q['score']} از ۱۰۰**",
             f"- پوشش: {q['scheduled']} از {q['expected']} جلسه ({q['coverage']:.0%})",
             f"- جلسه‌های اضافه‌بار روزانه: {q['overload_sessions']}",
             f"- جلسه‌های ساعت اول (۰۷:۳۰): {q['early_sessions']}",
             "- استفاده از فضاها: " + "، ".join(f"{ROOM_FA.get(k, k)} {v:.0%}" for k, v in q["room_utilization"].items())]
    if q["unscheduled"]:
        lines += [f"\nدرس‌های چیده‌نشده ({len(q['unscheduled'])}):", unscheduled_list(q["unscheduled"], 8)]
    lines.append("\nتداخل/نقض قید: " + ("ندارد." if not q["violations"] else f"{len(q['violations'])} مورد، مثلاً: {q['violations'][0]}"))
    return "\n".join(lines)


def instructors(rows: List[dict]) -> str:
    if not rows:
        return "استادی پیدا نشد."
    return table(["نام", "رتبه", "موظفی (واحد)", "دفتر", "ایمیل", "تخصص"],
                 [[r.get("instructor_name", r.get("name")), r.get("academic_rank", ""),
                   f"{r['min_units']} تا {r['max_units']}", r.get("office") or "—",
                   r.get("email") or "—", r.get("bio") or "—"] for r in rows])


def courses(rows: List[dict]) -> str:
    if not rows:
        return "درسی پیدا نشد."
    return table(["کد", "درس", "واحد", "ساعت", "نوع کلاس"],
                 [[r.get("course_code", r.get("code")), r.get("course_name", r.get("name")), r["units"],
                   r["weekly_hours"], ROOM_FA.get(r.get("room_type", r.get("default_room_type")), "")] for r in rows])


def departments(rows: List[dict]) -> str:
    return "\n".join(f"- **{d['name']}** (ساختمان {d['building']}): " + ("، ".join(d["majors"]) or "—") for d in rows)


def free_slots(rows: List[dict]) -> str:
    if not rows:
        return "هیچ زمان خالی مجازی پیدا نشد."
    return table(["روز", "ساعت", "کلاس"], [[r["day"], r["slot"], r["room_code"]] for r in rows])
