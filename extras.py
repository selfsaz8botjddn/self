# -*- coding: utf-8 -*-
"""امکانات بیشتر سلف: پاسخ خودکار، پیام‌های حذف/ویرایش‌شده، ذخیره مدیای تایمردار،
منشن‌گیر، ناظر کلمه/کاربر، سکوت/بلاک سریع و دستورهای ابزار (حساب، قیمت، تاریخ، کوتاه‌کن لینک، استوری).
تنظیمات هر کاربر داخل همان JSON پروفایل (sb._pget/_pput) ذخیره می‌شود."""
import asyncio
import io
import logging
import random
import re
import time
from collections import OrderedDict

import selfbot as sb
import selftools as tools
import selfmore as more
import selfdl as dl
import selfmedia as media

logger = logging.getLogger("selfextras")

DEFAULT_AR = "سلام! الان آنلاین نیستم؛ هر وقت برگشتم جواب می‌دم 🙏"
AR_COOLDOWN = 6 * 3600        # به هر نفر حداکثر هر ۶ ساعت یک‌بار
AR_MAX_PER_HOUR = 20          # سقف کل پاسخ‌های خودکار در ساعت (برای ایمنی اکانت)
CACHE_MAX = 1500
CACHE_TTL = 48 * 3600
WL_MAX = 10
NOTIFY_PER_MIN = 20
WL_COOLDOWN = 20

MU_MAX = 30                   # حداکثر کاربر ساکت‌شده
TOOL_PER_MIN = 20             # سقف دستورهای ابزار در دقیقه (برای جلوگیری از فشار به سرویس‌های بیرونی)

TOGGLES = ("ar_on", "del_on", "ttl_on", "mn_on", "wl_on", "mu_on")
CB_ACTIONS = ("more", "mt", "ar_text", "wl", "wl_add", "wl_del", "mu", "mu_add", "mu_del", "tg")
TEXT_STEPS = ("ar_text", "wl_add", "mu_add")
CMD_WORDS = ("mute", "unmute", "block", "unblock", "undo", "calc", "price", "date", "short", "story", "balance", "stars",
             "info", "autorep", "welcome", "card", "vnote", "joinall", "yt", "insta", "tiktok", "pinterest", "sticker", "stt", "tts")
STAR_WORDS = ("استارز", "استار", "ستاره", "stars", "star")


