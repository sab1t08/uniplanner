"""ابزارهای ایجنت: هر ابزار یک اسکیمای JSON برای مدل + یک تابع اجرایی قطعی دارد."""
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import config
from agent import formatters as fmt
from agent import resolver
from db import repo
from db.schema import normalize_fa
from solver import reports, service
from solver.checker import check_semester
from solver.problem import terms_for_semester


class Ask(Exception):
    """اطلاعات کم یا مبهم است؛ متن پیام به‌عنوان سؤال روشن‌سازی به کاربر نشان داده می‌شود."""


@dataclass
class Ctx:
    semester: str = config.DEFAULT_SEMESTER
    major_id: Optional[int] = None
    term: Optional[int] = None
    pending: Optional[dict] = None
    focus: dict = field(default_factory=dict)     # درخواست تغییر نما برای UI
    history: List[dict] = field(default_factory=list)


@dataclass
class Out:
    text: str
    pending: bool = False
    refresh: bool = False


# ───────────── تجزیه‌ی ورودی‌ها ─────────────

TERM_WORDS = {"یک": 1, "اول": 1, "دو": 2, "دوم": 2, "سه": 3, "سوم": 3, "چهار": 4, "چهارم": 4,
              "پنج": 5, "پنجم": 5, "شش": 6, "ششم": 6, "هفت": 7, "هفتم": 7, "هشت": 8, "هشتم": 8}


def parse_term(v) -> Optional[int]:
    if v is None or v == "":
        return None
    t = normalize_fa(str(v))
    m = re.search(r"\d+", t)
    n = int(m.group()) if m else next((x for w, x in TERM_WORDS.items() if w in t.split()), None)
    if n is None or not 1 <= n <= 8:
        raise Ask(f"ترم «{v}» نامعتبر است؛ عددی بین ۱ تا ۸ بگویید.")
    return n


def parse_day(v: str) -> str:
    t = normalize_fa(str(v))
    for d in config.DAYS:
        if normalize_fa(d) == t or normalize_fa(d).replace(" ", "") == t.replace(" ", ""):
            return d
    raise Ask("کدام روز؟ یکی از " + "، ".join(config.DAYS) + " را بگویید (پنجشنبه و جمعه کلاس نداریم).")


_ORD = {"اول": 0, "دوم": 1, "سوم": 2, "چهارم": 3}


def parse_slot(v: str) -> str:
    t = normalize_fa(str(v))
    for s in config.TIME_SLOTS:
        if t.replace(" ", "") == s:
            return s
    m = re.search(r"(\d{1,2})(?::(\d{2}))?", t)
    if m:
        h = int(m.group(1))
        h = h + 12 if 1 <= h <= 6 else h
        for s in config.TIME_SLOTS:
            if int(s[:2]) == h or (h == 9 and s.startswith("09")) or (h == 8 and s.startswith("07")):
                return s
        for s in config.TIME_SLOTS:
            if int(s[:2]) <= h < int(s[6:8]):
                return s
    for w, i in _ORD.items():
        if f"{w}" in t.split():
            return config.TIME_SLOTS[i]
    raise Ask("کدام ساعت؟ یکی از " + "، ".join(config.TIME_SLOTS) + " را بگویید.")


def _majors() -> dict:
    return {m["id"]: m["name"] for m in repo.list_majors()}


def _major_id(ctx: Ctx, args: dict, required: bool = True) -> Optional[int]:
    if args.get("major"):
        r = resolver.resolve_major(args["major"])
        if r.status == "ambiguous":
            raise Ask(f"منظورتان کدام رشته است: {r.labels()}؟")
        if r.status == "none":
            raise Ask(f"رشته‌ی «{args['major']}» را پیدا نکردم.")
        return r.id
    if ctx.major_id is not None:
        return ctx.major_id
    if required:
        raise Ask("برای کدام رشته؟")
    return None


def _term_of(ctx: Ctx, args: dict, required: bool = True) -> Optional[int]:
    t = parse_term(args.get("term"))
    if t is not None:
        return t
    if ctx.term is not None:
        return ctx.term
    if required:
        raise Ask("برای کدام ترم؟")
    return None


