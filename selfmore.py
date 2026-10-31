# -*- coding: utf-8 -*-
"""دستورهای جدید سلف (نسخهٔ ۲۳): اطلاعات، حساب من، اتو ریپ، خوشامدگویی، کارت بانکی،
ویدیو مسیج و عضویت همه. از extras._dispatch صدا زده می‌شن؛ هر تابع (متن، نگه‌داشتن) برمی‌گردونه."""
import asyncio
import html as _html
import logging
import os
import re
import shutil
import tempfile
import time
from datetime import timezone

import selfbot as sb

logger = logging.getLogger("selfmore")

CMDS = ("info", "autorep", "welcome", "card", "vnote", "joinall")

AUTOREP_MAX = 100
AUTOREP_GAP = 1.5            # فاصلهٔ بین پیام‌ها (ثانیه) برای کم‌کردن ریسک فلود
JOIN_MAX = 20                # حداکثر لینک در «عضویت همه»
JOIN_GAP = 2.5
WC_DEFAULT = "سلام {نام} 🌹 خوش اومدی!"
CONFIRM_WORDS = ("تایید", "تأیید", "عضو شدم", "عضوشدم", "بررسی", "چک", "check", "confirm",
                 "joined", "done", "verify", "✅")

_ar_tasks = {}     # uid -> task
_wc_cache = {}     # uid -> (ts, chats, text)
_wc_seen = {}      # (uid, chat) -> ts


def _nm(e):
    if e is None:
        return "ناشناس"
    n = " ".join(x for x in (getattr(e, "first_name", None), getattr(e, "last_name", None)) if x)
    return n or getattr(e, "title", None) or str(getattr(e, "id", "؟"))


def _tehran(dt):
    try:
        from zoneinfo import ZoneInfo
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return str(dt)[:16]


def _status(st):
    n = type(st).__name__ if st is not None else ""
    if n == "UserStatusOnline":
        return "🟢 آنلاین"
    if n == "UserStatusOffline":
        return "آخرین بازدید: " + _tehran(st.was_online)
    return {"UserStatusRecently": "اخیراً آنلاین بوده", "UserStatusLastWeek": "هفتهٔ اخیر",
            "UserStatusLastMonth": "ماه اخیر"}.get(n, "مخفی")


# ---------- قدمت اکانت ----------
# تلگرام تاریخ ساخت اکانت رو نمی‌ده؛ از روی آیدی عددی (که تقریباً با زمان صعودیه) تخمین زده می‌شه.
# جدول (آیدی، سال-ماه) تقریبیه؛ دقت در حد «سال» قابل‌اتکاست. با اکانت‌های با تاریخ مشخص چک و اصلاحش کن.
_AGE_ANCHORS = [
    (1_000_000, "2013-09"), (100_000_000, "2015-03"), (200_000_000, "2016-05"), (400_000_000, "2017-10"),
    (700_000_000, "2019-03"), (1_000_000_000, "2020-05"), (1_500_000_000, "2021-02"), (2_000_000_000, "2021-10"),
    (5_000_000_000, "2022-01"), (6_000_000_000, "2022-10"), (7_000_000_000, "2023-10"), (7_500_000_000, "2024-10"),
    (8_000_000_000, "2025-04"),
]


