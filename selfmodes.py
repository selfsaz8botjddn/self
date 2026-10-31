# -*- coding: utf-8 -*-
"""میم توی پنل، حالت دوست و حالت دشمن (نسخهٔ ۲۸).

  دکمهٔ «میم»        → راهنمای دستورهای میم (متنش رو پشتیبانی از /support ← «دستورات میم» عوض می‌کنه)
  دکمهٔ «حالت دوست»  → روشن/خاموش
  دکمهٔ «حالت دشمن»  → روشن/خاموش
  .افزودن دوست       (ریپلای روی پیام کاربر) → از این به بعد، بعد از هر پیامی که اون کاربر توی همین گپ بده،
                       یکی از ۱۵ پیام زیر به‌صورت نوبتی (یکی‌یکی) ریپلای می‌شه
  .افزودن دشمن       همین‌طور، با پیام‌های تند
  .حذف دوست / .حذف دشمن (ریپلای)   |   .حذف دوست همه   |   .لیست دوست / .لیست دشمن

متن پیام‌ها رو می‌تونی همین‌جا (FRIEND_MSGS / ENEMY_MSGS) عوض کنی؛ هر تعداد پیام بذاری نوبتی می‌چرخه.
اطلاعات توی self_profile (کلید modes) ذخیره می‌شه؛ جدول جدیدی لازم نیست.
"""
import asyncio
import logging
import random
import time

import selfbot as sb

logger = logging.getLogger("selfmodes")

CB_ACTIONS = ("meme", "mode")

MAX_TARGETS = 10          # حداکثر نفر برای هر حالت
MIN_GAP = 3.0             # حداقل فاصلهٔ دو جواب به یه نفر (ثانیه)؛ پیام‌های پشت‌سرهم یه جواب می‌گیرن
GLOBAL_PER_MIN = 20       # سقف کل جواب‌ها در دقیقه برای هر اکانت (محافظت از اکانت)

FRIEND_MSGS = [
    "سلام عزیز دلم 🌹 دیدن پیامت حال منو خوب می‌کنه",
    "تو همون آدمی هستی که با اومدنش، روزم رنگ می‌گیره 💕",
    "هر بار که پیام می‌دی، دلم یه جوری گرم می‌شه 🥰",
    "می‌دونستی بودنت بزرگ‌ترین هدیه‌ایه که زندگی بهم داده؟ ❤️",
    "قربونت برم که همیشه هستی، دوستت دارم 😍",
    "اسمت که میاد، لبخند خودش میاد رو لبم 😊",
    "تو برای من فقط یه دوست نیستی، یه تکه از قلبمی 💖",
    "بدون تو دنیا این‌قدر قشنگ نبود، ممنون که هستی 🌷",
    "هر جا باشی، دلم پیش توئه عزیزم 💞",
    "با تو بودن آرومم می‌کنه؛ مراقب خودت باش، نفسم 🌙",
    "تو لایق بهترین‌هایی، و من خوشحالم که کنارتم ✨",
    "یادت باشه خیلی دوستت دارم، حتی وقتی نمی‌گم 💌",
    "حرف زدن با تو بهترین قسمت روزمه ☀️",
    "تو مثل یه آهنگ قشنگی که هیچ‌وقت ازش خسته نمی‌شم 🎶",
    "عاشق مهربونی و بودنتم، همیشه کنارم بمون 💘",
]

ENEMY_MSGS = [
    "برو بابا، حوصلهٔ آدم‌های بی‌شعور مثل تو رو ندارم 😒",
    "هر بار حرف می‌زنی ثابت می‌کنی مغزت تعطیله، احمق",
    "خفه شو دیگه عوضی، کی از تو نظر خواست؟",
    "گمشو اون‌ور ایکبیری، دیگه پیام نده",
    "تو واقعاً الاغی یا فقط تمرین می‌کنی؟ 🐴",
    "پست‌فطرت، هر چی می‌گی بوی گند می‌ده",
    "کثافت، برو یه چیز به‌درد‌بخور یاد بگیر",
    "اینقدر بی‌مصرفی که آدم از دیدن اسمت حالش بد می‌شه",
    "برو مرتیکه، تو حتی ارزش جواب‌دادن هم نداری",
    "پفیوز بی‌عرضه، فقط بلدی حرف مفت بزنی",
    "دهنتو ببند، صدات هم آزارم می‌ده",
    "نکبت، هر جا می‌ری حال همه رو می‌گیری",
    "آشغال بی‌سواد، دیگه جلوی من دم در نیار",
    "برو به درک که اینقدر مزاحمی",
    "تموم شد دیگه، گمشو و نبینمت، عوضی بی‌ارزش",
]