def _find_session(ctx: Ctx, course: str, args: dict, label: str = "") -> dict:
    """یک جلسه‌ی مشخص را از روی نام درس (و در صورت نیاز رشته/ترم/شماره‌ی جلسه) پیدا می‌کند."""
    if not course:
        raise Ask(f"نام درس{label} را بگویید.")
    major_id = _major_id(ctx, args, required=False)
    term = parse_term(args.get("term")) or (ctx.term if major_id is not None else None)
    rows = repo.list_sessions(ctx.semester, major_id, term)
    if not rows:
        raise Ask("برای این محدوده برنامه‌ای چیده نشده است؛ ابتدا «برنامه را بچین» بگویید.")
    names = {r["course_code"]: r["course_name"] for r in rows}
    r = resolver.resolve_course(course, set(names))
    if r.status == "none":
        raise Ask(f"درس «{course}»{label} در برنامه‌ی جاری پیدا نشد.")
    if r.status == "ambiguous":
        raise Ask(f"منظورتان کدام درس است: {r.labels()}؟")
    mine = [x for x in rows if x["course_code"] == r.id]
    sn = args.get("session_no")
    if sn:
        mine = [x for x in mine if x["session_no"] == int(sn)]
        if not mine:
            raise Ask(f"جلسه‌ی {sn} برای این درس وجود ندارد.")
    secs = {(x["session_no"], x["day_of_week"], x["time_slot"]) for x in mine}
    if len({x["session_no"] for x in mine}) > 1:
        opts = "، ".join(f"جلسه {n} ({d} {t})" for n, d, t in sorted(secs))
        raise Ask(f"«{names[r.id]}» چند جلسه دارد؛ کدام؟ {opts}")
    if len({service.section_key(x) for x in mine}) > 1:
        who = "، ".join(sorted({f"{_majors()[x['major_id']]} ترم {x['target_term']}" for x in mine}))
        raise Ask(f"«{names[r.id]}» برای چند رشته/ترم ارائه شده ({who}). کدام؟")
    return mine[0]


# ───────────── اجرای ابزارها ─────────────

def _label(major_id, terms) -> str:
    if major_id is None:
        return "کل دانشگاه"
    return f"{_majors()[major_id]}" + (f" ترم {terms[0]}" if terms and len(terms) == 1 else "")


def run_schedule(ctx: Ctx, major_ids, terms) -> Out:
    single = major_ids is not None and len(major_ids) == 1
    label = _label(major_ids[0] if single else None, terms)
    res = service.schedule(ctx.semester, major_ids, terms, label=label)
    ctx.focus = {"view": "major"}
    return Out(fmt.schedule_result(res, label, _majors()), refresh=True)


def schedule_term(ctx: Ctx, a: dict) -> Out:
    if str(a.get("scope", "")).lower() in ("all", "کل", "همه"):
        major_ids, terms = None, None
    else:
        mid = _major_id(ctx, a)
        t = _term_of(ctx, a)
        major_ids, terms = [mid], [t]
        ctx.major_id, ctx.term = mid, t
        ctx.focus = {"view": "major", "major_id": mid, "term": t}
    scope_rows = [s for s in repo.list_sessions(ctx.semester)
                  if not s["is_locked"] and (major_ids is None or s["major_id"] in major_ids)
                  and (terms or terms_for_semester(ctx.semester)).count(s["target_term"])]
    if scope_rows:
        ctx.pending = {"op": "schedule", "major_ids": major_ids, "terms": terms}
        what = _label(major_ids[0], terms) if major_ids else "کل دانشگاه"
        return Out(f"برای {what} از قبل {len(scope_rows)} جلسه‌ی قفل‌نشده چیده شده است و با چیدمان جدید جایگزین می‌شود "
                   f"(جلسه‌های قفل‌شده حفظ می‌شوند). ادامه بدهم؟", pending=True)
    return run_schedule(ctx, major_ids, terms)


def move_session(ctx: Ctx, a: dict) -> Out:
    s = _find_session(ctx, a.get("course"), a)
    day, slot = parse_day(a.get("day") or ""), parse_slot(a.get("time_slot") or "")
    res = service.move_session(s["id"], day, slot)
    text = res.message
    if res.suggestions:
        text += "\nزمان‌های خالی پیشنهادی:\n" + fmt.free_slots(res.suggestions)
    return Out(text, refresh=res.ok)


def swap_sessions(ctx: Ctx, a: dict) -> Out:
    s1 = _find_session(ctx, a.get("course"), a, " اول")
    s2 = _find_session(ctx, a.get("other_course"), a, " دوم")
    res = service.swap_sessions(s1["id"], s2["id"])
    return Out(res.message, refresh=res.ok)


def change_instructor(ctx: Ctx, a: dict) -> Out:
    s = _find_session(ctx, a.get("course"), {**a, "session_no": None})
    r = resolver.resolve_instructor(a.get("instructor") or "")
    if r.status == "none":
        raise Ask(f"استادی با نام «{a.get('instructor')}» پیدا نشد.")
    if r.status == "ambiguous":
        raise Ask(f"منظورتان کدام استاد است: {r.labels()}؟")
    res = service.change_instructor(s["id"], r.id)
    return Out(res.message, refresh=res.ok)