# راهنمای هر ابزار؛ با زدن دکمه‌اش نشون داده می‌شه. دستورها <code> هستن (تک‌فاصله) تا با یک لمس کپی بشن.
TOOL_GUIDES = {
    "tr": ("ترجمه", "روی پیام متنی ریپلای کن و بنویس:\n<code>.ترجمه</code>\nترجمه به فارسی انجام می‌شه."),
    "purge": ("پاکسازی", "پیام‌های خودت رو دسته‌ای پاک می‌کنه (فقط توی همون چت):\n<code>.پاکسازی 50</code>\n۵۰ پیام آخر خودت (حداکثر ۱۰۰۰).\n"
              "<code>.پاکسازی همه</code>\nهمهٔ پیام‌های خودت توی این چت (حداکثر ۱۰۰۰ تا).\n"
              "روی یه پیام ریپلای کن و بنویس:\n<code>.پاکسازی</code>\nپیام‌های خودت از اون پیام تا آخر پاک می‌شن.\n"
              "پیام‌های یه کاربر (فقط وقتی ادمینِ دارای اجازهٔ حذف هستی یا توی پی‌وی):\nریپلای + <code>.پاکسازی کاربر</code>\n(حداکثر ۱۰۰ پیام اخیر اون کاربر)\n"
              "⚠️ برگشت‌پذیر نیست؛ توی پی‌وی برای هر دو طرف پاک می‌شه. برای جلوگیری از محدودیت تلگرام بین دسته‌ها مکث می‌کنه."),
    "tag": ("تگ اعضا", "توی گروه بنویس:\n<code>.تگ اعضا</code>\nبرای جلوگیری از اسپم، هر بار حداکثر ۱۰ عضو منشن می‌شن."),
    "calc": ("ماشین‌حساب", "بنویس:\n<code>.حساب 25*4+10</code>\nیا روی پیامِ دارای عبارت ریپلای کن و <code>.حساب</code> بفرست.\n"
             "پشتیبانی: جمع، تفریق، ضرب، تقسیم، توان ^ ، درصدِ باقی‌مانده % ، sqrt / abs / round / ln / log و اعداد فارسی."),
    "price": ("قیمت", "خلاصهٔ قیمت‌ها:\n<code>.قیمت</code>\nارز، طلا و سکه:\n<code>.قیمت دلار</code>\nیورو، پوند، درهم، لیر، طلا، سکه\n"
              "ارز دیجیتال:\n<code>.قیمت بیتکوین</code>\nاتریوم، تتر، تون، ریپل، سولانا، دوج، ترون، بایننس\nقیمت ارز/طلا به تومان نشون داده می‌شه."),
    "info": ("اطلاعات کاربر", "روی پیام کاربر ریپلای کن و بنویس:\n<code>.اطلاعات</code>\nیا:\n<code>.اطلاعات @یوزرنیم</code>\nآیدی، قدمت اکانت (سال ساخت، تخمینی از روی آیدی)، یوزرنیم، بیو، آخرین بازدید، گروه مشترک و ... رو نشون می‌ده."),
    "me": ("حساب من", "بنویس:\n<code>.حساب من</code>\nمشخصات اکانت خودت: آیدی، قدمت اکانت (سال ساخت تخمینی)، شماره، پرمیوم، استارز، تعداد چت و مخاطب و نشست‌های فعال."),
    "autorep": ("اتو ریپ", "روی پیام کاربر ریپلای کن و بنویس:\n<code>.اتو ریپ سلام 50</code>\nمتن رو به‌صورت ریپلای همون‌قدر می‌فرسته (حداکثر 100، با فاصلهٔ 1.5 ثانیه).\nتوقف:\n<code>.اتو ریپ توقف</code>\n⚠️ تعداد زیاد ممکنه تلگرام محدودیت بده."),
    "welcome": ("خوشامدگویی", "توی گروه بنویس:\n<code>.خوشامد</code>\nبعد از اون هر عضو جدید خوشامد می‌گیره. متن: <code>.خوشامد متن سلام {نام} خوش اومدی</code>\nخاموش: <code>.خوشامد خاموش</code>\nتلگرام توی گروه‌های خیلی شلوغ ورود اعضا رو همیشه نمی‌فرسته."),
    "card": ("کارت بانکی", "ذخیره:\n<code>.کارت ذخیره 6037 9918 1234 5678 به‌نام فلانی</code>\nارسال:\n<code>.کارت</code>\nحذف:\n<code>.کارت حذف</code>\nشماره رمزنگاری‌شده ذخیره می‌شه؛ ذخیره‌اش رو توی Saved Messages بنویس."),
    "vnote": ("ویدیو مسیج", "روی ویدیو یا گیف ریپلای کن و بنویس:\n<code>.ویدیو مسیج</code>\nبه ویدیو مسیج گرد (حداکثر 60 ثانیه) تبدیل و به‌صورت ریپلای فرستاده می‌شه."),
    "joinall": ("عضویت همه", "روی پیام ربات عضویت اجباری (دکمه‌دار) ریپلای کن و بنویس:\n<code>.عضویت همه</code>\nتوی همهٔ کانال‌های دکمه‌ها عضو می‌شه، دکمهٔ تأیید رو می‌زنه و نتیجه رو نشون می‌ده (حداکثر 20 کانال)."),
    "stars": ("موجودی استارز", "موجودی استارز تلگرامِ اکانتت رو نشون می‌ده:\n<code>.موجودی استارز</code>\nاکانت سلف باید وصل باشه."),
    "date": ("تاریخ", "تاریخ شمسی، میلادی، قمری و ساعت تهران:\n<code>.تاریخ</code>\nتبدیل تاریخ:\n<code>.تاریخ 1405/8/5</code>\nیا\n<code>.تاریخ 2026-10-27</code>"),
    "short": ("کوتاه‌کردن لینک", "بنویس:\n<code>.کوتاه https://example.com</code>\nیا روی پیام لینک‌دار ریپلای کن و <code>.کوتاه</code> بفرست (تا ۳ لینک)."),
    "story": ("ذخیرهٔ استوری", "روی استوری فورواردی/منشن‌شده ریپلای کن و بنویس:\n<code>.استوری</code>\nیا با لینک:\n<code>.استوری t.me/username/s/123</code>\n"
              "توی Saved Messages ذخیره می‌شه. استوری «محافظت از محتوا» ذخیره نمی‌شه."),
    "dl": ("دانلودر", "<b>یوتیوب</b> (توی Saved Messages ذخیره می‌شه):\n<code>.یوتیوب لینک</code>\n<code>.youtube لینک</code>\n"
           "کیفیت مشخص: <code>.یوتیوب 720 لینک</code>\nهمهٔ کیفیت‌های موجود: <code>.یوتیوب همه لینک</code>\n"
           "لیست کیفیت‌ها: <code>.یوتیوب لیست لینک</code>\nفقط صدا: <code>.یوتیوب صدا لینک</code>\n\n"
           "<b>اینستاگرام</b> (پست، ریلز، استوری؛ توی همون چت):\n<code>.اینستا لینک</code>\n<code>.insta لینک</code>\n\n"
           "<b>تیک‌تاک</b> (توی همون چت):\n<code>.تیک تاک لینک</code>\n<code>.tiktok لینک</code>\n\n"
           "<b>پینترست</b> (توی همون چت):\n<code>.پینترست لینک</code>\n<code>.pinterest لینک</code>\n\n"
           "به‌جای نوشتن لینک می‌تونی روی پیام لینک‌دار ریپلای کنی و فقط دستور رو بنویسی.\n"
           "⚠️ استوری اینستا و بعضی لینک‌ها (و یوتیوب روی بعضی سرورها) به کوکی اکانت نیاز دارن؛ مدیر ربات باید متغیرهای YT_COOKIES / IG_COOKIES رو تنظیم کنه."),
    "sticker": ("ساخت استیکر", "روی عکس، ویدیو، گیف، ویدیو مسیج یا متن ریپلای کن و بنویس:\n<code>.استیکر</code>\n"
                "همون پیام به استیکر تبدیل می‌شه و به‌صورت ریپلای فرستاده می‌شه.\n"
                "• ویدیو: استیکر ویدیویی (فقط ۳ ثانیهٔ اول، بدون صدا)\n• متن: استیکر متنی سفید با حاشیهٔ مشکی\n"
                "استیکر متنی بدون ریپلای:\n<code>.استیکر متن سلام</code>\n"
                "روی یه استیکر ریپلای کنی و <code>.استیکر</code> بنویسی، به عکس (یا ویدیو) تبدیل می‌شه.\n"
                "استیکر متحرک (TGS) پشتیبانی نمی‌شه."),
    "stt": ("ویس به متن", "روی یه ویس یا ویدیو مسیج ریپلای کن و بنویس:\n<code>.متن ویس</code>\n"
            "یا <code>.ویس به متن</code>\nمتن به‌صورت ریپلای روی ویس فرستاده می‌شه.\n"
            "⚠️ از رونویسی خود تلگرام استفاده می‌کنه؛ با اکانت پرمیوم بدون محدودیته و اکانت عادی فقط سهمیهٔ رایگان محدود داره."),
    "tts": ("متن به ویس", "بنویس:\n<code>.ویس سلام چطوری</code>\nصدای مرد: <code>.ویس مرد سلام</code>\nصدای زن: <code>.ویس زن سلام</code>\n"
            "سرعت: <code>.ویس سریع سلام</code> یا <code>.ویس آهسته سلام</code>\n"
            "یا روی یه متن ریپلای کن و فقط بنویس: <code>.ویس</code>\n"
            "فارسی یا انگلیسی خودکار تشخیص داده می‌شه (حداکثر ۵۰۰ حرف). از سرویس Edge TTS استفاده می‌کنه و سرور باید اینترنت داشته باشه."),
    "backup": ("بکاپ چت", "توی چت پی‌وی مورد نظر بنویس:\n<code>.بکاپ چت</code>\nهمهٔ پیام‌های همون پی‌وی (حداکثر ۲۰۰۰۰ پیام آخر) به‌صورت یک فایل HTML توی Saved Messages فرستاده می‌شه. مدیاها فقط با نوعشون (عکس، ویدیو، ...) نوشته می‌شن و دانلود نمی‌شن."),
    "pvjoin": ("عضویت اجباری پی‌وی", "روشن کردن:\n<code>.عضویت اجباری روشن</code>\nخاموش کردن:\n<code>.عضویت اجباری خاموش</code>\nوقتی روشنه، کسی که عضو کانال‌های تعیین‌شده نباشه پیامش پاک می‌شه و لینک کانال‌ها براش میاد. همون دکمهٔ «عضویت اجباری پیوی» توی پنل هم همین کار رو می‌کنه."),
    "save": ("ذخیرهٔ مدیا", "روی عکس/ویدیو/فایل ریپلای کن و بنویس:\n<code>.ذخیره</code>\nتوی Saved Messages ذخیره می‌شه."),
    "emoji": ("ایموجی پریمیوم", "بنویس:\n<code>.ایموجی متن</code>\nمتنت با ایموجی پریمیوم فرستاده می‌شه."),
    "mute": ("سکوت", "روی پیام کاربر ریپلای کن و بنویس:\n<code>.سکوت</code>\nبرداشتن:\n<code>.رفع سکوت</code>\nلیست و دکمهٔ روشن/خاموش از «امکانات بیشتر» ← مدیریت سکوت."),
    "block": ("بلاک سریع", "روی پیام کاربر ریپلای کن و بنویس:\n<code>.بلاک</code>\nرفع بلاک:\n<code>.رفع بلاک</code>\nبلاک واقعی تلگرام انجام می‌شه."),
}
TOOL_ORDER = ("tr", "tag", "purge", "dl", "sticker", "stt", "tts", "calc", "me", "info", "price", "stars", "date", "short", "story", "save", "emoji", "mute", "block",
              "autorep", "welcome", "card", "vnote", "joinall", "backup", "pvjoin")

