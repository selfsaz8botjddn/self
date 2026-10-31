# -*- coding: utf-8 -*-
"""
«حالت متن» سلف: فونت‌ها + افکت تایپ حرف‌به‌حرف.

- FONTS: نام فونت -> مشخصات. ساده / بولد / ایتالیک / مونو همان نام‌های قبلی‌اند (تنظیم کاربرهای قبلی سالم می‌مونه).
- render(text, mode) -> (متن استایل‌شده، تگ تلگرام یا None)
- apply(...): متن پیام رو ادیت می‌کنه (با افکت تایپ یا بدون اون)
- menu(...): صفحهٔ دکمه‌های «حالت متن»
"""
import asyncio
import html
import logging
import re

logger = logging.getLogger(__name__)

_UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LO = "abcdefghijklmnopqrstuvwxyz"
_DG = "0123456789"
_PERSIAN = re.compile(r"[\u0600-\u06FF]")


def _tbl(up, lo, dg=""):
    """جدول تبدیل حروف لاتین (و اعداد). اگه طول‌ها نخونه None برمی‌گردونه تا ربات بالا بیاد."""
    src = _UP + _LO + (_DG if dg else "")
    dst = up + lo + dg
    if len(src) != len(dst):
        logger.warning("selffonts: bad table (%d != %d)", len(src), len(dst))
        return None
    return str.maketrans(dict(zip(src, dst)))


def _math(up0, lo0, dg0=None, holes_up=None, holes_lo=None):
    up = [chr(up0 + i) for i in range(26)]
    lo = [chr(lo0 + i) for i in range(26)]
    for k, v in (holes_up or {}).items():
        up[ord(k) - 65] = v
    for k, v in (holes_lo or {}).items():
        lo[ord(k) - 97] = v
    dg = "".join(chr(dg0 + i) for i in range(10)) if dg0 else ""
    return _tbl("".join(up), "".join(lo), dg)


def _seq(start, n, step=1):
    return "".join(chr(start + i * step) for i in range(n))


# ---- جدول‌های لاتین ----
_T = {
    # همان سه فونت قبلی (بدون تغییر ظاهر)
    "بولد": _tbl("𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭", "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"),
    "ایتالیک": _tbl("𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡", "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻"),
    "مونو": _tbl("𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉", "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣"),
    # فونت‌های جدید
    "سریف بولد": _math(0x1D400, 0x1D41A, 0x1D7CE),
    "سریف ایتالیک": _math(0x1D434, 0x1D44E, None, None, {"h": "\u210E"}),
    "بولد ایتالیک": _math(0x1D468, 0x1D482),
    "اسکریپت": _math(0x1D4D0, 0x1D4EA),
    "گوتیک": _math(0x1D56C, 0x1D586),
    "توخالی": _math(0x1D538, 0x1D552, 0x1D7D8,
                    {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}),
    "پهن": _tbl(_seq(0xFF21, 26), _seq(0xFF41, 26), _seq(0xFF10, 10)),
    "دایره‌ای": _tbl(_seq(0x24B6, 26), _seq(0x24D0, 26), "⓪" + _seq(0x2460, 9)),
    "مربعی": _tbl(_seq(0x1F130, 26), _seq(0x1F130, 26)),
    "مربع سیاه": _tbl(_seq(0x1F170, 26), _seq(0x1F170, 26)),
    "کوچک‌کپ": _tbl("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"),
}


# ---- افکت‌هایی که روی فارسی هم کار می‌کنن ----
_NOJOIN = set("ادذرزژوآأإؤءةۀ")
_ARLET = re.compile(r"[\u0621-\u064A\u0671-\u06D3\u06FA-\u06FF]")


def _kashida(t):
    out = []
    for i, ch in enumerate(t):
        out.append(ch)
        if i + 1 < len(t):
            nx = t[i + 1]
            if _ARLET.match(ch) and ch not in _NOJOIN and _ARLET.match(nx) and nx != "ء":
                out.append("ـ")
    return "".join(out)