def change_room(ctx: Ctx, a: dict) -> Out:
    s = _find_session(ctx, a.get("course"), a)
    r = resolver.resolve_room(a.get("room") or "")
    if r.status != "ok":
        raise Ask("کدام کلاس؟ " + (r.labels() if r.matches else "نام کلاس را دقیق‌تر بگویید."))
    res = service.change_room(s["id"], r.id)
    return Out(res.message, refresh=res.ok)


def set_lock(ctx: Ctx, a: dict) -> Out:
    locked = a.get("locked", True)
    locked = locked if isinstance(locked, bool) else str(locked).lower() in ("true", "1", "بله", "قفل")
    if a.get("course"):
        s = _find_session(ctx, a["course"], {**a, "session_no": a.get("session_no")})
        ids = [x["id"] for x in repo.list_sessions(ctx.semester) if x["course_code"] == s["course_code"]
               and (x["major_id"], x["target_term"]) == (s["major_id"], s["target_term"])]
    else:
        mid, t = _major_id(ctx, a), _term_of(ctx, a)
        ids = [x["id"] for x in repo.list_sessions(ctx.semester, mid, t)]
    if not ids:
        raise Ask("جلسه‌ای برای قفل/آزادسازی پیدا نشد.")
    return Out(service.set_lock(ids, locked).message, refresh=True)


def show_schedule(ctx: Ctx, a: dict) -> Out:
    view = {"استاد": "instructor", "کلاس": "room", "رشته": "major"}.get(a.get("view", ""), a.get("view") or "major")
    majors = _majors()
    if view == "instructor":
        r = resolver.resolve_instructor(a.get("name") or "")
        if not r.ok:
            raise Ask("برنامه‌ی کدام استاد؟ " + (r.labels() if r.matches else "نام را بگویید."))
        ctx.focus = {"view": "instructor", "instructor_id": r.id}
        rows = repo.list_sessions(ctx.semester, instructor_id=r.id)
        return Out(f"برنامه‌ی {r.labels()}:\n\n" + fmt.sessions_table(rows, majors))
    if view == "room":
        r = resolver.resolve_room(a.get("name") or "")
        if not r.ok:
            raise Ask("برنامه‌ی کدام کلاس؟ " + (r.labels() if r.matches else "نام را بگویید."))
        ctx.focus = {"view": "room", "room_id": r.id}
        rows = repo.list_sessions(ctx.semester, classroom_id=r.id)
        return Out(f"برنامه‌ی {r.labels()}:\n\n" + fmt.sessions_table(rows, majors))
    mid, t = _major_id(ctx, a), _term_of(ctx, a)
    ctx.major_id, ctx.term = mid, t
    ctx.focus = {"view": "major", "major_id": mid, "term": t}
    rows = repo.list_sessions(ctx.semester, mid, t)
    return Out(f"برنامه‌ی {majors[mid]} ترم {t}:\n\n" + fmt.sessions_table(rows, majors))


def instructor_info(ctx: Ctx, a: dict) -> Out:
    if a.get("instructor"):
        r = resolver.resolve_instructor(a["instructor"])
        if r.status == "none":
            raise Ask(f"استادی با نام «{a['instructor']}» پیدا نشد.")
        if r.status == "ambiguous":
            raise Ask(f"منظورتان کدام استاد است: {r.labels()}؟")
        rows = [i for i in repo.list_instructors() if i["id"] == r.id]
        comp = repo.competencies()
        names = {c["code"]: c["name"] for c in repo.list_courses()}
        teach = [names[c] for c, ids in comp.items() if r.id in ids]
        return Out(fmt.instructors(rows) + "\n\nدروس مجاز: " + ("، ".join(teach) or "—"))
    mid = _major_id(ctx, a)
    return Out(f"اعضای هیئت علمی {_majors()[mid]}:\n\n" + fmt.instructors(repo.list_instructors(mid)))


def course_info(ctx: Ctx, a: dict) -> Out:
    mid = _major_id(ctx, a)
    t = _term_of(ctx, a, required=False)
    rows = repo.get_curriculum(mid, t)
    return Out(f"دروس چارت {_majors()[mid]}" + (f" ترم {t}" if t else "") + ":\n\n" + fmt.courses(rows))


def list_departments(ctx: Ctx, a: dict) -> Out:
    return Out(fmt.departments(repo.list_departments()))


def find_free_slots(ctx: Ctx, a: dict) -> Out:
    s = _find_session(ctx, a.get("course"), a)
    return Out(f"زمان‌های خالی مجاز برای «{s['course_name']}»:\n\n" + fmt.free_slots(service.free_slots(s["id"])))


def quality_report(ctx: Ctx, a: dict) -> Out:
    if a.get("major") or (a.get("term") and ctx.major_id):
        mid, t = _major_id(ctx, a), _term_of(ctx, a, required=False)
        return Out(fmt.quality(reports.quality_report(ctx.semester, [mid], [t] if t else None)))
    return Out(fmt.quality(reports.quality_report(ctx.semester)))