_cache = {}      # uid -> OrderedDict[msg_id -> rec]
_ar_seen = {}    # uid -> {sender_id: ts}
_ar_hist = {}    # uid -> [ts]
_notif_hist = {}  # uid -> [ts]
_wl_seen = {}    # (uid, chat, item) -> ts
_mn_seen = {}    # (uid, chat, msg_id) -> ts
_tool_hist = {}  # uid -> [ts]


# ---------- کمکی‌ها ----------
def _name(e):
    if e is None:
        return "ناشناس"
    n = " ".join(x for x in (getattr(e, "first_name", None), getattr(e, "last_name", None)) if x)
    return n or getattr(e, "title", None) or (("@" + e.username) if getattr(e, "username", None) else str(getattr(e, "id", "؟")))


def _link(chat, mid):
    try:
        from telethon.tl.types import Channel
        un = getattr(chat, "username", None)
        if un:
            return f"https://t.me/{un}/{mid}"
        if isinstance(chat, Channel):
            return f"https://t.me/c/{chat.id}/{mid}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _kind(msg):
    for attr, label in (("photo", "عکس"), ("video", "ویدیو"), ("voice", "ویس"), ("video_note", "ویدیو مسیج"),
                        ("sticker", "استیکر"), ("gif", "گیف"), ("audio", "آهنگ"), ("document", "فایل")):
        if getattr(msg, attr, None):
            return label
    return "رسانه" if getattr(msg, "media", None) else ""


async def _notify(uid, text):
    """اعلان توی چت ربات؛ سقف تعداد در دقیقه برای جلوگیری از اسپم."""
    bot = sb._botref[0]
    if not bot:
        return
    now = time.time()
    h = [t for t in _notif_hist.get(uid, []) if now - t < 60]
    if len(h) >= NOTIFY_PER_MIN:
        return
    h.append(now)
    _notif_hist[uid] = h
    try:
        await bot.send_message(uid, text[:4000], disable_web_page_preview=True)
    except Exception as e:  # noqa: BLE001
        logger.info("extras notify %s: %s", uid, e)


def cleanup(uid):
    _cache.pop(uid, None)
    _ar_seen.pop(uid, None)
    _ar_hist.pop(uid, None)
    _notif_hist.pop(uid, None)
    _tool_hist.pop(uid, None)


def match_watch(items, text, sender_id, username):
    """آیتم‌های ناظر که با این پیام جور هستن (کلمه / @یوزرنیم / آیدی عددی)."""
    low = (text or "").lower()
    un = (username or "").lower()
    out = []
    for it in items or []:
        k = it.lower()
        if k.startswith("@"):
            if un and k[1:] == un:
                out.append(it)
        elif k.isdigit():
            if sender_id is not None and int(k) == int(sender_id):
                out.append(it)
        elif k and k in low:
            out.append(it)
    return out