def _g2j(gy, gm, gd):
    """میلادی → شمسی (الگوریتم استاندارد jalaali)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + days % 31 if days < 186 else 1 + (days - 186) % 30
    return jy, jm, jd


def est_creation(user_id):
    """→ (datetime تقریبی، «جدیدتر از آخرین نقطه» هست؟)"""
    from datetime import datetime
    pts = [(i, datetime.strptime(d, "%Y-%m").replace(tzinfo=timezone.utc).timestamp()) for i, d in _AGE_ANCHORS]
    uid = int(user_id)
    if uid <= pts[0][0]:
        ts, newer = pts[0][1], False
    elif uid >= pts[-1][0]:
        ts, newer = pts[-1][1], True
    else:
        ts, newer = pts[-1][1], False
        for (i0, t0), (i1, t1) in zip(pts, pts[1:]):
            if i0 <= uid <= i1:
                ts = t0 + (t1 - t0) * (uid - i0) / (i1 - i0)
                break
    return datetime.fromtimestamp(ts, tz=timezone.utc), newer


def age_line(user_id):
    from datetime import datetime
    try:
        dt, newer = est_creation(user_id)
    except Exception:  # noqa: BLE001
        return ""
    now = datetime.now(timezone.utc)
    months = max((now.year - dt.year) * 12 + now.month - dt.month, 0)
    y, m = divmod(months, 12)
    jy = _g2j(dt.year, dt.month, dt.day or 1)[0]
    age = (f"{y} سال" if y else "") + (" و " if y and m else "") + (f"{m} ماه" if m or not y else "")
    pre = "حدود سال" if not newer else "سال"
    tail = " یا جدیدتر" if newer else ""
    return f"📅 ساخت اکانت: {pre} {dt.year} میلادی ({jy} شمسی){tail} — حدود {age} (تخمینی)"


# ---------- .اطلاعات ----------
async def cmd_info(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    from telethon.tl.types import User
    from telethon.tl.functions.users import GetFullUserRequest
    ent = None
    a = sb.fa2en(arg).strip()
    try:
        if a:
            ent = await asyncio.wait_for(client.get_entity(int(a) if a.lstrip("@").isdigit() else a.lstrip("@")), 15)
        elif ev.is_reply:
            r = await ev.get_reply_message()
            ent = await r.get_sender() if r else None
        elif ev.is_private:
            ent = await ev.get_chat()
    except Exception as e:  # noqa: BLE001
        logger.info("info resolve: %s", e)
    if not isinstance(ent, User):
        return "👤 روی پیام یه کاربر ریپلای کن و بنویس .اطلاعات (یا .اطلاعات @یوزرنیم | .اطلاعات آیدی‌عددی)", False
    about, common, blocked, photos = "", None, False, None
    try:
        fu = (await client(GetFullUserRequest(ent))).full_user
        about = (fu.about or "").strip()
        common = getattr(fu, "common_chats_count", None)
        blocked = bool(getattr(fu, "blocked", False))
    except Exception as e:  # noqa: BLE001
        logger.info("info full: %s", e)
    try:
        photos = (await client.get_profile_photos(ent, limit=0)).total
    except Exception:  # noqa: BLE001
        pass
    un = [("@" + u.username) for u in (getattr(ent, "usernames", None) or []) if getattr(u, "username", None)]
    if not un and getattr(ent, "username", None):
        un = ["@" + ent.username]
    flags = [t for t, ok in (("⭐ پرمیوم", getattr(ent, "premium", False)), ("🤖 ربات", ent.bot),
                             ("✔️ تأییدشده", getattr(ent, "verified", False)), ("⚠️ اسکم", getattr(ent, "scam", False)),
                             ("⚠️ فیک", getattr(ent, "fake", False)), ("🚫 محدود", getattr(ent, "restricted", False)),
                             ("👥 مخاطب", getattr(ent, "contact", False)), ("⛔ بلاک‌شده", blocked),
                             ("🗑 حذف‌شده", getattr(ent, "deleted", False))) if ok]
    lines = [f"👤 {_nm(ent)}", f"🆔 {ent.id}"]
    if not ent.bot:
        lines.append(age_line(ent.id))
    if un:
        lines.append("🔗 " + " ".join(un))
    if ent.phone:
        lines.append("📞 +" + str(ent.phone))
    if about:
        lines.append("📝 " + about[:200])
    lines.append("🕒 " + _status(ent.status))
    if common is not None:
        lines.append(f"👥 گروه مشترک: {common}")
    if photos is not None:
        lines.append(f"🖼 عکس پروفایل: {photos}")
    if flags:
        lines.append(" | ".join(flags))
    return "\n".join(lines), True


# ---------- .حساب من ----------
async def cmd_account(uid):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    me = await asyncio.wait_for(client.get_me(), 15)
    un = [("@" + u.username) for u in (getattr(me, "usernames", None) or []) if getattr(u, "username", None)]
    if not un and me.username:
        un = ["@" + me.username]
    lines = [f"👤 {_nm(me)}", f"🆔 {me.id}", age_line(me.id)]
    if un:
        lines.append("🔗 " + " ".join(un))
    if me.phone:
        lines.append("📞 +" + str(me.phone))
    lines.append("⭐ پرمیوم: " + ("بله" if getattr(me, "premium", False) else "خیر"))
    try:
        import extras
        from telethon.tl.functions.payments import GetStarsStatusRequest
        from telethon.tl.types import InputPeerSelf
        res = await asyncio.wait_for(client(GetStarsStatusRequest(peer=InputPeerSelf())), 15)
        lines.append("🌟 استارز: " + extras._fmt_stars(res.balance))
    except Exception as e:  # noqa: BLE001
        logger.info("account stars: %s", e)
    try:
        lines.append(f"💬 چت‌ها: {(await client.get_dialogs(limit=0)).total}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from telethon.tl.functions.contacts import GetContactsRequest
        c = await asyncio.wait_for(client(GetContactsRequest(hash=0)), 20)
        lines.append(f"👥 مخاطبین: {len(getattr(c, 'contacts', []) or [])}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from telethon.tl.functions.account import GetAuthorizationsRequest
        a = await asyncio.wait_for(client(GetAuthorizationsRequest()), 15)
        lines.append(f"📱 نشست‌های فعال: {len(a.authorizations)}")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines), True


# ---------- .اتو ریپ ----------
async def _ar_run(uid, chat, reply_id, text, n):
    client = sb._clients.get(uid)
    sent = 0
    try:
        for _ in range(n):
            if client is None or uid not in sb._clients:
                break
            sb._own[(uid, chat, sb.norm(text))] = time.time() + 15
            try:
                await client.send_message(chat, text, reply_to=reply_id)
                sent += 1
            except Exception as e:  # noqa: BLE001
                wait = getattr(e, "seconds", None)
                if isinstance(wait, int) and 0 < wait <= 60:
                    await asyncio.sleep(wait + 1)
                    continue
                logger.info("autorep stop %s: %s", uid, e)
                break
            await asyncio.sleep(AUTOREP_GAP)
    except asyncio.CancelledError:
        pass
    finally:
        _ar_tasks.pop(uid, None)
        try:
            await sb._botref[0].send_message(uid, f"🔁 اتو ریپ تموم شد ({sent} از {n}).")
        except Exception:  # noqa: BLE001
            pass


async def cmd_autorep(uid, ev, arg):
    a = re.sub(r"^(ریپ|rep|reply)\b\s*", "", (arg or "").strip(), flags=re.I)
    if sb.norm(a) in ("توقف", "stop", "خاموش", "لغو"):
        t = _ar_tasks.get(uid)
        if t:
            t.cancel()
            return "⏹ اتو ریپ متوقف شد.", False
        return "اتو ریپ فعالی نیست.", False
    m = re.match(r"^(.*\S)\s+([0-9۰-۹]+)$", a, re.S)
    if not m or not ev.is_reply:
        return ("🔁 روی پیام کاربر ریپلای کن و بنویس:\n.اتو ریپ سلام 50\n(حداکثر 100؛ توقف: .اتو ریپ توقف)", False)
    text = m.group(1).strip()[:300]
    if text.startswith((".", "/")):
        return "متن نباید با نقطه یا / شروع بشه.", False
    n = int(sb.fa2en(m.group(2)))
    capped = n > AUTOREP_MAX
    n = min(max(n, 1), AUTOREP_MAX)
    if uid in _ar_tasks:
        return "یه اتو ریپ در حال اجراست؛ اول .اتو ریپ توقف", False
    r = await ev.get_reply_message()
    if r is None:
        return "پیام ریپلای‌شده پیدا نشد.", False
    _ar_tasks[uid] = asyncio.create_task(_ar_run(uid, ev.chat_id, r.id, text, n))
    return (f"🔁 اتو ریپ شروع شد: «{text[:40]}» × {n}" + (" (سقف 100)" if capped else "")
            + f"\nفاصلهٔ هر پیام {AUTOREP_GAP} ثانیه؛ توقف: .اتو ریپ توقف"), False


# ---------- .خوشامد ----------
def _wc_get(uid):
    c = _wc_cache.get(uid)
    if c and time.time() - c[0] < 30:
        return c[1], c[2]
    d = sb._pget(uid)
    chats, text = d.get("wc_chats") or {}, d.get("wc_text") or WC_DEFAULT
    _wc_cache[uid] = (time.time(), chats, text)
    return chats, text


async def cmd_welcome(uid, ev, arg):
    a = re.sub(r"^(آمدگویی|آمد|آمدی)\b\s*", "", (arg or "").strip())
    d = sb._pget(uid)
    chats = d.get("wc_chats") or {}
    key = str(ev.chat_id)
    sub = sb.norm(a)
    out = None
    if sub in ("خاموش", "off", "غیرفعال"):
        chats.pop(key, None)
        out = "🔕 خوشامدگویی این گروه خاموش شد."
    elif sub.startswith("متن"):
        t = a[len("متن"):].strip()[:300]
        if not t:
            return "مثل: .خوشامد متن سلام {نام} خوش اومدی ❤️   ({نام} = منشن عضو جدید)", False
        d["wc_text"] = t
        out = "✅ متن خوشامد ذخیره شد:\n" + t
    elif sub in ("", "روشن", "on", "فعال"):
        if not ev.is_group:
            return "این دستور رو داخل گروه بنویس.", False
        chats[key] = True
        out = "🔔 خوشامدگویی این گروه روشن شد.\nمتن: " + (d.get("wc_text") or WC_DEFAULT) + "\nتغییر متن: .خوشامد متن ...   |   خاموش: .خوشامد خاموش"
    else:
        return "مثل: .خوشامد   |   .خوشامد متن سلام {نام}   |   .خوشامد خاموش", False
    d["wc_chats"] = chats
    sb._pput(uid, d)
    _wc_cache.pop(uid, None)
    return out, False


async def _welcome(uid, client, ev):
    if not (ev.user_joined or ev.user_added):
        return
    chats, text = _wc_get(uid)
    if str(ev.chat_id) not in chats:
        return
    now = time.time()
    if now - _wc_seen.get((uid, ev.chat_id), 0) < 3:
        return
    _wc_seen[(uid, ev.chat_id)] = now
    users = [u for u in (await ev.get_users() or []) if not getattr(u, "bot", False) and u.id != uid]
    if not users:
        return
    ments = "، ".join(f'<a href="tg://user?id={u.id}">{_html.escape(_nm(u), quote=False)}</a>' for u in users[:5])
    body = _html.escape(text, quote=False).replace("{نام}", ments)
    if "{نام}" not in text:
        body = body + "\n" + ments
    await asyncio.sleep(1.5)
    await client.send_message(ev.chat_id, body, parse_mode="html", link_preview=False)


def attach(uid, client):
    from telethon import events

    async def _h(ev):
        try:
            await _welcome(uid, client, ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.info("welcome %s: %s", uid, e)

    client.add_event_handler(_h, events.ChatAction)


# ---------- .کارت ----------
# کاراکترهای نامرئی (جهت‌نما، نیم‌فاصله‌ی صفر عرض و ...) که موقع کپی از اپ بانک‌ها قاطی شماره می‌شن
_INVIS = re.compile(r"[\u200b\u200d-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00ad\u0640]")
_CARD_RE = re.compile(r"(?<![0-9])(?:[0-9][\s\-./،,_]?){15}[0-9](?![0-9])")
_CARD_KEYS = r"(?:ذخیره|ثبت|set|save|add)"


def _luhn_ok(n):
    tot = 0
    for i, ch in enumerate(reversed(n)):
        v = int(ch)
        if i % 2:
            v *= 2
            if v > 9:
                v -= 9
        tot += v
    return tot % 10 == 0


def _card_parse(text):
    """متن آزاد → (۱۶ رقم، نام صاحب کارت). اگه شماره پیدا نشد (None, تعداد رقم‌های دیده‌شده)."""
    raw = sb.fa2en(_INVIS.sub("", text or ""))
    m = _CARD_RE.search(raw)
    if not m:
        return None, len(re.findall(r"[0-9]", raw))
    digits = re.sub(r"[^0-9]", "", m.group(0))
    owner = (raw[:m.start()] + " " + raw[m.end():]).strip()
    owner = re.sub(r"^(?:به[\s\u200c]*نام|بنام|نام|owner|name)\s*[:：]?\s*", "", owner, flags=re.I)
    return digits, " ".join(owner.split())[:40]


def _card_load(d):
    """→ (وضعیت، کارت): none = ذخیره نشده | bad = ذخیره شده ولی با کلید فعلی باز نمی‌شه | ok."""
    tok = d.get("card_enc")
    if not tok:
        return "none", None
    try:
        import json
        return "ok", json.loads(sb._fernet().decrypt(tok.encode()).decode())
    except Exception as e:  # noqa: BLE001
        logger.warning("card decrypt failed: %s", type(e).__name__)
        return "bad", None


def _card_save(uid, digits, owner):
    """ذخیره + خوندن دوباره برای اطمینان. → True اگه واقعاً ذخیره شد."""
    import json
    d = sb._pget(uid)
    d["card_enc"] = sb._fernet().encrypt(json.dumps({"n": digits, "o": owner}, ensure_ascii=False).encode()).decode()
    sb._pput(uid, d)
    st, c = _card_load(sb._pget(uid))
    return st == "ok" and bool(c) and c.get("n") == digits


async def cmd_card(uid, ev, arg):
    a = (arg or "").strip()
    sub = sb.norm(a)
    if sub in ("حذف", "پاک", "delete", "del"):
        d = sb._pget(uid)
        d.pop("card_enc", None)
        sb._pput(uid, d)
        return "🗑 کارت حذف شد.", False
    km = re.match(r"^" + _CARD_KEYS + r"\s*(.*)$", a, re.S | re.I)
    if a:
        # «.کارت ذخیره 6037 ... نام» یا مستقیم «.کارت 6037 ... نام»
        digits, owner = _card_parse(km.group(1) if km else a)
        if digits is None:
            seen = owner                      # وقتی شماره پیدا نشه، مقدار دوم تعداد رقم‌های دیده‌شده‌ست
            if not km and not seen:
                return "💳 .کارت (ارسال)   |   .کارت ذخیره 6037 9918 1234 5678 نام   |   .کارت حذف", False
            return (f"❌ شمارهٔ ۱۶ رقمی پیدا نشد (فقط {seen} رقم دیدم).\n"
                    "مثل: .کارت ذخیره 6037 9918 1234 5678 به‌نام فلانی"), False
        try:
            ok = _card_save(uid, digits, owner)
        except Exception as e:  # noqa: BLE001
            logger.warning("card save failed: %s", e)
            return "❌ ذخیره انجام نشد (خطای دیتابیس)؛ لاگ سرور رو ببین.", False
        if not ok:
            return "❌ کارت ذخیره نشد؛ بعد از نوشتن توی دیتابیس دوباره نخوندمش. لاگ سرور رو ببین.", False
        warn = "" if _luhn_ok(digits) else "\n⚠️ این شماره از چک اعتبار کارت رد نشد؛ یه بار دوباره بررسیش کن."
        return "✅ کارت ذخیره شد (رمزنگاری‌شده). برای ارسال: .کارت" + warn, False
    st, c = _card_load(sb._pget(uid))
    if st == "bad":
        return ("⚠️ کارتی ذخیره شده ولی با کلید فعلی باز نمی‌شه (SELF_KEY یا BOT_TOKEN عوض شده).\n"
                "دوباره ذخیره کن: .کارت ذخیره 6037 ... نام"), False
    if not c:
        return "کارتی ذخیره نشده؛ بنویس: .کارت ذخیره 6037 9918 1234 5678 به‌نام فلانی", False
    n = c["n"]
    lines = ["💳 شماره کارت:", " ".join(n[i:i + 4] for i in range(0, 16, 4))]
    if c.get("o"):
        lines.append("👤 " + c["o"])
    return "\n".join(lines), True


# ---------- .ویدیو مسیج ----------
def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


async def cmd_vnote(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    mm = re.match(r"^\s*(?:میم|meme)\b\s*(.*)$", arg or "", re.S | re.I)
    if mm:                                   # «.ویدیو میم بالا | پایین» = «.میم بالا | پایین»
        import selfmedia
        return await selfmedia.run(uid, ev, "meme", mm.group(1))
    if sb.norm(arg) not in ("", "مسیج", "message", "msg", "note"):
        return "🎥 روی ویدیو یا گیف ریپلای کن و بنویس: .ویدیو مسیج", False
    r = await ev.get_reply_message() if ev.is_reply else None
    mime = (getattr(getattr(r, "file", None), "mime_type", "") or "") if r else ""
    if r is None or not (getattr(r, "video", None) or getattr(r, "gif", None) or mime.startswith("video/")):
        return "🎥 روی یه ویدیو یا گیف ریپلای کن و بنویس: .ویدیو مسیج", False
    size = getattr(getattr(r, "file", None), "size", 0) or 0
    if size > 60 * 1024 * 1024:
        return "ویدیو بزرگ‌تر از 60 مگابایته؛ یه ویدیوی کوچیک‌تر انتخاب کن.", False
    ff = _ffmpeg()
    if not ff:
        return "❌ ffmpeg نصب نیست؛ imageio-ffmpeg رو به requirements.txt اضافه کن (توی این نسخه اضافه شده) و دوباره دیپلوی کن.", False
    try:
        await ev.edit(sb.fancy_html("⏳ در حال تبدیل به ویدیو مسیج..."), parse_mode="html")
    except Exception:  # noqa: BLE001
        pass
    from telethon.tl.types import DocumentAttributeVideo
    with tempfile.TemporaryDirectory() as td:
        src = await asyncio.wait_for(client.download_media(r, file=os.path.join(td, "in")), 120)
        if not src:
            return "❌ دانلود ویدیو انجام نشد.", False
        out = os.path.join(td, "note.mp4")
        cmd = [ff, "-y", "-i", src, "-t", "60",
               "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale=384:384,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-maxrate", "1200k", "-bufsize", "2400k",
               "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), 240)
        except asyncio.TimeoutError:
            proc.kill()
            return "❌ تبدیل بیش از حد طول کشید.", False
        err = (err or b"").decode("utf-8", "ignore")
        if proc.returncode != 0 or not os.path.exists(out):
            logger.info("vnote ffmpeg: %s", err[-300:])
            return "❌ تبدیل انجام نشد (فرمت ویدیو پشتیبانی نمی‌شه).", False
        times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        dur = int(int(times[-1][0]) * 3600 + int(times[-1][1]) * 60 + float(times[-1][2])) if times else 0
        await client.send_file(ev.chat_id, out, video_note=True, reply_to=r.id, supports_streaming=True,
                               attributes=[DocumentAttributeVideo(duration=max(dur, 1), w=384, h=384, round_message=True)])
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    return None, False


# ---------- .عضویت همه ----------
_INV = re.compile(r"(?:(?:t|telegram)\.me/(?:\+|joinchat/)|tg://join\?invite=)([\w-]+)", re.I)
_USR = re.compile(r"(?:(?:t|telegram)\.me/|tg://resolve\?domain=)([A-Za-z]\w{3,31})(?:[/?&]|$)", re.I)


async def _join_one(client, url):
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
    from telethon.tl.types import User
    m = _INV.search(url)
    if m:
        try:
            await client(ImportChatInviteRequest(m.group(1)))
            return "ok"
        except Exception as e:  # noqa: BLE001
            n = type(e).__name__
            if n == "UserAlreadyParticipantError":
                return "ok"
            if n == "InviteRequestSentError":
                return "درخواست عضویت فرستاده شد"
            raise
    m = _USR.search(url)
    if not m or m.group(1).lower() in ("addlist", "boost", "joinchat", "c", "share", "proxy"):
        return "لینک پشتیبانی نمی‌شه"
    ent = await client.get_entity(m.group(1))
    if isinstance(ent, User):
        return "ربات/کاربره، نه کانال"
    await client(JoinChannelRequest(ent))
    return "ok"


async def cmd_joinall(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    if sb.norm(arg) not in ("همه", "all"):
        return "📢 روی پیام ربات عضویت اجباری (دکمه‌دار) ریپلای کن و بنویس: .عضویت همه", False
    r = await ev.get_reply_message() if ev.is_reply else None
    if r is None or not r.buttons:
        return "📢 روی پیام ربات عضویت اجباری (همونی که دکمه داره) ریپلای کن و بنویس: .عضویت همه", False
    flat = [b for row in r.buttons for b in row]
    urls, seen = [], set()
    for b in flat:
        u = getattr(b, "url", None)
        if u and u not in seen:
            seen.add(u)
            urls.append((b.text, u))
    urls = urls[:JOIN_MAX]
    cbs = [b for b in flat if getattr(b, "data", None) is not None]
    if not urls:
        return "دکمه‌ای با لینک کانال توی این پیام نیست.", False
    try:
        await ev.edit(sb.fancy_html(f"⏳ در حال عضویت در {len(urls)} کانال..."), parse_mode="html")
    except Exception:  # noqa: BLE001
        pass
    ok, bad = 0, []
    for i, (label, u) in enumerate(urls):
        try:
            res = await asyncio.wait_for(_join_one(client, u), 30)
            if res == "ok":
                ok += 1
            else:
                bad.append(f"{(label or u)[:25]}: {res}")
        except Exception as e:  # noqa: BLE001
            wait = getattr(e, "seconds", None)
            if isinstance(wait, int):
                bad.append(f"{(label or u)[:25]}: فلود تلگرام، {wait} ثانیه صبر لازمه")
                break
            bad.append(f"{(label or u)[:25]}: {type(e).__name__}")
        if i < len(urls) - 1:
            await asyncio.sleep(JOIN_GAP)
    lines = [f"📢 عضویت همه: {ok} از {len(urls)} موفق"]
    lines += ["⚠️ " + x for x in bad[:6]]
    btn = None
    for b in cbs:
        t = sb.norm(b.text or "")
        if any(sb.norm(k) in t for k in CONFIRM_WORDS):
            btn = b
            break
    btn = btn or (cbs[-1] if cbs else None)
    if btn is None:
        lines.append("🔘 دکمهٔ تأیید (غیر لینک) پیدا نشد؛ خودت تأیید رو بزن.")
        return "\n".join(lines), True
    lines.append(f"🔘 دکمهٔ تأیید: «{(btn.text or '')[:30]}»")
    await asyncio.sleep(2)
    answer = ""
    try:
        res = await asyncio.wait_for(btn.click(), 25)
        answer = (getattr(res, "message", "") or "").strip()
    except Exception as e:  # noqa: BLE001
        lines.append(f"⚠️ زدن دکمه انجام نشد: {type(e).__name__}")
    await asyncio.sleep(2.5)
    reply = ""
    try:
        m2 = await client.get_messages(r.chat_id, ids=r.id)
        if m2 and (m2.raw_text or "") != (r.raw_text or ""):
            reply = (m2.raw_text or "").strip()
        if not reply:
            async for m in client.iter_messages(r.chat_id, min_id=r.id, limit=5):
                if m.sender_id == r.sender_id and (m.raw_text or "").strip():
                    reply = m.raw_text.strip()
                    break
    except Exception as e:  # noqa: BLE001
        logger.info("joinall result: %s", e)
    if answer:
        lines.append("💬 پاسخ دکمه: " + answer[:300])
    if reply:
        lines.append("📩 پیام ربات: " + reply[:400])
    if not answer and not reply:
        lines.append("ℹ️ ربات پاسخ مشخصی نداد؛ خودت پیام ربات رو چک کن.")
    return "\n".join(lines), True


async def run(uid, ev, cmd, arg):
    if cmd == "info":
        return await cmd_info(uid, ev, arg)
    if cmd == "autorep":
        return await cmd_autorep(uid, ev, arg)
    if cmd == "welcome":
        return await cmd_welcome(uid, ev, arg)
    if cmd == "card":
        return await cmd_card(uid, ev, arg)
    if cmd == "vnote":
        return await cmd_vnote(uid, ev, arg)
    if cmd == "joinall":
        return await cmd_joinall(uid, ev, arg)
    return None, False