_MODE = {
    "friend": {"label": "حالت دوست", "emoji": "💕", "word": "دوست", "msgs": FRIEND_MSGS,
               "about": "بعد از هر پیامِ کاربرِ اضافه‌شده، یکی از پیام‌های عاشقانه به‌نوبت ریپلای می‌شه."},
    "enemy": {"label": "حالت دشمن", "emoji": "😈", "word": "دشمن", "msgs": ENEMY_MSGS,
              "about": "بعد از هر پیامِ کاربرِ اضافه‌شده، یکی از پیام‌های تند (با فحش) به‌نوبت ریپلای می‌شه."},
}
_WORD = {"دوست": "friend", "friend": "friend", "دشمن": "enemy", "enemy": "enemy"}

MEME_HELP_DEFAULT = (
    "😂 دستورهای میم\n\n"
    "• .میم متن بالا | متن پایین\n"
    "   روی عکس، ویدیو، گیف یا استیکر ریپلای کن\n"
    "• فقط متن بالا:  .میم سلام\n"
    "• فقط متن پایین:  .میم | سلام\n"
    "• .ویدیو میم بالا | پایین  (همون کار)\n\n"
    "عکس ← عکس | ویدیو و گیف ← ویدیو (تا ۶۰ ثانیه، با صدا)\n"
    "فارسی و انگلیسی پشتیبانی می‌شه."
)
MEME_HELP_MAX = 900
_GLOBAL_ID = 0            # ردیف مشترک self_profile برای تنظیمات کل ربات (راهنمای میم)

HELP = ("💕😈 حالت دوست / دشمن\n"
        "روی پیام کاربر ریپلای کن و بنویس:\n"
        ".افزودن دوست   یا   .افزودن دشمن\n"
        "حذف: .حذف دوست / .حذف دشمن (ریپلای)  |  .حذف دوست همه\n"
        "لیست: .لیست دوست / .لیست دشمن\n"
        "روشن/خاموش‌کردن: دکمه‌های «حالت دوست» و «حالت دشمن» توی پنل.")

_cache = {}               # uid -> state
_last = {}                # (uid, key) -> ts
_hist = {}                # uid -> [ts]


# ---------- راهنمای میم (قابل‌تنظیم توسط پشتیبانی) ----------
def meme_help():
    t = (sb._pget(_GLOBAL_ID).get("meme_help") or "").strip()
    return t or MEME_HELP_DEFAULT


def set_meme_help(text):
    """متن جدید رو ذخیره می‌کنه؛ None یا متن خالی = برگشت به پیش‌فرض."""
    d = sb._pget(_GLOBAL_ID)
    t = (text or "").strip()[:MEME_HELP_MAX]
    if t:
        d["meme_help"] = t
    else:
        d.pop("meme_help", None)
    sb._pput(_GLOBAL_ID, d)


# ---------- وضعیت ----------
def _fresh():
    return {"on": {"friend": False, "enemy": False}, "t": {"friend": {}, "enemy": {}}}


def _load(uid):
    st = _cache.get(uid)
    if st is None:
        st = _fresh()
        saved = sb._pget(uid).get("modes") or {}
        for m in ("friend", "enemy"):
            st["on"][m] = bool((saved.get("on") or {}).get(m))
            st["t"][m] = dict((saved.get("t") or {}).get(m) or {})
        _cache[uid] = st
    return st


def _save(uid, st):
    d = sb._pget(uid)            # دوباره بخون تا بقیهٔ کلیدهای پروفایل (کارت و ...) دست‌نخورده بمونن
    d["modes"] = st
    sb._pput(uid, d)