# ---------- اتصال هندلرها ----------
def attach(uid, client):
    from telethon import events

    async def _in(ev):
        await _safe(on_incoming(uid, client, ev))

    async def _ed(ev):
        await _safe(on_edited(uid, ev))

    async def _del(ev):
        await _safe(on_deleted(uid, ev))

    client.add_event_handler(_in, events.NewMessage(incoming=True))
    client.add_event_handler(_ed, events.MessageEdited(incoming=True))
    client.add_event_handler(_del, events.MessageDeleted())
    more.attach(uid, client)


async def _safe(coro):
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.info("extras handler: %s", e)


# ---------- پیام ورودی ----------
async def on_incoming(uid, client, ev):
    d = sb._pget(uid)
    if not any(d.get(k) for k in TOGGLES):
        return
    msg = ev.message
    if msg is None:
        return
    try:
        snd = await ev.get_sender()
    except Exception:  # noqa: BLE001
        snd = None
    sid = getattr(snd, "id", None)
    if sid is not None and sid == sb._bot_id[0]:
        return
    text = msg.raw_text or ""

    # ۰) سکوت: پیام کاربرِ ساکت‌شده — در پی‌وی فقط از سمت خودت پاک می‌شود؛ بقیهٔ امکانات هم نادیده‌اش می‌گیرند
    if d.get("mu_on") and sid is not None and str(sid) in (d.get("mu_ids") or {}):
        if ev.is_private:
            try:
                await client.delete_messages(ev.chat_id, [msg.id], revoke=False)
            except Exception as e:  # noqa: BLE001
                logger.info("extras mute delete %s: %s", uid, e)
        return

    # ۲) حافظهٔ پیام‌ها برای حذف/ویرایش (فقط چت‌های خصوصی)
    if d.get("del_on") and ev.is_private:
        c = _cache.setdefault(uid, OrderedDict())
        c[msg.id] = {"chat": ev.chat_id, "name": _name(snd), "text": text, "media": _kind(msg), "ts": time.time()}
        while len(c) > CACHE_MAX:
            c.popitem(last=False)

    # ۳) ذخیرهٔ خودکار مدیای تایمردار
    if d.get("ttl_on") and getattr(getattr(msg, "media", None), "ttl_seconds", None):
        ok = await _save_media(client, msg, "me", f"⏱ مدیای تایمردار از {_name(snd)}" + (f"\n{text}" if text else ""))
        if not ok:
            await _notify(uid, f"⚠️ مدیای تایمردار از {_name(snd)} رسید ولی ذخیره نشد.")

    # ۱) پاسخ خودکار
    if d.get("ar_on") and ev.is_private and snd is not None and not getattr(snd, "bot", False) and sid != 777000:
        now = time.time()
        seen = _ar_seen.setdefault(uid, {})
        if now - seen.get(sid, 0) >= AR_COOLDOWN:
            hist = [t for t in _ar_hist.get(uid, []) if now - t < 3600]
            if len(hist) < AR_MAX_PER_HOUR:
                seen[sid] = now
                hist.append(now)
                _ar_hist[uid] = hist
                if len(seen) > 5000:
                    for k in sorted(seen, key=seen.get)[:1000]:
                        seen.pop(k, None)
                await asyncio.sleep(random.uniform(2, 5))
                await client.send_message(ev.chat_id, (d.get("ar_text") or DEFAULT_AR)[:500])

    # ۴) منشن‌گیر
    if d.get("mn_on") and ev.is_group and getattr(msg, "mentioned", False):
        key = (uid, ev.chat_id, msg.id)
        if key not in _mn_seen:
            _mn_seen[key] = time.time()
            if len(_mn_seen) > 3000:
                _mn_seen.clear()
            try:
                chat = await ev.get_chat()
            except Exception:  # noqa: BLE001
                chat = None
            link = _link(chat, msg.id)
            await _notify(uid, f"📣 تگ شدی\n👥 گروه: {_name(chat)}\n👤 از: {_name(snd)}\n💬 {text[:300] or '[' + (_kind(msg) or 'بدون متن') + ']'}"
                          + (f"\n🔗 {link}" if link else ""))

    # ۹) ناظر
    items = d.get("wl_items") or []
    if d.get("wl_on") and items:
        hits = match_watch(items, text, sid, getattr(snd, "username", None))
        if hits:
            now = time.time()
            fresh = [h for h in hits if now - _wl_seen.get((uid, ev.chat_id, h), 0) >= WL_COOLDOWN]
            for h in fresh:
                _wl_seen[(uid, ev.chat_id, h)] = now
            if len(_wl_seen) > 3000:
                _wl_seen.clear()
            if fresh:
                try:
                    chat = await ev.get_chat()
                except Exception:  # noqa: BLE001
                    chat = None
                link = _link(chat, msg.id)
                await _notify(uid, f"👁 ناظر: {'، '.join(fresh)}\n💬 چت: {_name(chat)}\n👤 از: {_name(snd)}\n📝 {text[:300] or '[' + (_kind(msg) or 'بدون متن') + ']'}"
                              + (f"\n🔗 {link}" if link else ""))


# ---------- ویرایش و حذف ----------
async def on_edited(uid, ev):
    if not sb._pget(uid).get("del_on") or not ev.is_private:
        return
    c = _cache.get(uid)
    rec = c.get(ev.message.id) if c else None
    new = ev.message.raw_text or ""
    if rec and rec["text"] != new:
        old = rec["text"]
        rec["text"] = new
        await _notify(uid, f"✏️ پیام ویرایش شد\n👤 از: {rec['name']}\n⬅️ قبل: {old[:500] or '[بدون متن]'}\n➡️ بعد: {new[:500] or '[بدون متن]'}")