def _spaced(t):
    # بین هر دو نویسهٔ غیرفاصله یه فاصله می‌ذاره
    return re.sub(r"(?<=\S)(?=\S)", " ", t)


def _dotted(t):
    return re.sub(r"(?<=\S)(?=\S)", "·", t)


# نام -> ("map", جدول، تگ فارسی) | ("tag", تگ) | ("fn", تابع)
FONTS = {"ساده": None}
for _n, _tag in (("بولد", "b"), ("ایتالیک", "i"), ("مونو", "code"), ("سریف بولد", "b"),
                 ("سریف ایتالیک", "i"), ("بولد ایتالیک", "bi"), ("اسکریپت", None), ("گوتیک", None),
                 ("توخالی", None), ("پهن", None), ("دایره‌ای", None), ("مربعی", None),
                 ("مربع سیاه", None), ("کوچک‌کپ", None)):
    if _T.get(_n) is not None:
        FONTS[_n] = ("map", _T[_n], _tag)
FONTS["زیرخط"] = ("tag", "u")
FONTS["خط‌خورده"] = ("tag", "s")
FONTS["اسپویلر"] = ("tag", "spoiler")
FONTS["نقل‌قول"] = ("tag", "quote")
FONTS["کشیده"] = ("fn", _kashida)
FONTS["فاصله‌دار"] = ("fn", _spaced)
FONTS["نقطه‌دار"] = ("fn", _dotted)

_HTML_TAGS = {"b": ("b",), "i": ("i",), "bi": ("b", "i"), "code": ("code",), "u": ("u",), "s": ("s",),
              "spoiler": ("tg-spoiler",), "quote": ("blockquote",)}


def render(text, mode):
    """(متن استایل‌شده، تگ). فونت ناشناخته/ساده: خود متن."""
    spec = FONTS.get(mode)
    if not spec:
        return text, None
    kind = spec[0]
    if kind == "map":
        tag = spec[2] if spec[2] and _PERSIAN.search(text) else None
        return text.translate(spec[1]), tag
    if kind == "tag":
        return text, spec[1]
    return spec[1](text), None


def to_html(text, tag):
    safe = html.escape(text, quote=False)
    for t in _HTML_TAGS.get(tag, ()):
        safe = f"<{t}>{safe}</{t}>"
    return safe


# ---------- ادیت پیام ----------
TYPE_MAX_CHARS = 300          # پیام بلندتر از این بدون انیمیشن (فقط فونت)
TYPE_MAX_STEPS = 12           # حداکثر تعداد ادیت برای یک پیام
SPEEDS = {1: ("کند", 0.9), 2: ("متوسط", 0.6), 3: ("سریع", 0.35)}
_locks = {}                   # uid -> Lock (انیمیشن‌ها پشت‌سرهم)
_waiting = {}                 # uid -> تعداد انیمیشن در صف


def _is_flood(exc):
    return "FloodWait" in type(exc).__name__ or "flood" in str(exc).lower()


async def _edit(ev, text, tag, cursor=False):
    if tag is None and not cursor:
        await ev.edit(text, parse_mode=None)
        return
    body = to_html(text, tag) + ("▌" if cursor else "")
    await ev.edit(body, parse_mode="html")


async def _final(ev, styled, tag):
    try:
        await _edit(ev, styled, tag)
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e).lower() or "NotModified" in type(e).__name__:
            return
        if tag is not None:
            try:      # اگه قالب‌بندی رد شد، حداقل متن استایل‌شده بدون تگ
                await ev.edit(styled, parse_mode=None)
            except Exception as e2:  # noqa: BLE001
                logger.debug("font final edit failed: %s", e2)
        else:
            logger.debug("font final edit failed: %s", e)