def undo(ctx: Ctx, a: dict) -> Out:
    res = service.undo(ctx.semester)
    return Out(res.message, refresh=res.ok)


def execute_pending(ctx: Ctx) -> Out:
    p, ctx.pending = ctx.pending, None
    if p and p["op"] == "schedule":
        return run_schedule(ctx, p["major_ids"], p["terms"])
    return Out("چیزی برای تأیید نبود.")


# ───────────── ثبت ابزارها برای مدل ─────────────

def _s(desc, **extra):
    return {"type": "string", "description": desc, **extra}


_MAJOR = _s("نام رشته؛ اگر کاربر نگفته خالی بگذار")
_TERM = {"type": "integer", "description": "شماره‌ی ترم ۱ تا ۸؛ اگر کاربر نگفته خالی بگذار"}
_COURSE = _s("نام درس همان‌طور که کاربر گفته")
_SESSION_NO = {"type": "integer", "description": "شماره‌ی جلسه‌ی درس (۱ یا ۲)، فقط اگر کاربر گفته"}


def _tool(name, desc, props, required=()):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}


TOOLS: Dict[str, dict] = {t["name"]: t for t in [
    _tool("schedule_term", "چیدمان خودکار برنامه‌ی یک رشته/ترم یا کل دانشگاه",
          {"major": _MAJOR, "term": _TERM, "scope": _s("برای کل دانشگاه مقدار all")}),
    _tool("move_session", "انتقال یک درس به روز و ساعت دیگر",
          {"course": _COURSE, "day": _s("روز هفته"), "time_slot": _s("ساعت یا بازه، مثل ۹:۳۰ یا 13:00-15:00"),
           "major": _MAJOR, "term": _TERM, "session_no": _SESSION_NO}, ["course", "day", "time_slot"]),
    _tool("swap_sessions", "عوض‌کردن زمان دو درس با هم",
          {"course": _COURSE, "other_course": _s("نام درس دوم"), "major": _MAJOR, "term": _TERM}, ["course", "other_course"]),
    _tool("change_instructor", "تغییر استاد یک درس",
          {"course": _COURSE, "instructor": _s("نام استاد جدید"), "major": _MAJOR, "term": _TERM}, ["course", "instructor"]),
    _tool("change_room", "تغییر کلاس یک درس",
          {"course": _COURSE, "room": _s("نام کلاس جدید"), "major": _MAJOR, "term": _TERM, "session_no": _SESSION_NO},
          ["course", "room"]),
    _tool("set_lock", "قفل یا آزادکردن جلسه‌ی یک درس، یا همه‌ی جلسه‌های یک رشته/ترم",
          {"locked": {"type": "boolean", "description": "true = قفل، false = آزاد"},
           "course": _s("نام درس؛ اگر خالی باشد کل رشته/ترم"), "major": _MAJOR, "term": _TERM}, ["locked"]),
    _tool("show_schedule", "نمایش برنامه‌ی یک رشته/ترم، یک استاد یا یک کلاس",
          {"view": _s("major یا instructor یا room"), "name": _s("نام استاد یا کلاس در نماهای مربوط"),
           "major": _MAJOR, "term": _TERM}),
    _tool("instructor_info", "اطلاعات اساتید: یک استاد مشخص یا اساتید یک رشته",
          {"instructor": _s("نام استاد؛ برای لیست رشته خالی"), "major": _MAJOR}),
    _tool("course_info", "لیست دروس چارت یک رشته (و ترم)", {"major": _MAJOR, "term": _TERM}),
    _tool("list_departments", "ساختار دانشکده‌ها و رشته‌ها", {}),
    _tool("find_free_slots", "زمان‌های خالی مجاز برای جابه‌جایی یک درس",
          {"course": _COURSE, "major": _MAJOR, "term": _TERM, "session_no": _SESSION_NO}, ["course"]),
    _tool("quality_report", "گزارش کیفیت برنامه، درس‌های چیده‌نشده و تداخل‌ها", {"major": _MAJOR, "term": _TERM}),
    _tool("undo", "بازگرداندن آخرین تغییر برنامه", {}),
]}

HANDLERS: Dict[str, Callable[[Ctx, dict], Out]] = {
    "schedule_term": schedule_term, "move_session": move_session, "swap_sessions": swap_sessions,
    "change_instructor": change_instructor, "change_room": change_room, "set_lock": set_lock,
    "show_schedule": show_schedule, "instructor_info": instructor_info, "course_info": course_info,
    "list_departments": list_departments, "find_free_slots": find_free_slots,
    "quality_report": quality_report, "undo": undo,
}
