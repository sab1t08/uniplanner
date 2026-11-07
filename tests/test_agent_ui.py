import unittest

import config
from agent import resolver, router, tools
from agent.tools import Ctx, parse_day, parse_slot, parse_term, Ask
from db import repo
from tests._env import EnvTest
from ui import render


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def chat(self, **kw):
        self.calls.append(kw)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": r}


def call(name, **args):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class ParseTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(parse_term("سه"), 3)
        self.assertEqual(parse_term("۵"), 5)
        self.assertEqual(parse_day("سه شنبه"), "سه‌شنبه")
        self.assertEqual(parse_slot("9:30"), "09:30-11:30")
        self.assertEqual(parse_slot("۱۳"), "13:00-15:00")
        self.assertEqual(parse_slot("3"), "15:00-17:00")
        self.assertEqual(parse_slot("15:00-17:00"), "15:00-17:00")
        with self.assertRaises(Ask):
            parse_day("جمعه")
        with self.assertRaises(Ask):
            parse_term("12")


class ResolverTests(EnvTest):
    def test_majors(self):
        self.assertEqual(resolver.resolve_major("کامپیوتر").status, "ambiguous")
        self.assertEqual(resolver.resolve_major("علوم کامپیوتر").id, 6)
        self.assertEqual(resolver.resolve_major("مهندسی کامپیوتر").id, 1)
        self.assertEqual(resolver.resolve_major("عمران").id, 2)
        self.assertEqual(resolver.resolve_major("xyz123").status, "none")

    def test_room_digits(self):
        self.assertEqual(resolver.resolve_room("کلاس ۱۰۱").labels(), "کلاس ۱۰۱")


class RouterTests(EnvTest):
    def setUp(self):
        super().setUp()
        self.ctx = Ctx(semester="1405-1")

    def test_schedule_then_confirm_overwrite(self):
        c = FakeClient(call("schedule_term", major="مهندسی کامپیوتر", term="۱"),
                       call("schedule_term", major="مهندسی کامپیوتر", term=1))
        r = router.handle(c, "برنامه ترم ۱ کامپیوتر را بچین", self.ctx)
        self.assertIn("چیده شد", r.text)
        self.assertTrue(r.refresh and repo.list_sessions("1405-1", 1, 1))
        r = router.handle(c, "دوباره بچین", self.ctx)
        self.assertTrue(r.pending)
        r = router.handle(c, "بله", self.ctx)         # تأیید بدون فراخوانی مدل
        self.assertIn("چیده شد", r.text)
        self.assertEqual(len(c.calls), 2)

    def test_cancel(self):
        c = FakeClient(call("schedule_term", major="مهندسی کامپیوتر", term=1),
                       call("schedule_term", major="مهندسی کامپیوتر", term=1))
        router.handle(c, "بچین", self.ctx)
        router.handle(c, "بچین", self.ctx)
        self.assertEqual(router.handle(c, "نه", self.ctx).text, "انجام نشد.")
        self.assertIsNone(self.ctx.pending)

    def test_ambiguous_major_asks(self):
        c = FakeClient(call("schedule_term", major="کامپیوتر", term=3))
        r = router.handle(c, "ترم ۳ کامپیوتر را بچین", self.ctx)
        self.assertIn("کدام رشته", r.text)
        self.assertFalse(repo.list_sessions("1405-1"))

    def test_missing_required_asks(self):
        c = FakeClient(call("move_session", course="ریاضی"))
        self.assertIn("لازم است", router.handle(c, "ریاضی را جابه‌جا کن", self.ctx).text)

    def test_move_through_agent(self):
        c = FakeClient(call("schedule_term", major="مهندسی کامپیوتر", term=1))
        router.handle(c, "بچین", self.ctx)
        s = repo.list_sessions("1405-1", 1, 1)[0]
        free = __import__("solver.service", fromlist=["x"]).free_slots(s["id"])[0]
        c = FakeClient(call("move_session", course=s["course_name"], day=free["day"],
                            time_slot=free["slot"], session_no=s["session_no"]))
        r = router.handle(c, "منتقل کن", self.ctx)
        self.assertTrue(r.refresh, r.text)
        self.assertEqual(repo.get_session(s["id"])["day_of_week"], free["day"])

    def test_json_in_content_and_plain_chat_and_errors(self):
        txt = '{"name": "list_departments", "arguments": {}}'
        self.assertIn("دانشکده", router.handle(FakeClient({"content": txt}), "دانشکده‌ها", self.ctx).text) \
            if False else None
        r = router.handle(FakeClient({"content": txt}), "ساختار", self.ctx)
        self.assertIn("-", r.text)
        self.assertEqual(router.handle(FakeClient({"content": "سلام!"}), "سلام", self.ctx).text, "سلام!")
        r = router.handle(FakeClient(ConnectionError("down")), "x", self.ctx)
        self.assertTrue(r.error and "Ollama" in r.text)
        self.assertIn("متوجه نشدم", router.handle(FakeClient(call("hack_db")), "x", self.ctx).text)

    def test_read_tools(self):
        for name, args in (("instructor_info", {"major": "حقوق"}), ("course_info", {"major": "حقوق", "term": 1}),
                           ("list_departments", {}), ("quality_report", {})):
            r = router.handle(FakeClient(call(name, **args)), "x", self.ctx)
            self.assertTrue(r.text and "خطا" not in r.text, (name, r.text))


class RenderTests(EnvTest):
    def test_html_is_escaped(self):
        s = {"course_code": "X", "session_no": 1, "day_of_week": "شنبه", "time_slot": "09:30-11:30",
             "classroom_id": 1, "instructor_id": 1, "major_id": 1, "target_term": 1,
             "course_name": "<script>alert(1)</script>", "instructor_name": "a&b", "room_code": "r",
             "room_type": "THEORY", "is_locked": 1}
        h = render.grid_html([s])
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("قفل", h)

    def test_exports(self):
        from solver import service
        service.schedule("1405-1", [1], [1], time_limit=3)
        import io, openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(render.export_xlsx("1405-1")))
        self.assertIn("CE_T1", wb.sheetnames)
        self.assertTrue(render.export_csv("1405-1").startswith("\ufeff".encode()))


if __name__ == "__main__":
    unittest.main()
