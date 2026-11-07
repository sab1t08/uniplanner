"""
مسیر پردازش پیام: مدل فقط «ابزار و آرگومان» را انتخاب می‌کند؛ اعتبارسنجی، اجرا و قالب‌بندی با کد قطعی است.
"""
import json
import re
from dataclasses import dataclass
from typing import List, Optional

import config
from agent import tools
from agent.tools import Ask, Ctx, Out
from db import repo
from db.schema import normalize_fa

CONFIRM = {"بله", "آره", "اره", "تایید", "تأیید", "اوکی", "ok", "باشه", "انجام بده", "بزن", "ادامه بده", "ادامه"}
CANCEL = {"نه", "خیر", "لغو", "انصراف", "منصرف شدم", "نکن", "بیخیال"}
MAX_CALLS = 3


@dataclass
class Reply:
    text: str
    refresh: bool = False
    pending: bool = False
    error: Optional[str] = None


def _system_prompt(ctx: Ctx) -> str:
    majors = {m["id"]: m["name"] for m in repo.list_majors()}
    cur = majors.get(ctx.major_id, "—")
    return (
        "تو دستیار سامانه‌ی زمان‌بندی دانشگاه هستی. هر درخواست کاربر را با فراخوانی دقیقاً یک ابزار مناسب انجام بده. "
        "نام‌ها (رشته، درس، استاد، کلاس) را همان‌طور که کاربر گفته در آرگومان‌ها بگذار؛ شناسه نساز. "
        "اگر اطلاعات ضروری در پیام نیست، ابزار را صدا نزن و فقط یک سؤال کوتاه بپرس. "
        "نتیجه یا داده‌ای را از خودت نساز. برای سلام، تشکر یا سؤال عمومی، کوتاه و فارسی پاسخ بده.\n"
        f"نیمسال جاری: {ctx.semester}. رشته‌ی فعال: {cur}. ترم فعال: {ctx.term or '—'}.\n"
        f"روزها: {'، '.join(config.DAYS)}. ساعت‌ها: {'، '.join(config.TIME_SLOTS)}."
    )


def _tool_schemas() -> List[dict]:
    return [{"type": "function", "function": t} for t in tools.TOOLS.values()]


def _coerce(schema: dict, args: dict) -> dict:
    """حذف آرگومان‌های ناشناخته، تبدیل نوع‌ها، و بررسی آرگومان‌های ضروری."""
    props = schema["parameters"]["properties"]
    out = {}
    for k, v in (args or {}).items():
        if k not in props or v in (None, "", "null"):
            continue
        typ = props[k]["type"]
        try:
            if typ == "integer":
                digits = re.sub(r"\D", "", normalize_fa(str(v)))
                v = int(digits) if digits else v
            elif typ == "boolean" and not isinstance(v, bool):
                v = str(v).strip().lower() in ("true", "1", "بله", "قفل")
            elif typ == "string":
                v = str(v)
        except ValueError:
            raise Ask(f"مقدار «{v}» برای {k} نامعتبر است.")
        out[k] = v
    missing = [r for r in schema["parameters"]["required"] if r not in out]
    if missing:
        labels = {"course": "نام درس", "day": "روز", "time_slot": "ساعت", "other_course": "نام درس دوم",
                  "instructor": "نام استاد", "room": "نام کلاس", "locked": "قفل یا آزاد"}
        raise Ask("برای این کار لازم است بگویید: " + "، ".join(labels.get(m, m) for m in missing) + ".")
    return out


def _calls_from_message(msg: dict) -> List[dict]:
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c["function"] if isinstance(c, dict) else c.function
        name = fn["name"] if isinstance(fn, dict) else fn.name
        args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        calls.append({"name": name, "arguments": dict(args or {})})
    if calls:
        return calls
    content = (msg.get("content") or "").strip()      # گاهی مدل فراخوانی را به‌صورت JSON متنی می‌نویسد
    m = re.search(r"\{.*\}", content, re.S)
    if m:
        try:
            d = json.loads(m.group())
            if isinstance(d, dict) and d.get("name") in tools.TOOLS:
                return [{"name": d["name"], "arguments": d.get("arguments") or d.get("parameters") or {}}]
        except ValueError:
            pass
    return []


def _norm_word(text: str) -> str:
    return re.sub(r"[\s.!؟?،]+$", "", text.strip().lower())


def confirm(ctx: Ctx) -> Reply:
    if not ctx.pending:
        return Reply("چیزی برای تأیید وجود ندارد.")
    out = tools.execute_pending(ctx)
    return Reply(out.text, refresh=out.refresh)


def cancel(ctx: Ctx) -> Reply:
    ctx.pending = None
    return Reply("انجام نشد.")


def handle(client, text: str, ctx: Ctx) -> Reply:
    word = _norm_word(text)
    if ctx.pending:
        if word in CONFIRM:
            return confirm(ctx)
        if word in CANCEL:
            return cancel(ctx)
        ctx.pending = None          # پیام جدید = انصراف ضمنی از درخواست قبلی

    messages = [{"role": "system", "content": _system_prompt(ctx)}, *ctx.history[-6:],
                {"role": "user", "content": text}]
    try:
        resp = client.chat(model=config.OLLAMA_MODEL, messages=messages, tools=_tool_schemas(),
                           options={"temperature": 0.0})
    except Exception as e:
        return Reply("ارتباط با مدل زبانی برقرار نشد. مطمئن شوید Ollama روشن و مدل نصب است.", error=str(e))

    msg = resp["message"] if isinstance(resp, dict) else resp.message
    msg = msg if isinstance(msg, dict) else {"content": msg.content, "tool_calls": msg.tool_calls}
    calls = _calls_from_message(msg)

    if not calls:
        reply = (msg.get("content") or "").strip() or "متوجه نشدم؛ لطفاً دقیق‌تر بگویید."
        _remember(ctx, text, reply)
        return Reply(reply)

    texts, refresh, pending = [], False, False
    for call in calls[:MAX_CALLS]:
        schema = tools.TOOLS.get(call["name"])
        if schema is None:
            texts.append("متوجه نشدم؛ لطفاً دقیق‌تر بگویید."); break
        try:
            out: Out = tools.HANDLERS[call["name"]](ctx, _coerce(schema, call["arguments"]))
        except Ask as q:
            texts.append(str(q)); break
        except Exception as e:      # خطای غیرمنتظره نباید برنامه را بکشد
            return Reply("در اجرای دستور خطایی رخ داد.", error=f"{type(e).__name__}: {e}")
        texts.append(out.text)
        refresh |= out.refresh
        pending = out.pending
        if pending:
            break
    reply = "\n\n".join(texts)
    _remember(ctx, text, reply)
    return Reply(reply, refresh=refresh, pending=pending)


def _remember(ctx: Ctx, user: str, assistant: str) -> None:
    ctx.history += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant[:400]}]
    ctx.history = ctx.history[-12:]