def cleanup(uid):
    _cache.pop(uid, None)
    _hist.pop(uid, None)
    for k in [k for k in _last if k[0] == uid]:
        _last.pop(k, None)


# ---------- دکمه‌های پنل ----------
def panel_rows(d):
    on = ((d or {}).get("modes") or {}).get("on") or {}

    def lab(m):
        return f"{_MODE[m]['emoji']} {_MODE[m]['label']}: " + ("روشن" if on.get(m) else "خاموش")

    return [
        [sb.B("😂 میم", callback_data="self:meme", style="primary")],
        [sb.B(lab("friend"), callback_data="self:mode:friend", style="success" if on.get("friend") else "primary"),
         sb.B(lab("enemy"), callback_data="self:mode:enemy", style="danger" if on.get("enemy") else "primary")],
    ]


def _names(st, m, limit=8):
    items = list(st["t"][m].values())
    names = [(" ".join(str(x.get("n") or "؟").split()))[:24] for x in items[:limit]]
    more = len(items) - len(names)
    return "، ".join(names) + (f" و {more} نفر دیگه" if more > 0 else "")


def _mode_panel(uid, m):
    st = _load(uid)
    meta = _MODE[m]
    on = st["on"][m]
    n = len(st["t"][m])
    lines = [f"{meta['emoji']} {meta['label']}: " + ("روشن ✅" if on else "خاموش ❌"), "",
             meta["about"], f"تعداد پیام‌های چرخشی: {len(meta['msgs'])}", "",
             f"افزودن: روی پیام کاربر ریپلای کن و بنویس  .افزودن {meta['word']}",
             f"حذف: .حذف {meta['word']}   |   همه: .حذف {meta['word']} همه   |   لیست: .لیست {meta['word']}",
             "فقط توی همون گپی که اضافه‌اش می‌کنی جواب می‌ده.", "",
             f"افراد اضافه‌شده: {n} از {MAX_TARGETS}" + (f"\n{_names(st, m)}" if n else "")]
    rows = [[sb.B("خاموش کردن" if on else "روشن کردن", callback_data=f"self:mode:{m}:t",
                  style="danger" if on else "success")]]
    if n:
        rows.append([sb.B("پاک‌کردن همهٔ افراد", callback_data=f"self:mode:{m}:c", style="danger")])
    rows.append([sb.B("بازگشت", callback_data="self:home", style="primary")])
    return "\n".join(lines), sb.KB(rows)


async def handle_cb(uid, a, p, qm, context):
    if a == "meme":
        await qm.edit_text(meme_help()[:MEME_HELP_MAX],
                           reply_markup=sb.KB([[sb.B("بازگشت", callback_data="self:home", style="primary")]]))
        return
    m = p[2] if len(p) > 2 else ""
    if m not in _MODE:
        return
    sub = p[3] if len(p) > 3 else ""
    st = _load(uid)
    if sub == "c":
        st["t"][m] = {}
    else:                                   # دکمهٔ اصلی پنل و دکمهٔ داخلی هر دو روشن/خاموش می‌کنن
        st["on"][m] = not st["on"][m]
    _save(uid, st)
    t, kb = _mode_panel(uid, m)
    await qm.edit_text(t, reply_markup=kb)


# ---------- دستورها ----------
def route(cmd, arg):
    """(کار، حالت) یا None. «.حذف دوست» اینجا قبل از «.حذف» معمولی (حذف پیام) گرفته می‌شه."""
    w = sb.norm(arg).split()
    mode = _WORD.get(w[0]) if w else None
    if cmd == "addmode":
        if mode and len(w) == 1:
            return "add", mode
        return ("help", None) if not w else None        # «.افزودن چیز دیگه» دست‌نخورده می‌مونه
    if cmd == "listmode":
        return ("list", mode) if (mode or not w) else None
    if cmd == "delmsg" and mode and (len(w) == 1 or (len(w) == 2 and w[1] in ("همه", "all"))):
        return ("delall" if len(w) == 2 else "del", mode)
    return None