async def on_deleted(uid, ev):
    if not sb._pget(uid).get("del_on"):
        return
    c = _cache.get(uid)
    if not c:
        return
    now = time.time()
    out = []
    for mid in ev.deleted_ids or []:
        rec = c.get(mid)
        if not rec:
            continue
        if ev.chat_id and rec["chat"] != ev.chat_id:
            continue
        c.pop(mid, None)
        if now - rec["ts"] <= CACHE_TTL:
            out.append(rec)
    for rec in out[:10]:
        body = rec["text"] or (f"[{rec['media']}]" if rec["media"] else "[بدون متن]")
        await _notify(uid, f"🗑 پیام حذف شد\n👤 از: {rec['name']}\n💬 {body[:800]}" + (f"\n📎 همراه با {rec['media']}" if rec["media"] and rec["text"] else ""))
    if len(out) > 10:
        await _notify(uid, f"🗑 و {len(out) - 10} پیام حذف‌شدهٔ دیگر.")


# ---------- ذخیرهٔ مدیا ----------
async def _save_media(client, msg, dest, caption):
    try:
        data = await client.download_media(msg, file=bytes)
        if not data:
            return False
        ext = getattr(getattr(msg, "file", None), "ext", None) or ".bin"
        bio = io.BytesIO(data)
        bio.name = "media" + ext
        await client.send_file(dest, bio, caption=caption[:1000])
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("extras save media: %s", e)
        return False


async def save_replied(uid, ev):
    """دستور .ذخیره (ریپلای روی مدیا) → مدیا به Saved Messages می‌رود."""
    client = sb._clients.get(uid)
    if not client or not ev.is_reply:
        return "روی مدیای موردنظر ریپلای کن و بنویس .ذخیره"
    r = await ev.get_reply_message()
    if not r or not getattr(r, "media", None):
        return "پیام ریپلای‌شده مدیا ندارد."
    ok = await _save_media(client, r, "me", "💾 ذخیره‌شده با .ذخیره")
    return "✅ توی Saved Messages ذخیره شد." if ok else "❌ ذخیره نشد؛ احتمالاً مدیا قبلاً منقضی یا باز شده."


# ---------- پنل ----------
def _st(d, k):
    return "روشن" if d.get(k) else "خاموش"


MORE_ROWS_PER_PAGE = 8


def toggle(uid, key):
    """روشن/خاموش‌کردن یکی از سوئیچ‌های امکانات بیشتر (برای صفحهٔ یکپارچهٔ دستورات سلف)."""
    if key not in TOGGLES:
        return
    d = sb._pget(uid)
    d[key] = not bool(d.get(key))
    if key == "ar_on" and d[key] and not d.get("ar_text"):
        d["ar_text"] = DEFAULT_AR
    sb._pput(uid, d)


def more_rows(uid):
    """همهٔ دکمه‌های «امکانات بیشتر» (سوئیچ‌ها + ابزارها) به‌صورت ردیف، بدون صفحه‌بندی."""
    d = sb._pget(uid)
    B = sb.B
    wl = d.get("wl_items") or []
    mu = d.get("mu_ids") or {}

    def t(k, label):
        return B(f"{label}: {_st(d, k)}", callback_data=f"self:mt:{k}:1", style="success" if d.get(k) else "primary")

    rows = [
        [t("ar_on", "پاسخ خودکار پی‌وی"), B("متن پاسخ", callback_data="self:ar_text", style="primary")],
        [t("del_on", "خبر حذف/ویرایش پی‌وی")],
        [t("ttl_on", "ذخیرهٔ مدیای تایمردار")],
        [t("mn_on", "خبر منشن در گروه‌ها")],
        [t("wl_on", f"ناظر کلمه/آیدی ({len(wl)})"), B("مدیریت ناظر", callback_data="self:wl", style="primary")],
        [t("mu_on", f"سکوت پی‌وی ({len(mu)} نفر)"), B("مدیریت سکوت", callback_data="self:mu", style="primary")],
    ]
    tb = [B(TOOL_GUIDES[k][0], callback_data=f"self:tg:{k}", style="primary") for k in TOOL_ORDER]
    rows += [tb[i:i + 2] for i in range(0, len(tb), 2)]
    return rows


def more_panel(uid, page=1):
    """«امکانات بیشتر»؛ همهٔ دکمه‌ها یک‌جا و توضیح هر امکان داخل خود دکمه است.
    اگه ردیف‌ها از MORE_ROWS_PER_PAGE بیشتر شد، خودکار می‌ره صفحهٔ بعد."""
    d = sb._pget(uid)
    B, KB = sb.B, sb.KB
    note = "" if uid in sb._clients else "⚠️ اکانت وصل نیست؛ بعد از اتصال کار می‌کنن.\n\n"
    wl = d.get("wl_items") or []
    mu = d.get("mu_ids") or {}

    def t(k, label):
        return B(f"{label}: {_st(d, k)}", callback_data=f"self:mt:{k}:{page}", style="success" if d.get(k) else "primary")

    page1 = [
        [t("ar_on", "پاسخ خودکار پی‌وی"), B("متن پاسخ", callback_data="self:ar_text", style="primary")],
        [t("del_on", "خبر حذف/ویرایش پی‌وی")],
        [t("ttl_on", "ذخیرهٔ مدیای تایمردار")],
        [t("mn_on", "خبر منشن در گروه‌ها")],
        [t("wl_on", f"ناظر کلمه/آیدی ({len(wl)})"), B("مدیریت ناظر", callback_data="self:wl", style="primary")],
        [t("mu_on", f"سکوت پی‌وی ({len(mu)} نفر)"), B("مدیریت سکوت", callback_data="self:mu", style="primary")],
    ]
    # صفحهٔ ابزارها: هر ابزار یه دکمهٔ واقعیه؛ با زدنش راهنمای استفاده‌اش میاد
    tb = [B(TOOL_GUIDES[k][0], callback_data=f"self:tg:{k}", style="primary") for k in TOOL_ORDER]
    page2 = [tb[i:i + 2] for i in range(0, len(tb), 2)]
    groups = []
    for g in (page1, page2):
        for i in range(0, len(g), MORE_ROWS_PER_PAGE):
            groups.append(g[i:i + MORE_ROWS_PER_PAGE])
    pages = len(groups)
    page = min(max(int(page or 1), 1), pages)
    rows = list(groups[page - 1])
    nav = []
    if page > 1:
        nav.append(B("◀ صفحهٔ قبل", callback_data=f"self:more:{page - 1}", style="primary"))
    if page < pages:
        nav.append(B("صفحهٔ بعد ▶", callback_data=f"self:more:{page + 1}", style="success"))
    if nav:
        rows.append(nav)
    rows.append([B("بازگشت", callback_data="self:home", style="primary")])
    tx = note + "› امکانات بیشتر" + (f" ({page}/{pages})" if pages > 1 else "")
    return tx, KB(rows)