async def apply(uid, ev, original, mode, typing=False, speed=2):
    """روی پیام خودِ کاربر فونت رو اعمال می‌کنه؛ typing=True یعنی حرف‌به‌حرف."""
    styled, tag = render(original, mode)
    changed = tag is not None or styled != original
    if not typing or len(original) < 3 or len(original) > TYPE_MAX_CHARS:
        if changed:
            await _final(ev, styled, tag)
        return
    if _waiting.get(uid, 0) >= 2:      # صف شلوغه؛ بدون انیمیشن (محافظت از اکانت)
        if changed:
            await _final(ev, styled, tag)
        return
    delay = SPEEDS.get(speed, SPEEDS[2])[1]
    lk = _locks.setdefault(uid, asyncio.Lock())
    _waiting[uid] = _waiting.get(uid, 0) + 1
    try:
        async with lk:
            n = len(original)
            step = 1 if n <= TYPE_MAX_STEPS else -(-n // TYPE_MAX_STEPS)
            for k in range(step, n, step):
                shown, t = render(original[:k], mode)
                if not shown.strip():
                    continue
                try:
                    await _edit(ev, shown, t, cursor=True)
                except Exception as e:  # noqa: BLE001
                    if "not modified" in str(e).lower() or "NotModified" in type(e).__name__:
                        continue
                    if _is_flood(e) or "MessageIdInvalid" in type(e).__name__:
                        break           # فلود یا پیام پاک شده: انیمیشن رو ول کن
                    logger.debug("typewriter step: %s", e)
                    break
                await asyncio.sleep(delay)
            await _final(ev, styled, tag)
    finally:
        _waiting[uid] = max(0, _waiting.get(uid, 1) - 1)


# ---------- منوی دکمه‌ها ----------
PAGE_SIZE = 12


def names():
    return list(FONTS)


def label(d):
    """متن دکمهٔ «حالت متن» توی پنل."""
    s = "حالت متن: " + d.get("font_mode", "ساده")
    return s + (" + تایپ" if d.get("type_fx") else "")


def menu(d, page, B, KB):
    """(متن، کیبورد) صفحهٔ فونت‌ها. B و KB از selfbot داده می‌شن."""
    allf = names()
    pages = max(1, -(-len(allf) // PAGE_SIZE))
    page = min(max(int(page), 0), pages - 1)
    cur = d.get("font_mode", "ساده")
    if cur not in FONTS:
        cur = "ساده"
    fx = bool(d.get("type_fx"))
    spd = d.get("type_speed", 2)
    spd = spd if spd in SPEEDS else 2
    part = allf[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    lines = ["<b>حالت متن</b>", "فونت پیام‌های بعدی‌ات رو انتخاب کن. دستورها (با نقطه) دست نمی‌خورن.", ""]
    for n in part:
        s, t = render("Abc سلام", n)
        lines.append(("» " if n == cur else "") + to_html(s, t) + " — " + n)
    rows = [[B("تایپ حرف‌به‌حرف: " + ("روشن" if fx else "خاموش"), callback_data=f"self:tfx:{page}",
               style="success" if fx else "primary"),
             B("سرعت: " + SPEEDS[spd][0], callback_data=f"self:tspd:{page}", style="primary")]]
    btns = [B(("» " if n == cur else "") + n, callback_data=f"self:font:{page}:{n}",
              style="success" if n == cur else "primary") for n in part]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    nav = []
    if page > 0:
        nav.append(B("صفحهٔ قبل", callback_data=f"self:fonts:{page - 1}", style="primary"))
    if page < pages - 1:
        nav.append(B("صفحهٔ بعد", callback_data=f"self:fonts:{page + 1}", style="primary"))
    if nav:
        rows.append(nav)
    rows.append([B("بازگشت", callback_data="self:home", style="primary")])
    if fx:
        lines += ["", "تایپ حرف‌به‌حرف: پیام‌های تا " + str(TYPE_MAX_CHARS) + " حرف با افکت تایپ ادیت می‌شن."]
    return "\n".join(lines), KB(rows)