async def run(uid, ev, rt):
    action, m = rt
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    if action == "help":
        return HELP, True
    st = _load(uid)
    if action == "list":
        parts = []
        for k in ((m,) if m else ("friend", "enemy")):
            meta = _MODE[k]
            n = len(st["t"][k])
            parts.append(f"{meta['emoji']} {meta['label']} ({'روشن' if st['on'][k] else 'خاموش'}): "
                         + (f"{n} نفر — {_names(st, k)}" if n else "کسی اضافه نشده"))
        return "\n".join(parts), True
    if action == "delall":
        n = len(st["t"][m])
        st["t"][m] = {}
        _save(uid, st)
        return f"🗑 {n} نفر از {_MODE[m]['label']} پاک شد.", False
    r = await ev.get_reply_message() if ev.is_reply else None
    word = _MODE[m]["word"]
    if r is None:
        verb = "افزودن" if action == "add" else "حذف"
        return f"روی پیام کاربر ریپلای کن و بنویس: .{verb} {word}", False
    try:
        sender = await r.get_sender()
    except Exception:  # noqa: BLE001
        sender = None
    tid = getattr(sender, "id", None)
    if tid is None or type(sender).__name__ != "User":
        return "❌ فقط روی پیامِ یه کاربر کار می‌کنه (نه کانال یا ادمین ناشناس).", False
    key = f"{ev.chat_id}:{tid}"
    name = " ".join(x for x in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if x) or str(tid)
    if action == "del":
        if st["t"][m].pop(key, None) is None:
            return f"این کاربر توی {_MODE[m]['label']} نبود.", False
        _save(uid, st)
        return f"🗑 {name} از {_MODE[m]['label']} حذف شد.", False
    # add
    if tid == uid:
        return "❌ نمی‌تونی خودت رو اضافه کنی.", False
    if getattr(sender, "bot", False):
        return "❌ ربات رو نمی‌شه اضافه کرد.", False
    if key not in st["t"][m] and len(st["t"][m]) >= MAX_TARGETS:
        return f"❌ حداکثر {MAX_TARGETS} نفر برای هر حالت. اول یکی رو حذف کن.", False
    other = "enemy" if m == "friend" else "friend"
    moved = st["t"][other].pop(key, None) is not None       # یه نفر همزمان دوست و دشمن نمی‌شه
    st["t"][m][key] = {"i": 0, "n": name[:40]}
    turned_on = not st["on"][m]
    st["on"][m] = True
    _save(uid, st)
    txt = f"{_MODE[m]['emoji']} {name} به {_MODE[m]['label']} اضافه شد"
    if moved:
        txt += f" (از {_MODE[other]['label']} برداشته شد)"
    if turned_on:
        txt += " و حالتش روشن شد"
    return txt + ".", False


# ---------- جواب خودکار ----------
async def _on_incoming(uid, client, ev):
    st = _load(uid)
    if not (st["on"]["friend"] or st["on"]["enemy"]):
        return
    sid = ev.sender_id
    if not sid or sid == uid:
        return
    key = f"{ev.chat_id}:{sid}"
    mode = t = None
    for k in ("friend", "enemy"):
        if st["on"][k] and key in st["t"][k]:
            mode, t = k, st["t"][k][key]
            break
    if t is None:
        return
    now = time.time()
    if now - _last.get((uid, key), 0) < MIN_GAP:
        return
    h = [x for x in _hist.get(uid, []) if now - x < 60]
    if len(h) >= GLOBAL_PER_MIN:
        return
    h.append(now)
    _hist[uid] = h
    _last[(uid, key)] = now
    msgs = _MODE[mode]["msgs"]
    i = int(t.get("i", 0)) % len(msgs)
    t["i"] = (i + 1) % len(msgs)           # اول شمارنده جلو می‌ره تا دو پیام هم‌زمان یه جمله رو تکرار نکنن
    _save(uid, st)
    await asyncio.sleep(random.uniform(0.8, 1.8))
    await client.send_message(ev.chat_id, msgs[i], reply_to=ev.id)


def attach(uid, client):
    from telethon import events

    async def _h(ev):
        try:
            await _on_incoming(uid, client, ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.info("modes %s: %s", uid, e)

    client.add_event_handler(_h, events.NewMessage(incoming=True))