def wl_panel(uid):
    d = sb._pget(uid)
    items = d.get("wl_items") or []
    B, KB = sb.B, sb.KB
    tx = "› مدیریت ناظر\n\n" + (
        "موردی ثبت نشده.\nکلمه، @یوزرنیم یا آیدی عددی اضافه کن؛ هر وقت توی هر چتی دیده بشه خبرت می‌کنم."
        if not items else "برای حذف، روی هر مورد بزن:") + f"\n(حداکثر {WL_MAX} مورد)"
    rows = [[B(f"🗑 {it[:40]}", callback_data=f"self:wl_del:{i}", style="danger")] for i, it in enumerate(items)]
    if len(items) < WL_MAX:
        rows.append([B("افزودن مورد", callback_data="self:wl_add", style="success")])
    rows.append([B("بازگشت", callback_data="self:more:1", style="primary")])
    return tx, KB(rows)


def mu_panel(uid):
    d = sb._pget(uid)
    ids = d.get("mu_ids") or {}
    B, KB = sb.B, sb.KB
    tx = ("› مدیریت سکوت\n\n"
          + ("کسی ساکت نشده.\nروی پیام کاربر ریپلای کن و بنویس .سکوت، یا با دکمهٔ زیر اضافه کن."
             if not ids else "برای برداشتن سکوت، روی هر مورد بزن:")
          + f"\n(حداکثر {MU_MAX} نفر)\n\n"
          "⚠️ در پی‌وی پیام‌های جدیدشان فقط از سمت خودت پاک می‌شود (طرف مقابل چیزی نمی‌بیند). "
          "در گروه‌ها تلگرام اجازهٔ حذف پیام دیگران را نمی‌دهد؛ فقط اعلان‌های سلف (منشن‌گیر/ناظر) برایشان قطع می‌شود.")
    rows = [[B(f"🔇 {name[:30]}", callback_data=f"self:mu_del:{tid}", style="danger")] for tid, name in ids.items()]
    if len(ids) < MU_MAX:
        rows.append([B("افزودن کاربر", callback_data="self:mu_add", style="success")])
    rows.append([B("بازگشت", callback_data="self:more:1", style="primary")])
    return tx[:1000], KB(rows)


async def handle_cb(uid, a, p, qm, context):
    ud = context.user_data
    B, KB = sb.B, sb.KB
    if a == "more":
        page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 1
        tx, kb = more_panel(uid, page)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "tg":
        key = p[2] if len(p) > 2 else ""
        if key in TOOL_GUIDES:
            title, body = TOOL_GUIDES[key]
            await qm.edit_text(f"<b>{title}</b>\n\n{body}", parse_mode="HTML",
                               reply_markup=KB([[B("بازگشت", callback_data=f"self:more:{2 + TOOL_ORDER.index(key) // 2 // MORE_ROWS_PER_PAGE}", style="primary")]]))
    elif a == "mt":
        key = p[2] if len(p) > 2 else ""
        page = int(p[3]) if len(p) > 3 and p[3].isdigit() else 1
        if key in TOGGLES:
            d = sb._pget(uid)
            d[key] = not bool(d.get(key))
            if key == "ar_on" and d[key] and not d.get("ar_text"):
                d["ar_text"] = DEFAULT_AR
            sb._pput(uid, d)
        tx, kb = more_panel(uid, page)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "ar_text":
        ud["self_st"] = {"step": "ar_text"}
        cur = sb._pget(uid).get("ar_text") or DEFAULT_AR
        await context.bot.send_message(uid, f"متن فعلی:\n{cur}\n\nمتن جدید پاسخ خودکار رو بفرست (حداکثر ۵۰۰ حرف). برای لغو بنویس «لغو».")
    elif a == "wl":
        tx, kb = wl_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "wl_add":
        ud["self_st"] = {"step": "wl_add"}
        await context.bot.send_message(uid, "کلمه، @یوزرنیم یا آیدی عددی رو بفرست (چندتا رو با خط جدید یا کاما جدا کن). برای لغو بنویس «لغو».")
    elif a == "wl_del":
        d = sb._pget(uid)
        items = d.get("wl_items") or []
        try:
            items.pop(int(p[2]))
        except Exception:  # noqa: BLE001
            pass
        d["wl_items"] = items
        sb._pput(uid, d)
        tx, kb = wl_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "mu":
        tx, kb = mu_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)
    elif a == "mu_add":
        ud["self_st"] = {"step": "mu_add"}
        await context.bot.send_message(uid, "آیدی عددی یا @یوزرنیم کاربر رو بفرست (چندتا رو با خط جدید یا کاما جدا کن). "
                                            "برای @یوزرنیم باید اکانت سلف وصل باشه. برای لغو بنویس «لغو».")
    elif a == "mu_del":
        d = sb._pget(uid)
        ids = d.get("mu_ids") or {}
        ids.pop(p[2] if len(p) > 2 else "", None)
        d["mu_ids"] = ids
        sb._pput(uid, d)
        tx, kb = mu_panel(uid)
        await qm.edit_text(tx, reply_markup=kb)


