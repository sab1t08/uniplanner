"""نگاشت متن آزاد فارسی به شناسه‌ی رشته/استاد/درس/کلاس (دقیق ← پیشوند کلمه ← تقریبی)."""
import difflib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from db import repo
from db.schema import normalize_fa

TITLES = {"دکتر", "استاد", "مهندس", "خانم", "آقای", "جناب"}


@dataclass
class Resolved:
    status: str                                   # ok | ambiguous | none
    matches: List[Tuple[object, str]] = field(default_factory=list)   # (id, label)

    @property
    def id(self):
        return self.matches[0][0] if self.matches else None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def labels(self) -> str:
        return "، ".join(l for _, l in self.matches)


def _clean(text: str, strip_titles: bool = False) -> str:
    words = normalize_fa(text).split()
    if strip_titles:
        words = [w for w in words if w not in TITLES]
    return " ".join(words)


def _word_prefix(t: str, form: str) -> bool:
    return f" {t}" in f" {form} "


def resolve(text: str, items: Sequence[Tuple[object, str, Iterable[str]]], strip_titles: bool = False,
            prefer: Optional[set] = None) -> Resolved:
    """items: (id, label, forms). prefer: شناسه‌هایی که در صورت ابهام ارجحند (مثلاً دروس رشته‌ی فعال)."""
    t = _clean(text or "", strip_titles)
    if not t:
        return Resolved("none")
    forms = [(i, lab, [_clean(f, strip_titles) for f in fs]) for i, lab, fs in items]
    label_of = {i: lab for i, lab, _ in forms}

    def pack(ids):
        ids = list(dict.fromkeys(ids))
        if prefer and len(ids) > 1:
            pref = [i for i in ids if i in prefer]
            if len(pref) == 1:
                ids = pref
        return Resolved("ok" if len(ids) == 1 else "ambiguous", [(i, label_of[i]) for i in ids])

    full = [i for i, lab, fs in forms if fs and fs[0] == t]          # فرم اول = نام کامل
    if len(full) == 1:
        return pack(full)
    exact = [i for i, _, fs in forms if t in fs]
    contains = [i for i, _, fs in forms if any(_word_prefix(t, f) for f in fs)]
    if exact:
        # نام مستعار دقیق، اما اگر در نام رشته‌های دیگر هم آمده، مبهم است
        return pack(list(dict.fromkeys(exact + contains)))
    if contains:
        return pack(contains)
    inside = [(len(f), i) for i, _, fs in forms for f in fs if len(f) >= 3 and _word_prefix(f, t)]
    if inside:
        best = max(n for n, _ in inside)
        return pack([i for n, i in inside if n == best])
    pool = {f: i for i, _, fs in forms for f in fs}
    close = difflib.get_close_matches(t, list(pool), n=3, cutoff=0.72)
    if close:
        return pack([pool[c] for c in close])
    return Resolved("none")


# ───────────── نگاشت‌های مشخص ─────────────

def resolve_major(text: str) -> Resolved:
    aliases: Dict[int, List[str]] = {}
    for a in repo.list_major_aliases():
        aliases.setdefault(a["major_id"], []).append(a["alias"])
    items = [(m["id"], m["name"], [m["name"], *aliases.get(m["id"], [])]) for m in repo.list_majors()]
    return resolve(text, items)


def resolve_instructor(text: str) -> Resolved:
    return resolve(text, [(i["id"], i["name"], [i["name"]]) for i in repo.list_instructors()], strip_titles=True)


def resolve_course(text: str, among_codes: Optional[set] = None, prefer: Optional[set] = None) -> Resolved:
    cs = [c for c in repo.list_courses() if among_codes is None or c["code"] in among_codes]
    return resolve(text, [(c["code"], c["name"], [c["name"], c["code"]]) for c in cs], prefer=prefer)


def resolve_room(text: str) -> Resolved:
    return resolve(text, [(r["id"], r["room_code"], [r["room_code"]]) for r in repo.list_rooms()])
