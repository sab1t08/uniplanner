"""رابط کاربری Streamlit؛ فقط نمایش و فراخوانی لایه‌های service/agent (بدون منطق دامنه)."""
import streamlit as st

import config
from agent import formatters as fmt
from agent import router, tools
from agent.llm import make_client
from db import repo
from db.schema import init_all
from db.validate import validate_base_data
from solver import reports, service
from ui import render

st.set_page_config(page_title="سامانه‌ی زمان‌بندی دانشگاهی", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
html, body, button, input, textarea, select { font-family: Vazirmatn, Vazir, Tahoma, "Segoe UI", sans-serif; }
[data-testid="stAppViewContainer"], [data-testid="stSidebar"] { direction: rtl; }
.stMarkdown, .stChatMessage, label, [data-testid="stMetric"], [data-testid="stAlert"] { text-align: right; direction: rtl; }
[data-testid="stSlider"], [data-testid="stDataFrame"] { direction: ltr; }
</style>
""", unsafe_allow_html=True)

S = st.session_state
VIEWS = {"رشته/ترم": "major", "استاد": "instructor", "کلاس": "room"}
GREETING = "سلام! بگویید چه کاری انجام دهم؛ مثلاً «برنامه‌ی ترم ۳ مهندسی کامپیوتر را بچین» یا «درس ریاضی ۱ را بیاور چهارشنبه ساعت ۹:۳۰»."


@st.cache_resource
def _init_db() -> bool:
    init_all()
    return True


@st.cache_resource
def _client():
    try:
        return make_client()
    except Exception:
        return None


def _apply_focus(ctx: tools.Ctx, names: dict, majors: dict) -> None:
    f, ctx.focus = ctx.focus, {}
    if ctx.major_id in majors:
        S["sel_major"] = majors[ctx.major_id]
    if ctx.term:
        S["sel_term"] = ctx.term
    inv = {v: k for k, v in VIEWS.items()}
    if f.get("view") in inv:
        S["sel_view"] = inv[f["view"]]
    if f.get("instructor_id"):
        S["sel_inst"] = names["inst"].get(f["instructor_id"])
    if f.get("room_id"):
        S["sel_room"] = names["room"].get(f["room_id"])


def _process_chat(text: str, names: dict, majors: dict) -> None:
    ctx: tools.Ctx = S.ctx
    ctx.semester = S.get("sem", config.DEFAULT_SEMESTER)
    S.chat.append({"role": "user", "content": text})
    # «بله/لغو» برای تأیید نیازی به مدل ندارد
    needs_llm = not (ctx.pending and router._norm_word(text) in router.CONFIRM | router.CANCEL)
    client = _client() if needs_llm else None
    if needs_llm and client is None:
        S.chat.append({"role": "assistant", "content": "کتابخانه‌ی ollama نصب نیست (pip install ollama)."})
        return
    with st.spinner("در حال پردازش..."):
        reply = router.handle(client, text, ctx)
    S.chat.append({"role": "assistant", "content": reply.text})
    if reply.error:
        S.notice = ("error", reply.error)
    _apply_focus(ctx, names, majors)


def main() -> None:
    _init_db()
    S.setdefault("ctx", tools.Ctx())
    S.setdefault("chat", [{"role": "assistant", "content": GREETING}])
    S.setdefault("notice", None)

    major_rows = repo.list_majors()
    majors = {m["id"]: m["name"] for m in major_rows}
    by_name = {v: k for k, v in majors.items()}
    names = {"inst": {i["id"]: i["name"] for i in repo.list_instructors()},
             "room": {r["id"]: r["room_code"] for r in repo.list_rooms()}}
    S.setdefault("sel_major", major_rows[0]["name"])
    S.setdefault("sel_term", 1)
    S.setdefault("sel_view", "رشته/ترم")

    queued = S.pop("queued", None)
    prompt = st.chat_input("دستور یا سؤال آموزشی را بنویسید...") or queued
    if prompt:
        _process_chat(prompt, names, majors)

    # ───── سایدبار ─────
    with st.sidebar:
        st.title("مدیریت آموزش")
        sem = st.selectbox("نیمسال تحصیلی", config.SEMESTERS, key="sem")
        major_name = st.selectbox("رشته", list(by_name), key="sel_major")
        term = st.slider("ترم", 1, 8, key="sel_term")
        limit = st.number_input("مهلت سولور (ثانیه)", 5, 600, int(config.SOLVER_TIME_LIMIT), step=5)
        mid = by_name[major_name]
        ctx: tools.Ctx = S.ctx
        ctx.semester, ctx.major_id, ctx.term = sem, mid, term

        def run(major_ids, terms, label):
            with st.spinner("در حال چیدمان..."):
                try:
                    res = service.schedule(sem, major_ids, terms, float(limit), label)
                    S.notice = ("success", fmt.schedule_result(res, label or "کل دانشگاه", majors))
                except Exception as e:
                    S.notice = ("error", f"{type(e).__name__}: {e}")

        if st.button("چیدمان این رشته/ترم", use_container_width=True, type="primary"):
            run([mid], [term], f"{major_name} ترم {term}")
        if st.button("چیدمان کل نیمسال", use_container_width=True):
            run(None, None, "کل دانشگاه")
        depth = repo.undo_depth(sem)
        if st.button(f"بازگردانی آخرین تغییر ({depth})", use_container_width=True, disabled=depth == 0):
            S.notice = ("info", service.undo(sem).message)
        st.divider()
        if st.button("آماده‌سازی خروجی Excel", use_container_width=True):
            S.xlsx = render.export_xlsx(sem)
        if S.get("xlsx"):
            st.download_button("دانلود Excel", S.xlsx, f"timetable_{sem}.xlsx", use_container_width=True)
        st.download_button("دانلود CSV", render.export_csv(sem), f"timetable_{sem}.csv", use_container_width=True)

    # ───── اعلان‌ها و شاخص‌ها ─────
    if S.notice:
        level, text = S.notice
        {"success": st.success, "error": st.error, "info": st.info}.get(level, st.info)(text)
        S.notice = None
    k = repo.kpis(sem, mid, term)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("رشته و ترم", f"{major_name} / {term}")
    c2.metric("جلسه‌های چیده‌شده", k["scheduled_classes"])
    c3.metric("اساتید فعال", k["active_professors"])
    c4.metric("جلسه‌های قفل‌شده", k["total_locked"])

    left, right = st.columns([2.3, 1], gap="large")

    with left:
        t_grid, t_edit, t_quality, t_data = st.tabs(["برنامه هفتگی", "ویرایش جلسه", "گزارش کیفیت", "کنترل داده‌ها"])

        with t_grid:
            view_label = st.radio("نما", list(VIEWS), horizontal=True, key="sel_view")
            view = VIEWS[view_label]
            if view == "instructor":
                nm = st.selectbox("استاد", list(names["inst"].values()), key="sel_inst")
                iid = next(k_ for k_, v in names["inst"].items() if v == nm)
                sessions = repo.list_sessions(sem, instructor_id=iid)
            elif view == "room":
                nm = st.selectbox("کلاس", list(names["room"].values()), key="sel_room")
                rid = next(k_ for k_, v in names["room"].items() if v == nm)
                sessions = repo.list_sessions(sem, classroom_id=rid)
            else:
                sessions = repo.list_sessions(sem, mid, term)
            st.markdown(render.grid_html(sessions, view), unsafe_allow_html=True)
            miss = service.unscheduled_report(sem, [mid], [term])
            if miss:
                with st.expander(f"درس‌های چیده‌نشده ({len(miss)})", expanded=True):
                    st.dataframe([{"درس": u["name"], "جلسه‌ی ناقص": u["missing"], "دلیل": u["reason"]} for u in miss],
                                 use_container_width=True, hide_index=True)

        with t_edit:
            secs = fmt.merge_sections(repo.list_sessions(sem, mid, term), majors)
            secs.sort(key=lambda s: (s["course_name"], s["session_no"]))
            if not secs:
                st.info("برای این رشته/ترم برنامه‌ای نیست. ابتدا چیدمان را اجرا کنید.")
            else:
                label = lambda s: f"{s['course_name']} - جلسه {s['session_no']} ({s['day_of_week']} {s['time_slot']})" + \
                                  (" [قفل]" if s["is_locked"] else "")
                idx = st.selectbox("جلسه", range(len(secs)), format_func=lambda i: label(secs[i]), key="edit_idx")
                cur = secs[idx]
                options = service.free_slots(cur["id"], limit=12)
                if options:
                    st.caption("زمان‌های خالی و مجاز (به ترتیب ارجحیت):")
                    pick = st.selectbox("زمان جدید", range(len(options)), key="edit_slot",
                                        format_func=lambda i: f"{options[i]['day']} {options[i]['slot']} - {options[i]['room_code']}")
                else:
                    st.warning("زمان خالی مجازی برای این جلسه وجود ندارد.")
                    pick = None

                def cb_move():
                    if pick is not None:
                        o = options[pick]
                        r = service.move_session(cur["id"], o["day"], o["slot"])
                        S.notice = ("success" if r.ok else "error", r.message)

                def cb_lock():
                    S.notice = ("info", service.set_lock([x["id"] for x in repo.list_sessions(sem, mid, term)
                                                          if x["course_code"] == cur["course_code"]],
                                                         not cur["is_locked"]).message)

                b1, b2 = st.columns(2)
                b1.button("انتقال به زمان انتخابی", on_click=cb_move, disabled=pick is None, use_container_width=True)
                b2.button("آزادکردن قفل" if cur["is_locked"] else "قفل‌کردن درس", on_click=cb_lock, use_container_width=True)

        with t_quality:
            scope = st.radio("محدوده", ["رشته/ترم انتخابی", "کل نیمسال"], horizontal=True)
            q = reports.quality_report(sem, [mid], [term]) if scope.startswith("رشته") else reports.quality_report(sem)
            m1, m2, m3 = st.columns(3)
            m1.metric("نمره‌ی کیفیت", f"{q['score']}")
            m2.metric("پوشش", f"{q['coverage']:.0%}", f"{q['scheduled']} از {q['expected']} جلسه", delta_color="off")
            m3.metric("اضافه‌بار روزانه", q["overload_sessions"])
            for typ, v in q["room_utilization"].items():
                st.progress(min(1.0, v), text=f"استفاده از فضای {fmt.ROOM_FA.get(typ, typ)}: {v:.0%}")
            if q["violations"]:
                st.error("تداخل/نقض قید:\n\n" + "\n".join(f"- {x}" for x in q["violations"][:15]))
            else:
                st.success("هیچ تداخل یا نقض قیدی وجود ندارد.")
            if q["unscheduled"]:
                st.subheader("درس‌های چیده‌نشده")
                st.dataframe([{"رشته": u["major"], "ترم": u["term"], "درس": u["name"], "دلیل": u["reason"]}
                              for u in q["unscheduled"]], use_container_width=True, hide_index=True)
            st.subheader("بار اساتید")
            st.dataframe([{"استاد": d["name"], "واحد": d["units"], "سقف": d["max_units"]} for d in q["instructor_load"]],
                         use_container_width=True, hide_index=True)

        with t_data:
            st.caption("این بررسی روی داده‌های پایه انجام می‌شود و مشکلاتی را نشان می‌دهد که هیچ سولوری حل نمی‌کند.")
            rep = validate_base_data()
            for level, fn in (("error", st.error), ("warning", st.warning), ("info", st.info)):
                for msg in rep[level]:
                    fn(msg)

    with right:
        st.subheader("دستیار آموزش")
        box = st.container(height=520)
        with box:
            for m in S.chat:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
        if S.ctx.pending:
            a, b = st.columns(2)
            a.button("تأیید", use_container_width=True, type="primary", on_click=lambda: S.__setitem__("queued", "بله"))
            b.button("انصراف", use_container_width=True, on_click=lambda: S.__setitem__("queued", "لغو"))
        q1, q2 = st.columns(2)
        q1.button("گزارش کیفیت", use_container_width=True, on_click=lambda: S.__setitem__("queued", "گزارش کیفیت برنامه را بده"))
        q2.button("بازگردانی", use_container_width=True, on_click=lambda: S.__setitem__("queued", "آخرین تغییر را برگردان"))


try:
    main()
except Exception as ex:   # خطای خام پایتون نباید صفحه را بشکند
    st.error(f"خطای غیرمنتظره: {type(ex).__name__}: {ex}")