async def _mu_add_text(uid, text):
    """آیدی عددی یا @یوزرنیم‌های متن را به لیست سکوت اضافه می‌کند؛ تعداد اضافه‌شده را برمی‌گرداند."""
    from telethon.tl.types import User
    d = sb._pget(uid)
    ids = d.get("mu_ids") or {}
    client = sb._clients.get(uid)
    added = 0
    for raw in re.split(r"[,\n،]+", text or ""):
        x = sb.fa2en(raw).strip()
        if not x or len(ids) >= MU_MAX:
            continue
        tid, name = None, x[:40]
        try:
            if x.lstrip("@").isdigit():
                tid = int(x.lstrip("@"))
                if client:
                    try:
                        ent = await asyncio.wait_for(client.get_entity(tid), 10)
                        name = _name(ent)[:40]
                    except Exception:  # noqa: BLE001
                        pass
            elif client and re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{3,31}", x):
                ent = await asyncio.wait_for(client.get_entity(x.lstrip("@")), 10)
                if isinstance(ent, User):
                    tid, name = ent.id, _name(ent)[:40]
        except Exception as e:  # noqa: BLE001
            logger.info("extras mu_add resolve %s: %s", uid, e)
        if tid is None or tid in (uid, sb._bot_id[0], 777000) or str(tid) in ids:
            continue
        ids[str(tid)] = name
        added += 1
    d["mu_ids"] = ids
    if added:
        d["mu_on"] = True
    sb._pput(uid, d)
    return added


async def handle_text(uid, st, text, msg, context):
    step = st.get("step")
    if text in ("لغو", "/cancel"):
        await msg.reply_text("لغو شد.")
        return
    d = sb._pget(uid)
    panel = (more_panel, (uid,))
    if step == "ar_text":
        d["ar_text"] = text[:500]
        sb._pput(uid, d)
        await msg.reply_text("✅ متن پاسخ خودکار ذخیره شد.")
    elif step == "wl_add":
        items = d.get("wl_items") or []
        raw = [x.strip() for x in text.replace("،", ",").replace("\n", ",").split(",")]
        added = 0
        for x in raw:
            x = x.strip()[:40]
            if not x or x.lower() in [i.lower() for i in items] or len(items) >= WL_MAX:
                continue
            items.append(x)
            added += 1
        d["wl_items"] = items
        if added:
            d["wl_on"] = True
        sb._pput(uid, d)
        await msg.reply_text(f"✅ {added} مورد اضافه شد." if added else "موردی اضافه نشد (تکراری، خالی یا سقف ۱۰ مورد).")
    elif step == "mu_add":
        added = await _mu_add_text(uid, text)
        await msg.reply_text(f"✅ {added} نفر ساکت شد." if added
                             else "موردی اضافه نشد (پیدا نشد، تکراری، سقف پر، یا اکانت سلف وصل نیست).")
        panel = (mu_panel, (uid,))
    tx, kb = panel[0](*panel[1])
    await msg.reply_text(tx, reply_markup=kb)


# ---------- دستورهای ابزار و سکوت/بلاک (از چت‌ها با نقطه) ----------
def _rate_ok(uid):
    now = time.time()
    h = [t for t in _tool_hist.get(uid, []) if now - t < 60]
    if len(h) >= TOOL_PER_MIN:
        _tool_hist[uid] = h
        return False
    h.append(now)
    _tool_hist[uid] = h
    return True


async def _target(ev):
    """هدف دستور سکوت/بلاک: فرستندهٔ پیامِ ریپلای‌شده؛ یا در پی‌وی، طرف گفتگو. → (entity, input_peer) یا (None, None)."""
    from telethon.tl.types import User
    if ev.is_reply:
        r = await ev.get_reply_message()
        if r is not None and not r.out:
            snd = await r.get_sender()
            if isinstance(snd, User):
                return snd, await r.get_input_sender()
    if ev.is_private:
        chat = await ev.get_chat()
        if isinstance(chat, User):
            return chat, await ev.get_input_chat()
    return None, None


_NEED_TARGET = "روی پیام یه کاربر ریپلای کن (توی پی‌وی هم می‌تونی بدون ریپلای بنویسی)."


async def _cmd_mute(uid, ev, on):
    ent, _ = await _target(ev)
    if ent is None:
        return _NEED_TARGET, False
    tid = ent.id
    if tid in (uid, sb._bot_id[0], 777000):
        return "نمی‌شه این حساب رو ساکت کرد.", False
    d = sb._pget(uid)
    ids = d.get("mu_ids") or {}
    name = _name(ent)[:40]
    if on:
        if str(tid) not in ids and len(ids) >= MU_MAX:
            return f"سقف {MU_MAX} نفر پُره؛ از پنل ← امکانات بیشتر ← مدیریت سکوت یکی رو بردار.", False
        ids[str(tid)] = name
        d["mu_ids"], d["mu_on"] = ids, True
        sb._pput(uid, d)
        return f"🔇 «{name}» ساکت شد؛ پیام‌های جدیدش توی پی‌وی از سمت تو پاک می‌شه. برداشتن: .سکوت خاموش", False
    if ids.pop(str(tid), None) is None:
        return f"«{name}» توی لیست سکوت نبود.", False
    d["mu_ids"] = ids
    sb._pput(uid, d)
    return f"🔔 سکوت «{name}» برداشته شد.", False


async def _cmd_block(uid, ev, on):
    client = sb._clients.get(uid)
    ent, inp = await _target(ev)
    if client is None or ent is None or inp is None:
        return _NEED_TARGET, False
    if ent.id in (uid, sb._bot_id[0], 777000):
        return "نمی‌شه این حساب رو بلاک کرد.", False
    from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
    await client(BlockRequest(id=inp) if on else UnblockRequest(id=inp))
    name = _name(ent)[:40]
    return (f"⛔️ «{name}» بلاک شد. برداشتن: .رفع بلاک" if on else f"✅ بلاک «{name}» برداشته شد."), False


async def _reply_text(ev):
    if ev.is_reply:
        r = await ev.get_reply_message()
        if r is not None:
            return r.raw_text or "", getattr(r, "entities", None)
    return "", None


def _fmt_stars(bal):
    """balance تلگرام: توی لایه‌های قدیمی عدد صحیحه؛ توی جدیدها StarsAmount (amount + nanos)."""
    amount = getattr(bal, "amount", bal)
    nanos = getattr(bal, "nanos", 0) or 0
    amount = int(amount or 0)
    if nanos:
        frac = f"{nanos / 1e9:.2f}".lstrip("0")      # مثلاً ".50"
        return f"{amount:,}{frac}"
    return f"{amount:,}"


async def _cmd_stars(uid):
    """موجودی استارز تلگرامِ خودِ اکانت سلف (payments.getStarsStatus روی peer خودش)."""
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    try:
        from telethon.tl.functions.payments import GetStarsStatusRequest
        from telethon.tl.types import InputPeerSelf
    except ImportError:
        return "❌ نسخهٔ telethon قدیمیه؛ توی requirements.txt نسخه رو بالا ببر و دوباره دیپلوی کن.", False
    res = await asyncio.wait_for(client(GetStarsStatusRequest(peer=InputPeerSelf())), 20)
    return f"⭐️ موجودی استارز: {_fmt_stars(res.balance)}", True


async def _dispatch(uid, ev, cmd, arg):
    """→ (متن، نگه‌داشتن پیام). نگه‌داشتن=True یعنی جواب بعد از ۱۰ ثانیه پاک نشود."""
    if cmd in dl.CMDS:
        return await dl.run(uid, ev, cmd, arg)
    if cmd in media.CMDS:
        return await media.run(uid, ev, cmd, arg)
    if cmd in more.CMDS:
        return await more.run(uid, ev, cmd, arg)
    if cmd == "mute":
        return await _cmd_mute(uid, ev, on=norm_arg(arg) not in ("خاموش", "off", "غیرفعال", "حذف"))
    if cmd == "unmute":
        return await _cmd_mute(uid, ev, on=False)
    if cmd == "block":
        return await _cmd_block(uid, ev, on=True)
    if cmd == "unblock":
        return await _cmd_block(uid, ev, on=False)
    if cmd == "undo":
        what = norm_arg(arg)
        if what in ("سکوت", "mute"):
            return await _cmd_mute(uid, ev, on=False)
        if what in ("بلاک", "block"):
            return await _cmd_block(uid, ev, on=False)
        return "مثل: .رفع سکوت   یا   .رفع بلاک", False

    if cmd == "calc" and norm_arg(arg) in ("من", "me", "my"):
        return await more.cmd_account(uid)

    if cmd == "calc":
        expr = arg
        if not expr.strip():
            expr = (await _reply_text(ev))[0]
        if not expr.strip():
            return "🧮 مثل: .حساب 25*4+10   (یا ریپلای روی پیامی که عبارت داره)", False
        try:
            e, res = tools.calc(expr)
        except tools.CalcError as ex:
            return f"❌ {ex}\n🧮 مثل: .حساب (12+8)*3/2   |   .حساب sqrt(144)", False
        return f"🧮 {e} = {res}", True

    if cmd == "price":
        try:
            return await tools.price_text(arg), True
        except Exception as e:  # noqa: BLE001
            logger.info("extras price: %s", e)
            return "❌ منبع قیمت الان در دسترس نیست؛ چند دقیقهٔ دیگه دوباره امتحان کن.", False

    if cmd == "date":
        return tools.date_text(arg), True

    if cmd == "balance":
        if norm_arg(arg) in STAR_WORDS:
            return await _cmd_stars(uid)
        return "💰 مثل: .موجودی استارز", False

    if cmd == "stars":
        return await _cmd_stars(uid)

    if cmd == "short":
        text, ents = arg, None
        urls = tools.find_urls(text)
        if not urls:
            text, ents = await _reply_text(ev)
            urls = tools.find_urls(text, ents)
        if not urls:
            return "🔗 مثل: .کوتاه https://example.com/very/long/link   (یا ریپلای روی پیام لینک‌دار)", False
        lines = []
        for u in urls:
            try:
                lines.append(await tools.shorten(u))
            except Exception as e:  # noqa: BLE001
                logger.info("extras shorten: %s", e)
                lines.append("❌ کوتاه نشد: " + (u[:40] + "…" if len(u) > 40 else u))
        return "🔗 لینک کوتاه:\n" + "\n".join(lines), True

    if cmd == "story":
        client = sb._clients.get(uid)
        if client is None:
            return "اکانت سلف وصل نیست.", False
        return await tools.story_save(client, ev, arg), False
    return None, False


def norm_arg(s):
    return sb.norm(s)


async def handle_cmd(uid, ev, cmd, arg):
    """نقطهٔ ورود دستورهای نقطه‌ای این فایل؛ از selfbot._handle_dot صدا زده می‌شود."""
    if not _rate_ok(uid):
        await sb._say(uid, ev, "⏳ دستورها پشت‌سرهم زیاد شد؛ یه دقیقه صبر کن.")
        return
    try:
        out, keep = await _dispatch(uid, ev, cmd, arg)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.info("extras cmd %s: %s", cmd, e)
        out, keep = "❌ انجام نشد؛ دوباره امتحان کن.", False
    if out:
        await sb._say(uid, ev, out, keep=keep)
