# -*- coding: utf-8 -*-
"""
قابلیت «سلف» روبی.

- پشتیبانی از پنل مدیریت (🤖 سلف) با آیدی عددی کاربر، سلف رو برای اون کاربر فعال/غیرفعال می‌کنه.
- کاربرِ فعال‌شده با /self (یا نوشتن «سلف») اکانتش رو وصل می‌کنه و تا ۳ کلمه تنظیم می‌کنه:
  هر کلمه = (کلمه، گپ، تایمر، جای دکمه: چپ/وسط/راست/خالی).
- دستورهای نقطه‌ای داخل چت از اکانت کاربر: .پنل  .کلمه فلان  .زمان فلان  .دکمه فلان  .گپ [اسم گروه]
- حالت خودکار: اسلاتی که کلمه + گپ + تایمر (>= ۵ ثانیه) داره، هر تایمر ثانیه خودش کلمه رو توی گپ می‌گه و دکمه‌ی انتخابی رو می‌زنه.
- (حالت دستی قدیمی، فقط اگه تایمر صفر باشه) وقتی کاربر از اکانت خودش دقیقاً اون کلمه رو توی همون گپ بفرسته، بعد از تایمر، سلف روی دکمه‌ی انتخاب‌شده‌ی
  پیام ربات (ریپلای یا آخرین پیام دکمه‌دار ربات‌های دیگه) کلیک می‌کنه. روی ربات روباهیو هیچ‌وقت کلیک نمی‌کنه.

- ساعت زنده کنار اسم: .ساعت (روشن) | .ساعت 3 (انتخاب فونت) | .ساعت فونت (لیست) | .غیرفعال ساعت
- ایموجی پریمیوم (بدون اکانت پریمیوم، از طریق ربات اینلاین): .ایموجی متن [کد ایموجی]
- ریاکشن: روی پیام کاربر ریپلای کن و بنویس .ریاکشن 🔥❤👍  یا  /reykt  (اکانت پریمیوم تا ۳ ریاکشن روی همون پیام)
- کپی پروفایل: روی پیام یه کاربر ریپلای کن و بنویس .کپی  (اسم، بیو، عکس) | برگشت: .غیرفعال کپی

امنیت: سشن‌ها رمزنگاری‌شده ذخیره می‌شن (Fernet)، سلف هیچ‌وقت روی ربات روباهیو کلیک نمی‌کنه،
محدودیت سرعت داره و کاربر هر لحظه می‌تونه اتصال رو قطع کنه (خروج کامل از سشن).

متغیرهای Railway: SELF_API_ID و SELF_API_HASH (از my.telegram.org)، اختیاری: SELF_KEY.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text
from telegram import InlineKeyboardButton as _IKB, InlineKeyboardMarkup as KB, KeyboardButton as _KB, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram import InlineQueryResultArticle, InlineQueryResultCachedPhoto, InlineQueryResultPhoto, InputTextMessageContent, MessageEntity
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, InlineQueryHandler, MessageHandler, filters

from database import Base, get_session
import selffonts


# ---------- دکمه‌های رنگی (بدون ایموجی) ----------
# Bot API 9.4: style = primary (آبی) | success (سبز) | danger (قرمز). روی تلگرام قدیمی بدون رنگ دیده می‌شه.
_EMO = re.compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]")
_BTN_MAP = {"⌫": "حذف", "◀️": "قبلی", "▶️": "بعدی", "🔙": "بازگشت", "✔️ ": "» "}


def _clean(t):
    t = t or ""
    if t in _BTN_MAP:
        return _BTN_MAP[t]
    t = t.replace("✔️ ", "» ")
    t = _EMO.sub("", t)
    return re.sub(r"\s+", " ", t).strip() or "•"


def _style_for(text, cd):
    cd = cd or ""
    t = text or ""
    if cd in ("self:disc", "self:kc", "selfa:off", "selfa:del", "self:discy") or cd.startswith("self:d:") \
            or any(w in t for w in ("لغو", "انصراف", "قطع", "توقف", "خاموش", "حذف")) and cd != "self:kx":
        return "danger"
    if cd in ("self:ky", "self:conn", "selfa:on", "self:tog", "self:clkt") or any(w in t for w in ("فعال", "روشن", "تأیید", "اتصال")):
        return "success"
    return "primary"


def B(text, callback_data=None, style=None, **kw):
    txt = _clean(text)
    return _IKB(txt, callback_data=callback_data, api_kwargs={"style": style or _style_for(text, callback_data)}, **kw)


def KeyboardButton(text, **kw):
    return _KB(_clean(text), api_kwargs={"style": "danger" if "لغو" in text else "primary"}, **kw)


logger = logging.getLogger(__name__)

MAX_SLOTS = 3
MAX_DELAY = 3600
MIN_AUTO = 5      # تایمر >= این مقدار یعنی حالت خودکار: هر N ثانیه کلمه رو خودش توی گپ می‌گه
POS = {"l": "⬅️ چپ", "m": "⏺ وسط", "r": "➡️ راست", "n": "⬜ خالی"}
API_ID = int(os.environ.get("SELF_API_ID", "0") or 0)
API_HASH = os.environ.get("SELF_API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()


class SelfAccess(Base):
    __tablename__ = "self_access"
    user_id = Column(BigInteger, primary_key=True)
    enabled = Column(Boolean, default=True)
    paused = Column(Boolean, default=False)
    phone = Column(String, nullable=True)
    session_enc = Column(Text, nullable=True)
    slots = Column(Text, default="[]")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SelfProfile(Base):
    """وضعیت ساعت و کپی پروفایل هر کاربر (JSON)."""
    __tablename__ = "self_profile"
    user_id = Column(BigInteger, primary_key=True)
    data = Column(Text, default="{}")


class SelfWork(Base):
    """قابلیت «کار»: مجوز (فقط پشتیبانی/مدیر می‌دهد) + وضعیت روشن/خاموش + تنها گپِ فعال."""
    __tablename__ = "self_work"
    user_id = Column(BigInteger, primary_key=True)
    allowed = Column(Boolean, default=False)
    active = Column(Boolean, default=False)
    chat = Column(BigInteger, nullable=True)
    title = Column(String, nullable=True)


class SelfLesson(Base):
    """قابلیت «درس» (همون ساختار SelfWork)."""
    __tablename__ = "self_lesson"
    user_id = Column(BigInteger, primary_key=True)
    allowed = Column(Boolean, default=False)
    active = Column(Boolean, default=False)
    chat = Column(BigInteger, nullable=True)
    title = Column(String, nullable=True)


# ---------- ابزارها ----------
_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa2en(s):
    return (s or "").translate(_FA)


def norm(s):
    return re.sub(r"[\u200c\u200d\ufe0f\s]+", " ", s or "").strip().lower()


def _fernet():
    from cryptography.fernet import Fernet
    raw = os.environ.get("SELF_KEY", "").strip() or ("rubahio-self:" + BOT_TOKEN)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def enc(s):
    return _fernet().encrypt(s.encode()).decode()


def dec(s):
    return _fernet().decrypt(s.encode()).decode()


def configured():
    return bool(API_ID and API_HASH)


def get_row(uid):
    s = get_session()
    try:
        r = s.get(SelfAccess, uid)
        if r:
            s.expunge(r)
        return r
    finally:
        s.close()


def save_row(uid, **kw):
    s = get_session()
    try:
        r = s.get(SelfAccess, uid)
        if not r:
            r = SelfAccess(user_id=uid, enabled=True, paused=False, slots="[]")
            s.add(r)
        for k, v in kw.items():
            setattr(r, k, v)
        s.commit()
    finally:
        s.close()


# کلمه‌هایی که هیچ‌کس نمی‌تونه برای اسلات تنظیم کنه (فاصله، ریزه‌کاری نوشتاری و حروف تکراری هم دور زدنش نمی‌شه)
_BANNED_RAW = ("روب روب", "شکار", "هور هور", "عو عو", "روباه")
_BAN_TR = str.maketrans("يكۀةىئؤأإآ", "یکههیییااا")


def _squash(t):
    t = (t or "").translate(_BAN_TR).lower()
    t = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", t)      # اعراب و کشیده
    t = re.sub(r"[^\w]|_", "", t)                              # فاصله، نیم‌فاصله، علامت‌ها
    return re.sub(r"(.)\1+", r"\1", t)                         # روووب → روب


_BANNED = tuple(_squash(w) for w in _BANNED_RAW)


def is_banned_word(text):
    q = _squash(text)
    return bool(q) and any(b in q for b in _BANNED)


def slots_of(row):
    try:
        v = json.loads(row.slots or "[]")
        out = [x for x in v if isinstance(x, dict)][:MAX_SLOTS]
        for x in out:      # کلمهٔ ممنوعه‌ای که قبلاً ذخیره شده هم کار نمی‌کنه و از پنل پاک می‌شه
            if is_banned_word(x.get("word")):
                x["word"] = ""
        return out
    except Exception:
        return []


def parse_delay(text):
    m = re.match(r"^(\d{1,5})\s*(s|m|ثانیه|دقیقه)?$", fa2en(text).strip().lower())
    if not m:
        return None
    n = int(m.group(1)) * (60 if m.group(2) in ("m", "دقیقه") else 1)
    return n if 0 <= n <= MAX_DELAY else None


def fmt_delay(n):
    n = int(n or 0)
    return f"{n // 60} دقیقه" if n >= 60 and n % 60 == 0 else f"{n} ثانیه"


# ---------- هسته‌ی سلف (Telethon) ----------
_clients = {}
_slots = {}
_paused = {}
_hist = {}
_last = {}
_login = {}
_bot_id = [0]
_capture = {}
_botref = [None]
_own = {}   # (uid, chat, کلمه) -> زمان؛ پیام‌هایی که خود سلف می‌فرسته و نباید دوباره تریگر بشن
_feature_tasks = {}  # uid -> asyncio tasks for presence/typing helpers
_typing_emit_at = {}


def _load_runtime(uid, row):
    _slots[uid] = slots_of(row)
    _paused[uid] = bool(row.paused)
    _sync_auto(uid)


async def stop_client(uid, logout=False, restore=False):
    c = _clients.pop(uid, None)
    _cancel_auto(uid)
    _cancel_feat(uid)
    _cancel_clock(uid)
    for task in _feature_tasks.pop(uid, []):
        task.cancel()
    _typing_emit_at.pop(uid, None)
    try:
        import extras
        extras.cleanup(uid)
    except Exception:  # noqa: BLE001
        pass
    try:
        import selfmodes
        selfmodes.cleanup(uid)
    except Exception:  # noqa: BLE001
        pass
    _slots.pop(uid, None)
    if c and restore:
        try:
            await _reset_profile(uid, c)
        except Exception as e:  # noqa: BLE001
            logger.info("self reset profile %s: %s", uid, e)
    if c:
        try:
            if logout:
                await c.log_out()
            else:
                await c.disconnect()
        except Exception:
            try:
                await c.disconnect()
            except Exception:
                pass


async def start_client(uid):
    if not configured():
        return False
    from telethon import TelegramClient, events
    from telethon.sessions import StringSession
    row = get_row(uid)
    if not (row and row.enabled and row.session_enc):
        return False
    await stop_client(uid)
    from telethon import errors as _te
    dead = tuple(getattr(_te, n) for n in ("AuthKeyDuplicatedError", "AuthKeyUnregisteredError", "SessionRevokedError",
                                           "SessionExpiredError", "UserDeactivatedError", "UserDeactivatedBanError")
                 if hasattr(_te, n))
    client = None
    try:
        client = TelegramClient(StringSession(dec(row.session_enc)), API_ID, API_HASH)
        await client.connect()
        me = await client.get_me()
        if me is None:
            raise _te.AuthKeyUnregisteredError(None)
    except dead as e:
        # فقط خطاهای قطعیِ «نشست باطل شده» نشست رو پاک می‌کنن (مثلاً وقتی دو سرور همزمان با یک نشست وصل شدن)
        logger.warning("self session dead %s: %s %s", uid, type(e).__name__, e)
        save_row(uid, session_enc=None)
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        try:
            await _botref[0].send_message(uid, "⚠️ نشست اکانتت از سمت تلگرام باطل شد و باید دوباره وصل کنی (/self ← اتصال اکانت).")
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception as e:  # noqa: BLE001
        # خطای موقت (شبکه، فلود، ...): نشست دست‌نخورده می‌مونه و بعداً دوباره تلاش می‌شه
        logger.warning("self start failed (session kept) %s: %s %s", uid, type(e).__name__, e)
        try:
            if client:
                await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return False
    _load_runtime(uid, row)

    async def _h(ev, _uid=uid):
        await _on_out(_uid, ev)
    client.add_event_handler(_h, events.NewMessage(outgoing=True))

    async def _pv_guard(ev, _uid=uid, _client=client):
        # کنترل‌های پیوی فقط روی پیام‌های ورودیِ پیوی اعمال می‌شوند.
        try:
            d = _pget(_uid)
            if not ev.is_private or ev.sender_id == _uid:
                return
            if d.get("pv_lock"):
                await ev.delete()
                return
            if d.get("pv_join"):
                channels = [x.strip().lstrip("@") for x in os.environ.get("SELF_PV_JOIN_CHANNELS", "selfsaz_iran,fox_frenzy").split(",") if x.strip()]
                missing = []
                for channel in channels[:5]:
                    try:
                        ent = await _client.get_entity(channel)
                        await _client.get_permissions(ent, ev.sender_id)
                    except Exception:
                        missing.append(channel)
                if missing:
                    links = "\n".join("https://t.me/" + x for x in missing)
                    await ev.reply("برای گفتگو، اول عضو کانال‌های زیر شو و دوباره پیام بده:\n" + links)
                    await ev.delete()
        except Exception as exc:
            logger.info("private guard %s: %s", _uid, exc)

    client.add_event_handler(_pv_guard, events.NewMessage(incoming=True))
    async def _raw_typing(update, _uid=uid, _client=client):
        await _mirror_typing(_uid, _client, update)
    client.add_event_handler(_raw_typing, events.Raw)
    try:
        import extras
        extras.attach(uid, client)
    except Exception as e:  # noqa: BLE001
        logger.warning("extras attach %s: %s", uid, e)
    try:
        import selfmodes
        selfmodes.attach(uid, client)
    except Exception as e:  # noqa: BLE001
        logger.warning("selfmodes attach %s: %s", uid, e)
    _clients[uid] = client
    _sync_auto(uid)
    _sync_feat(uid)
    _sync_clock(uid)
    _sync_presence(uid)
    return True


async def _on_out(uid, ev):
    cap = _capture.get(uid)
    if cap:
        if time.time() - cap["ts"] > 300:
            _capture.pop(uid, None)
        elif ev.chat_id == cap["chat"] and (ev.raw_text or "").strip():
            _capture.pop(uid, None)
            if is_banned_word(ev.raw_text):
                try:
                    await _botref[0].send_message(uid, "⛔ این کلمه مجاز نیست و برای اسلات ثبت نشد. یه کلمهٔ دیگه انتخاب کن.")
                except Exception as e:  # noqa: BLE001
                    logger.info("self banned notify failed: %s", e)
                return
            sl = _get_slots(uid)
            sl[cap["slot"]]["word"] = (ev.raw_text or "").strip()[:40]
            sl[cap["slot"]].setdefault("delay", 0)
            _put_slots(uid, sl)
            try:
                tx, kb = _slot_panel(uid, cap["slot"])
                await _botref[0].send_message(uid, f"✅ کلمه خودکار ثبت شد: «{sl[cap['slot']]['word']}»\n\n" + tx,
                                              reply_markup=kb, parse_mode="HTML")
            except Exception as e:  # noqa: BLE001
                logger.info("self capture notify failed: %s", e)
            return
    if await _handle_dot(uid, ev):
        return
    # فونت «حالت متن» (+ افکت تایپ حرف‌به‌حرف) روی پیام‌های معمولی؛ دستورها (با نقطه) دست نمی‌خورن.
    dfont = _pget(uid)
    mode = dfont.get("font_mode", "ساده")
    fx = bool(dfont.get("type_fx"))
    original = (ev.raw_text or "")
    if ((mode != "ساده" and mode in selffonts.FONTS) or fx) and original and not ev.media \
            and not original.lstrip().startswith((".", "/")) and not _keeps_entities(ev):
        try:
            if fx:
                # انیمیشن توی تسک جدا اجرا می‌شه تا بقیهٔ کارها (کلمه‌های خودکار) معطل نشن
                asyncio.create_task(selffonts.apply(uid, ev, original, mode, True, dfont.get("type_speed", 2)))
            else:
                await selffonts.apply(uid, ev, original, mode, False)
        except Exception as e:
            logger.debug("font transform skipped %s: %s", uid, e)
    if _paused.get(uid):
        return
    txt = norm(ev.raw_text or "")
    if not txt:
        return
    if _own.pop((uid, ev.chat_id, txt), 0) > time.time():
        return   # پیامی که خود سلف فرستاده؛ دوباره تریگر نشه
    for i, s in enumerate(_slots.get(uid, [])):
        if s and s.get("word") and s.get("chat") and ev.chat_id == s["chat"] and ev.chat_id != _bot_id[0] and norm(s["word"]) == txt and int(s.get("delay", 0) or 0) < MIN_AUTO:
            now = time.time()
            if now - _last.get((uid, i), 0) < 2:
                continue
            h = [t for t in _hist.get(uid, []) if now - t < 60]
            if len(h) >= 20:
                return
            h.append(now)
            _hist[uid] = h
            _last[(uid, i)] = now
            asyncio.create_task(_fire(uid, ev, s))


def _keeps_entities(ev):
    """پیامی که ایموجی پرمیوم یا لینک متنی داره رو دست نمی‌زنیم (ادیت با متن خام اون‌ها رو پاک می‌کنه)."""
    try:
        for e in (getattr(ev.message, "entities", None) or []):
            if type(e).__name__ in ("MessageEntityCustomEmoji", "MessageEntityTextUrl"):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _pick_button(msg, pos):
    rows = [r for r in (msg.buttons or []) if r]
    if not rows or pos == "n":
        return None
    row = rows[0]
    if pos == "l":
        return row[0]
    if pos == "r":
        return row[-1]
    if len(row) == 1:
        return row[0]
    return row[len(row) // 2] if len(row) % 2 == 1 else None


async def _usable(m):
    """پیام دکمه‌دار از یک ربات؛ هیچ‌وقت ربات روباهیو."""
    if not m.buttons or m.sender_id == _bot_id[0]:
        return False
    snd = await m.get_sender()
    return bool(snd and getattr(snd, "bot", False))


async def _fire(uid, ev, s):
    try:
        await asyncio.sleep(max(0, int(s.get("delay", 0))))
        client = _clients.get(uid)
        if not client or _paused.get(uid) or ev.chat_id == _bot_id[0]:
            return
        pos = s.get("pos", "n")
        # سر تایمر کلمه رو توی همون گپ می‌گه (ریپلای هم باشه، ریپلای می‌کنه)
        word = (s.get("word") or "").strip()
        if word:
            _own[(uid, ev.chat_id, norm(word))] = time.time() + 15
            await client.send_message(ev.chat_id, word, reply_to=ev.reply_to_msg_id if ev.is_reply else None)
        if pos == "n":
            return
        target = None
        if ev.is_reply:
            m = await ev.get_reply_message()
            if m and await _usable(m):
                target = m
        if not target:
            async for m in client.iter_messages(ev.chat_id, limit=15):
                if m.buttons and await _usable(m):
                    target = m
                    break
        btn = _pick_button(target, pos) if target else None
        if btn:
            await btn.click()
    except Exception as e:  # noqa: BLE001
        logger.info("self fire failed %s: %s", uid, e)


# ---------- حالت خودکار: هر N ثانیه کلمه رو خودش توی گپ می‌گه و دکمه رو می‌زنه ----------
_auto = {}   # (uid, اسلات) -> (امضا، تسک)


def _auto_sig(s):
    return (s.get("word"), s.get("chat"), int(s.get("delay", 0) or 0), s.get("pos", "n"))


def _cancel_auto(uid):
    for k in [k for k in _auto if k[0] == uid]:
        try:
            _auto.pop(k)[1].cancel()
        except Exception:
            pass


def _sync_auto(uid):
    """تسک‌های خودکار رو با تنظیمات فعلی هماهنگ می‌کنه (تغییر تنظیمات = ری‌استارت همون اسلات)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    sl = _slots.get(uid, []) if uid in _clients else []
    want = {}
    for i, s in enumerate(sl):
        if s and s.get("word") and s.get("chat") and int(s.get("delay", 0) or 0) >= MIN_AUTO:
            want[(uid, i)] = _auto_sig(s)
    for k in [k for k in _auto if k[0] == uid and (k not in want or _auto[k][0] != want[k])]:
        _auto.pop(k)[1].cancel()
    for k, sig in want.items():
        if k not in _auto:
            _auto[k] = (sig, loop.create_task(_auto_loop(uid, k[1], sig)))


async def _auto_loop(uid, i, sig):
    delay = sig[2]
    try:
        while True:
            await asyncio.sleep(delay)
            client = _clients.get(uid)
            sl = _slots.get(uid, [])
            cur = sl[i] if i < len(sl) else None
            if not client or not cur or _auto_sig(cur) != sig:
                return
            if _paused.get(uid):
                continue
            now = time.time()
            h = [t for t in _hist.get(uid, []) if now - t < 60]
            if len(h) >= 20:
                continue
            h.append(now)
            _hist[uid] = h
            try:
                await _auto_fire(uid, client, cur)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.info("self auto fire %s/%s: %s", uid, i, e)
                wait = getattr(e, "seconds", None)
                if isinstance(wait, int) and wait > 0:
                    await asyncio.sleep(min(wait, 300))
    except asyncio.CancelledError:
        raise


async def _auto_fire(uid, client, s):
    chat = s["chat"]
    word = (s.get("word") or "").strip()
    if not word or chat == _bot_id[0]:
        return
    _own[(uid, chat, norm(word))] = time.time() + 15
    sent = await client.send_message(chat, word)
    pos = s.get("pos", "n")
    if pos == "n":
        return
    target = None
    for _ in range(4):                      # جواب ربات رو چند ثانیه صبر می‌کنه
        await asyncio.sleep(1.5)
        async for m in client.iter_messages(chat, limit=10):
            if m.id <= sent.id:
                break
            if m.buttons and await _usable(m):
                target = m
                break
        if target:
            break
    if not target:                          # جواب تازه نیومد؛ آخرین پیام دکمه‌دار رباتا
        async for m in client.iter_messages(chat, limit=15):
            if m.buttons and await _usable(m):
                target = m
                break
    btn = _pick_button(target, pos) if target else None
    if btn:
        await btn.click()


# ---------- قابلیت‌های زمان‌بندی‌شده «کار» و «درس» ----------
# مجوز فقط از پنل پشتیبانی/مدیر داده می‌شه. روشن‌کردن توی گپ: «.کار روشن» / «.درس روشن» (هرکدوم فقط توی یک گپ).
#   کار : هر ۱۲ دقیقه و ۱۰ ثانیه «کار» می‌فرسته و دکمهٔ وسط رو می‌زنه.
#   درس : هر ۷ دقیقه «روباهیو درس» می‌فرسته، دکمهٔ «ریاضی» رو می‌زنه و بعدش دکمهٔ دوم رو.
LESSON_WORD = "درس"
LESSON_TOPIC = "ریاضی"
FEATS = {
    "work": {"model": SelfWork, "label": "کار", "word": "کار", "interval": 730.0},
    "lesson": {"model": SelfLesson, "label": "درس", "word": LESSON_WORD, "interval": 420.0},
}
_feat_tasks = {}   # (uid, kind) -> تسک


def feat_get(uid, kind):
    s = get_session()
    try:
        r = s.get(FEATS[kind]["model"], uid)
        if not r:
            return {"allowed": False, "active": False, "chat": None, "title": None}
        return {"allowed": bool(r.allowed), "active": bool(r.active), "chat": r.chat, "title": r.title}
    finally:
        s.close()


def feat_save(uid, kind, **kw):
    M = FEATS[kind]["model"]
    s = get_session()
    try:
        r = s.get(M, uid)
        if not r:
            r = M(user_id=uid, allowed=False, active=False)
            s.add(r)
        for k, v in kw.items():
            setattr(r, k, v)
        s.commit()
    finally:
        s.close()


def feat_set_allowed(uid, kind, allowed):
    """فقط پنل پشتیبانی/مدیر صدا می‌زنه. با گرفتن مجوز، قابلیت هم خاموش می‌شه."""
    if allowed:
        feat_save(uid, kind, allowed=True)
    else:
        feat_save(uid, kind, allowed=False, active=False, chat=None, title=None)
    _sync_feat(uid, kind)


def _cancel_feat(uid, kind=None):
    for k in ([kind] if kind else list(FEATS)):
        t = _feat_tasks.pop((uid, k), None)
        if t:
            try:
                t.cancel()
            except Exception:
                pass


def _sync_feat(uid, kind=None):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for k in ([kind] if kind else list(FEATS)):
        w = feat_get(uid, k)
        if not (uid in _clients and w["allowed"] and w["active"] and w["chat"]):
            _cancel_feat(uid, k)
            continue
        t = _feat_tasks.get((uid, k))
        if t and not t.done():
            continue
        _feat_tasks[(uid, k)] = loop.create_task(_feat_loop(uid, k))


def _btn_texts(m):
    return [b for row in (m.buttons or []) for b in row]


async def _lesson_fire(uid, client, chat):
    """«روباهیو درس» رو می‌گه، دکمهٔ «ریاضی» رو می‌زنه، بعد دکمهٔ دوم رو."""
    _own[(uid, chat, norm(LESSON_WORD))] = time.time() + 15
    sent = await client.send_message(chat, LESSON_WORD)
    target = btn = None
    for _ in range(6):                      # جواب ربات رو چند ثانیه صبر می‌کنه
        await asyncio.sleep(1.5)
        async for m in client.iter_messages(chat, limit=10):
            if m.id <= sent.id:
                break
            if m.buttons and await _usable(m):
                btn = next((b for b in _btn_texts(m) if LESSON_TOPIC in (b.text or "")), None)
                if btn:
                    target = m
                    break
        if btn:
            break
    if not btn:
        logger.info("self lesson %s: دکمهٔ «%s» پیدا نشد", uid, LESSON_TOPIC)
        return
    menu = [b.text for b in _btn_texts(target)]
    await btn.click()
    for _ in range(6):                      # سؤال/گزینه‌ها: ادیتِ همون پیام یا پیام تازهٔ ربات
        await asyncio.sleep(1.5)
        cands = [await client.get_messages(chat, ids=target.id)]
        async for m in client.iter_messages(chat, limit=6):
            if m.id > target.id:
                cands.append(m)
        for m in cands:
            if not m or not m.buttons or not await _usable(m):
                continue
            bts = _btn_texts(m)
            if [b.text for b in bts] == menu or len(bts) < 2:
                continue                    # هنوز همون منوی موضوع‌هاست
            await bts[1].click()
            return
    logger.info("self lesson %s: دکمهٔ دوم پیدا نشد", uid)


async def _feat_fire(uid, client, kind, chat):
    if kind == "lesson":
        await _lesson_fire(uid, client, chat)
    else:
        await _auto_fire(uid, client, {"chat": chat, "word": FEATS[kind]["word"], "pos": "m"})


async def _feat_loop(uid, kind):
    interval = FEATS[kind]["interval"]
    try:
        while True:
            t0 = time.monotonic()
            client = _clients.get(uid)
            w = feat_get(uid, kind)
            if not client or not (w["allowed"] and w["active"] and w["chat"]):
                return
            if not _paused.get(uid) and w["chat"] != _bot_id[0]:
                try:
                    await _feat_fire(uid, client, kind, w["chat"])
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    logger.info("self %s %s: %s", kind, uid, e)
                    wait = getattr(e, "seconds", None)
                    if isinstance(wait, int) and wait > 0:
                        await asyncio.sleep(min(wait, 300))
            await asyncio.sleep(max(1.0, interval - (time.monotonic() - t0)))
    finally:
        if _feat_tasks.get((uid, kind)) is asyncio.current_task():
            _feat_tasks.pop((uid, kind), None)


def _fmt_interval(kind):
    n = FEATS[kind]["interval"]
    m, sec = divmod(int(n), 60)
    if m and sec:
        return f"{m} دقیقه و {sec} ثانیه"
    return f"{m} دقیقه" if m else f"{sec} ثانیه"


def _feat_what(kind):
    if kind == "lesson":
        return f"«{LESSON_WORD}» رو می‌گه، دکمهٔ «{LESSON_TOPIC}» رو می‌زنه و بعد دکمهٔ دوم رو"
    return f"«{FEATS[kind]['word']}» رو می‌گه و دکمهٔ وسط رو می‌زنه"


async def feat_cmd(uid, ev, kind, arg):
    """.کار / .درس  →  روشن | خاموش | (وضعیت)"""
    lb = FEATS[kind]["label"]
    w = feat_get(uid, kind)
    if not w["allowed"]:
        return f"⛔ قابلیت «{lb}» برای حساب تو فعال نیست. از پشتیبانی بخواه فعالش کنه."
    a = norm(arg)
    if a in ("خاموش", "off", "غیرفعال"):
        if not w["active"]:
            return f"ℹ️ «{lb}» الان خاموشه."
        feat_save(uid, kind, active=False, chat=None, title=None)
        _sync_feat(uid, kind)
        return f"⏹ «{lb}» خاموش شد."
    if a in ("روشن", "on", "فعال"):
        chat = await ev.get_chat()
        is_bot = bool(ev.is_private and getattr(chat, "bot", False))
        if ev.chat_id in (uid, _bot_id[0]) or not (ev.is_group or is_bot):
            return "💬 این دستور رو داخل گپِ هدف (گروه یا چت ربات) بنویس."
        if w["active"] and w["chat"] and w["chat"] != ev.chat_id:
            return (f"⚠️ «{lb}» همین الان توی «{w['title'] or 'یه گپ دیگه'}» روشنه و فقط توی یک گپ می‌شه فعالش کرد.\n"
                    f"اول اونجا «.{lb} خاموش» بزن.")
        if w["active"]:
            return f"ℹ️ «{lb}» توی همین گپ از قبل روشنه."
        title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(ev.chat_id)
        feat_save(uid, kind, active=True, chat=ev.chat_id, title=str(title)[:40])
        _sync_feat(uid, kind)
        return f"✅ «{lb}» روشن شد؛ هر {_fmt_interval(kind)} {_feat_what(kind)} می‌زنه."
    st = f"روشن توی «{w['title']}»" if w["active"] else "خاموش"
    return f"🛠 {lb}: {st}\nمثل: .{lb} روشن   |   .{lb} خاموش"


def _feat_panel(uid, kind):
    lb = FEATS[kind]["label"]
    w = feat_get(uid, kind)
    st = f"روشن توی «{w['title'] or 'گپ'}»" if w["active"] else "خاموش"
    t = (f"› {lb}\n\n"
         f"وضعیت: {st}\n\n"
         f"هر {_fmt_interval(kind)} خودش {_feat_what(kind)} می‌زنه.\n\n"
         f"روشن‌کردن: توی گپِ موردنظر بنویس  .{lb} روشن\n"
         f"خاموش‌کردن: .{lb} خاموش  (یا دکمهٔ پایین)\n"
         "فقط توی یک گپ می‌شه فعالش کرد.")
    rows = []
    if w["active"]:
        rows.append([B("خاموش کردن", callback_data=f"self:{kind}off", style="danger")])
    rows.append([B("بازگشت", callback_data="self:games", style="primary")])
    return t, KB(rows)


async def start_all(app):
    _bot_id[0] = app.bot.id
    _botref[0] = app.bot
    try:
        bot_me = await app.bot.get_me()
        configure_emoji_bot(bot_me.username)
    except Exception as e:
        logger.warning("could not resolve inline bot username: %s", e)
    asyncio.create_task(prepare_banner(app))
    if not configured():
        logger.info("self: SELF_API_ID/SELF_API_HASH تنظیم نشده؛ سلف غیرفعال است")
        return
    s = get_session()
    try:
        ids = [r.user_id for r in s.query(SelfAccess).filter(SelfAccess.enabled.is_(True), SelfAccess.session_enc.isnot(None)).all()]
    finally:
        s.close()

    async def _go():
        # بعد از دیپلوی، چند ثانیه صبر می‌کنیم تا نمونهٔ قبلیِ ربات کامل بسته بشه؛ اگه دو نمونه همزمان با یک نشست وصل بشن
        # تلگرام نشست رو باطل می‌کنه و کاربر مجبور می‌شه دوباره وصل کنه.
        await asyncio.sleep(max(0, int(os.environ.get("SELF_START_DELAY", "25") or 0)))
        for uid in ids:
            for attempt in range(3):
                try:
                    if await start_client(uid):
                        break
                except Exception as e:  # noqa: BLE001
                    logger.warning("self autostart %s: %s", uid, e)
                row = get_row(uid)
                if not (row and row.enabled and row.session_enc):
                    break
                await asyncio.sleep(20)
            await asyncio.sleep(1.5)
    asyncio.create_task(_go())


async def _dialogs(client):
    out = []
    try:
        async for d in client.iter_dialogs(limit=300):
            if d.id == _bot_id[0]:
                continue
            if d.is_group:
                out.append((d.id, d.name or str(d.id), True))
            elif d.is_user and not getattr(d.entity, "bot", False):
                out.append((d.id, d.name or str(d.id), False))
    except Exception as e:  # noqa: BLE001
        logger.info("dialogs failed: %s", e)
    return out


# ---------- ساعت زنده کنار اسم + کپی پروفایل ----------
# هر فونت: (اسم، ۱۰ رقم از 0 تا 9، علامت دونقطه)
CLOCK_FONTS = [
    ("عادی", "0123456789", ":"),
    ("بولد", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗", ":"),
    ("دابل", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡", ":"),
    ("مونو", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿", ":"),
    ("سنس بولد", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵", ":"),
    ("سنس", "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫", ":"),
    ("دایره‌ای", "⓪①②③④⑤⑥⑦⑧⑨", ":"),
    ("فول‌وید", "０１２３４５６７８９", "："),
    ("فارسی", "۰۱۲۳۴۵۶۷۸۹", ":"),
    ("بالانویس", "⁰¹²³⁴⁵⁶⁷⁸⁹", ":"),
    ("زیرنویس", "₀₁₂₃₄₅₆₇₈₉", ":"),
    ("دایره مشکی", "⓿➊➋➌➍➎➏➐➑➒", ":"),
    ("فراکتور", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵", "·"),
    ("سریف بولد", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗", "⁚"),
    ("تمام‌عرض نقطه", "０１２３４５６７８９", "．"),
    ("مونواسپیس نقطه", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿", "·"),
]
CLOCK_TZ = os.environ.get("SELF_TZ", "Asia/Tehran").strip() or "Asia/Tehran"
_clock_tasks = {}


def _tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(CLOCK_TZ)
    except Exception:
        from datetime import timedelta
        return timezone(timedelta(hours=3, minutes=30))   # ایران (بدون ساعت تابستانی)


def fmt_clock(font=1, now=None):
    font = font if isinstance(font, int) and 1 <= font <= len(CLOCK_FONTS) else 1
    _, digits, colon = CLOCK_FONTS[font - 1]
    t = (now or datetime.now(_tz())).strftime("%H:%M")
    return "".join(colon if ch == ":" else digits[int(ch)] for ch in t)


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def build_first_name(base_first, clock_txt=None, limit=64):
    """اسم + ساعت؛ اگه اسم بلند بود اسم کوتاه می‌شه تا ساعت جا بشه."""
    base_first = (base_first or "").strip()
    if not clock_txt:
        return base_first[:limit] or "."
    room = limit - _u16(clock_txt) - 1
    if room <= 0 or not base_first:
        return clock_txt
    while base_first and _u16(base_first) > room:
        base_first = base_first[:-1]
    return (base_first.rstrip() + " " + clock_txt) if base_first else clock_txt


def _pget(uid):
    s = get_session()
    try:
        r = s.get(SelfProfile, uid)
        return json.loads(r.data or "{}") if r else {}
    except Exception:
        return {}
    finally:
        s.close()


def _pput(uid, d):
    s = get_session()
    try:
        r = s.get(SelfProfile, uid)
        if not r:
            r = SelfProfile(user_id=uid)
            s.add(r)
        r.data = json.dumps(d, ensure_ascii=False)
        s.commit()
    finally:
        s.close()


async def _ensure_orig(uid, client):
    """پروفایل اصلی کاربر (قبل از ساعت/کپی) یک‌بار ذخیره می‌شه تا برگردوندنش ممکن باشه."""
    d = _pget(uid)
    if "orig_first" in d:
        return d
    from telethon.tl.functions.users import GetFullUserRequest
    me = await client.get_me()
    full = await client(GetFullUserRequest(me))
    d = _pget(uid)          # بعد از awaitها دوباره بخون؛ نسخهٔ قدیمی ممکنه تغییرات تازه رو پاک کنه
    d["orig_first"] = me.first_name or ""
    d["orig_last"] = me.last_name or ""
    d["orig_about"] = getattr(full.full_user, "about", None) or ""
    _pput(uid, d)
    return d


_CLOCK_COPY_KEYS = ("orig_first", "orig_last", "orig_about", "last_clock",
                    "copy_first", "copy_last", "copy_photo", "copy_about", "clock_on", "copy_on")


def _maybe_clear(uid):
    """وقتی ساعت و کپی هر دو خاموشن، فقط کلیدهای مربوط به همین دو پاک می‌شن.
    (قبلاً کل پروفایل پاک می‌شد و شمارهٔ کارت، متن خوشامد و بقیهٔ تنظیمات هم از بین می‌رفتن.)"""
    d = _pget(uid)
    if not d.get("clock_on") and not d.get("copy_on"):
        for k in _CLOCK_COPY_KEYS:
            d.pop(k, None)
        _pput(uid, d)


async def _apply_name(uid, client, about=None):
    from telethon.tl.functions.account import UpdateProfileRequest
    d = _pget(uid)
    if d.get("copy_on"):
        bf, bl = d.get("copy_first", ""), d.get("copy_last", "")
    else:
        bf, bl = d.get("orig_first", ""), d.get("orig_last", "")
    clk = fmt_clock(d.get("font", 1)) if d.get("clock_on") else None
    first = build_first_name(bf, clk)
    await client(UpdateProfileRequest(first_name=first, last_name=(bl or "")[:64], about=about))
    d = _pget(uid)          # دوباره بخون تا چیزی که وسط کار ذخیره شده (مثل کارت) پاک نشه
    d["last_clock"] = clk
    _pput(uid, d)


def _cancel_clock(uid):
    t = _clock_tasks.pop(uid, None)
    if t:
        try:
            t.cancel()
        except Exception:
            pass


def _sync_clock(uid):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    t = _clock_tasks.get(uid)
    if t and not t.done():
        return
    if uid in _clients and _pget(uid).get("clock_on"):
        _clock_tasks[uid] = loop.create_task(_clock_loop(uid))


async def _clock_loop(uid):
    from telethon import errors
    last = None
    try:
        while True:
            client = _clients.get(uid)
            d = _pget(uid)
            if not client or not d.get("clock_on"):
                return
            cur = fmt_clock(d.get("font", 1))
            wait = 0
            if cur != last:
                try:
                    await _apply_name(uid, client)
                    last = cur
                except errors.FloodWaitError as e:
                    wait = min(int(getattr(e, "seconds", 60)) + 2, 3600)
                except Exception as e:  # noqa: BLE001
                    logger.info("self clock %s: %s", uid, e)
                    wait = 30
            if not wait:
                wait = 60 - (time.time() % 60) + 0.7
            await asyncio.sleep(wait)
    except asyncio.CancelledError:
        raise
    finally:
        if _clock_tasks.get(uid) is asyncio.current_task():
            _clock_tasks.pop(uid, None)


def clock_fonts_text(uid):
    d = _pget(uid)
    cur = d.get("font", 1)
    lines = [f"🕒 ساعت: {'✅ روشن' if d.get('clock_on') else '❌ خاموش'}   فونت فعلی: {cur}", ""]
    for i, (name, _, _) in enumerate(CLOCK_FONTS, 1):
        lines.append(f"{i}) {fmt_clock(i)}   {name}" + ("  ✔️" if i == cur else ""))
    lines += ["", "انتخاب: .ساعت 3   |   خاموش: .غیرفعال ساعت"]
    return "\n".join(lines)


async def clock_set(uid, on=None, font=None):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    d = _pget(uid)
    if font is not None:
        d["font"] = font
    try:
        if on is False:
            if not d.get("clock_on"):
                return "🕒 ساعت از قبل خاموشه."
            d["clock_on"] = False
            _pput(uid, d)
            _cancel_clock(uid)
            await _apply_name(uid, client)
            _maybe_clear(uid)
            return "✅ ساعت خاموش شد و اسمت برگشت."
        d = await _ensure_orig(uid, client) if not d.get("clock_on") else d
        if font is not None:
            d["font"] = font
        d["clock_on"] = True
        _pput(uid, d)
        await _apply_name(uid, client)
        _sync_clock(uid)
        return f"✅ ساعت زنده روشن شد: {fmt_clock(d.get('font', 1))}  (فونت {d.get('font', 1)})\nتغییر فونت: .ساعت 2   |   لیست: .ساعت فونت"
    except Exception as e:  # noqa: BLE001
        logger.info("self clock_set %s: %s", uid, e)
        return "⚠️ خطا: " + str(e)[:120]


async def _delete_copy_photo(client, d):
    pid = d.pop("copy_photo", None)
    if not pid:
        return
    from telethon import utils
    from telethon.tl.functions.photos import DeletePhotosRequest
    async for ph in client.iter_profile_photos("me", limit=10):
        if ph.id == pid:
            await client(DeletePhotosRequest([utils.get_input_photo(ph)]))
            break


async def copy_on(uid, ev):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not ev.is_reply:
        return "↩️ روی پیام اون کاربر ریپلای کن و بنویس: .کپی"
    try:
        import io
        from telethon.tl.types import User as TUser
        from telethon.tl.functions.users import GetFullUserRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        msg = await ev.get_reply_message()
        target = await msg.get_sender() if msg else None
        if not isinstance(target, TUser):
            return "❌ فقط روی پیام یه کاربر می‌شه کپی کرد (نه کانال/ادمین ناشناس)."
        me = await client.get_me()
        if target.id == me.id:
            return "😅 این خودتی!"
        d = await _ensure_orig(uid, client)
        full = await client(GetFullUserRequest(target))
        about = getattr(full.full_user, "about", None) or ""
        about = about[: (140 if getattr(me, "premium", False) else 70)]
        first = (target.first_name or "").strip() or (target.username or "") or "."
        last = (target.last_name or "").strip()
        photo_bytes = None
        try:
            photo_bytes = await client.download_profile_photo(target, file=bytes)
        except Exception as e:  # noqa: BLE001
            logger.info("self copy photo dl: %s", e)
        await _delete_copy_photo(client, d)    # اگه قبلاً کپی دیگه‌ای فعال بود، عکسش پاک بشه
        new_pid = None
        if photo_bytes:
            bio = io.BytesIO(photo_bytes)
            bio.name = "photo.jpg"
            up = await client.upload_file(bio)
            res = await client(UploadProfilePhotoRequest(file=up))
            new_pid = getattr(getattr(res, "photo", None), "id", None)
        d["copy_on"] = True
        d["copy_first"], d["copy_last"] = first[:64], last[:64]
        d["copy_photo"] = new_pid
        d["copy_about"] = about
        _pput(uid, d)
        try:
            await _apply_name(uid, client, about=about)
        except Exception:
            await _apply_name(uid, client, about=about[:70])
        _sync_clock(uid)
        extra = "" if photo_bytes else "\n(عکس نداشت یا مخفیه؛ عکس خودت دست نخورد.)"
        return f"✅ پروفایل «{first}» کپی شد (اسم، بیو" + ("، عکس" if photo_bytes else "") + ")." + extra + "\nبرگشت: .غیرفعال کپی"
    except Exception as e:  # noqa: BLE001
        logger.info("self copy %s: %s", uid, e)
        return "⚠️ کپی نشد: " + str(e)[:120]


async def copy_off(uid):
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    d = _pget(uid)
    if not d.get("copy_on"):
        return "ℹ️ کپی فعال نیست."
    try:
        await _delete_copy_photo(client, d)
        for k in ("copy_on", "copy_first", "copy_last", "copy_about"):
            d.pop(k, None)
        _pput(uid, d)
        await _apply_name(uid, client, about=d.get("orig_about", ""))
        _maybe_clear(uid)
        return "✅ کپی غیرفعال شد؛ اسم، بیو و عکس خودت برگشت."
    except Exception as e:  # noqa: BLE001
        logger.info("self copy_off %s: %s", uid, e)
        return "⚠️ خطا: " + str(e)[:120]


async def _reset_profile(uid, client):
    """موقع قطع اتصال/غیرفعال‌سازی: ساعت و کپی برداشته می‌شن و پروفایل اصلی برمی‌گرده."""
    d = _pget(uid)
    if not d.get("clock_on") and not d.get("copy_on"):
        return
    had_copy = bool(d.get("copy_on"))
    if had_copy:
        await _delete_copy_photo(client, d)
    for k in ("copy_on", "copy_first", "copy_last", "copy_about"):
        d.pop(k, None)
    d["clock_on"] = False
    _pput(uid, d)
    await _apply_name(uid, client, about=d.get("orig_about", "") if had_copy else None)
    _maybe_clear(uid)


def _clock_panel(uid):
    d = _pget(uid)
    on = bool(d.get("clock_on"))
    cur = d.get("font", 1)
    tx = (f"🕒 <b>ساعت زنده</b>\n\nوضعیت: {'✅ روشن' if on else '❌ خاموش'}\n"
          f"نمونه: {fmt_clock(cur)}\n\nساعت کنار اسم اکانتت (به وقت ایران) هر دقیقه به‌روز می‌شه. فونت دلخواهت رو انتخاب کن:\n"
          "⚠️ تا ساعت روشنه اسمت رو دستی عوض نکن؛ اول ساعت رو خاموش کن.")
    kb = [[B("⏹ خاموش کردن" if on else "▶️ روشن کردن", callback_data="self:clkt")]]
    row = []
    for i in range(1, len(CLOCK_FONTS) + 1):
        row.append(B(f"{'✔️ ' if i == cur else ''}{fmt_clock(i)}", callback_data=f"self:clkf:{i}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([B("🔙 بازگشت", callback_data="self:home")])
    return tx, KB(kb)


EMOJI_BOT = ""

def configure_emoji_bot(username):
    """همیشه از ربات همین پروژه استفاده کن؛ وابستگی به ربات شخص ثالث لازم نیست."""
    global EMOJI_BOT
    EMOJI_BOT = (username or "").strip().lstrip("@")
    logger.info("premium emoji inline bot configured: @%s", EMOJI_BOT or "(missing)")


async def send_premium(client, chat, text, reply_to=None):
    """ارسال نتیجهٔ اینلاین در همان گفتگوی جاری (گروه، سوپرگروه یا پیوی)."""
    try:
        if not EMOJI_BOT:
            raise RuntimeError("یوزرنیم ربات همین پروژه پیدا نشد؛ ابتدا ربات را راه‌اندازی کن.")
        # استفاده از peer واقعی همین پیام باعث می‌شود در گروه و پیوی مقصد درست انتخاب شود.
        peer = chat if getattr(chat, "SUBCLASS_OF_ID", None) else await client.get_input_entity(chat)
        results = await client.inline_query(EMOJI_BOT, text, entity=peer)
        if not results:
            logger.warning("premium inline returned no results for peer=%s", chat)
            return False
        # نتیجهٔ خطا را به‌عنوان پیام موفق ارسال نکن.
        errors = [r for r in results if (getattr(r, "title", "") or "").startswith("❌")]
        chosen = next((r for r in results if "تبدیل به ایموجی پرمیوم" in (getattr(r, "title", "") or "")), None)
        if chosen is None and errors:
            raise RuntimeError(getattr(errors[0], "title", "کد ایموجی معتبر نیست"))
        if chosen is None:
            raise RuntimeError("ربات همین پروژه نتیجهٔ تبدیل ایموجی پرمیوم برنگرداند.")
        await chosen.click(entity=peer, reply_to=reply_to)
        return True
    except Exception as e:
        logger.warning("premium inline send failed peer=%s: %s", chat, e, exc_info=True)
        raise


REACT_DEFAULT = ["❤", "🔥", "👍"]
_EMO_RE = re.compile("(?:[\\U0001F1E6-\\U0001F1FF]{2}|[\\u2600-\\u27BF\\u2B00-\\u2BFF\\U0001F000-\\U0001F1E5\\U0001F200-\\U0001FAFF]\\ufe0f?[\\U0001F3FB-\\U0001F3FF]?(?:\\u200d[\\u2600-\\u27BF\\u2B00-\\u2BFF\\U0001F000-\\U0001F1E5\\U0001F200-\\U0001FAFF]\\ufe0f?)*)")


async def react_start(uid, ev, arg):
    """.ریاکشن [ایموجی‌ها] روی پیام کاربر ریپلای کن؛ همون پیام ریاکشن می‌خوره.
    اکانت پریمیوم تا ۳ ریاکشن هم‌زمان، اکانت عادی ۱ تا. مثل: .ریاکشن 🔥❤👍  یا  /reykt 🔥❤👍"""
    from telethon import errors
    from telethon.tl.functions.messages import SendReactionRequest
    from telethon.tl.types import ReactionEmoji
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not ev.is_reply:
        return "↩️ روی پیام اون کاربر ریپلای کن و بنویس:\n.ریاکشن 🔥❤👍   یا   /reykt"
    try:
        me = await client.get_me()
        limit = 3 if getattr(me, "premium", False) else 1
        emos = []
        for e in _EMO_RE.findall(arg or ""):
            e = e.replace("\ufe0f", "")
            if e and e not in emos:
                emos.append(e)
        emos = (emos or REACT_DEFAULT)[:limit]
        await client(SendReactionRequest(peer=ev.chat_id, msg_id=ev.reply_to_msg_id,
                                         reaction=[ReactionEmoji(emoticon=e) for e in emos]))
    except errors.ReactionInvalidError:
        return "❌ یکی از ایموجی‌ها به‌عنوان ریاکشن مجاز نیست."
    except errors.FloodWaitError as e:
        return f"⏳ تلگرام محدود کرد؛ {e.seconds} ثانیه صبر کن."
    except Exception as e:  # noqa: BLE001
        logger.info("self react %s: %s", uid, e)
        return "⚠️ ارسال نشد: " + str(e)[:120]
    try:
        await ev.delete()      # پیام دستور پاک می‌شه
    except Exception:  # noqa: BLE001
        pass
    return None


def _u16(t):
    return len(t.encode("utf-16-le")) // 2


async def emoji_send(uid, ev, arg):
    """ارسال ایموجی پریمیوم در هر گپ/پیوی؛ برای اکانت عادی از اینلاین بات کمک می‌گیرد."""
    import copy
    from telethon.tl.types import MessageEntityCustomEmoji
    client = _clients.get(uid)
    if not client:
        return "❌ اول اکانتت رو وصل کن (/self)."
    if not arg:
        return "✨ مثال: .ایموجی سلام {5260416394419265081}\nدر گروه، پیوی یا پیام‌های ذخیره‌شده قابل استفاده است."
    try:
        me = await client.get_me()
        if not getattr(me, "premium", False):
            # حساب عادی از اینلاینِ ربات خود همین پروژه استفاده می‌کند؛ ربات خارجی لازم نیست.
            inline_text = re.sub(r"\{([0-9۰-۹٠-٩]{5,25})\}", r"[\1]", arg)
            try:
                ok = await send_premium(client, ev.input_chat, inline_text,
                                        reply_to=ev.reply_to_msg_id if ev.is_reply else None)
            except Exception as inline_error:
                logger.info("premium inline failed in chat %s: %s", ev.chat_id, inline_error)
                return (f"❌ ارسال ایموجی با ربات خود پروژه (@{EMOJI_BOT or 'نامشخص'}) انجام نشد. "
                        "در BotFather برای همین ربات /setinline را فعال کن؛ برای حذف دکمهٔ موقت هم /setinlinefeedback را روشن کن. "
                        "اگر تلگرام اجازهٔ custom emoji به این ربات ندهد، این محدودیت با تغییر کد دور زده نمی‌شود. "
                        "جزئیات: " + str(inline_error)[:180])
            if not ok:
                return f"❌ ربات همین پروژه (@{EMOJI_BOT or 'نامشخص'}) هیچ نتیجه‌ای برنگرداند؛ /setinline و قالب کد {{آیدی}} را بررسی کن."
            try:
                await ev.delete()
            except Exception:
                pass
            return None
        raw = ev.raw_text or "" 
        end = len(raw.rstrip())
        start = end - len(arg)
        start16 = _u16(raw[:start])
        ents = []
        for e in (ev.message.entities or []):
            if e.offset >= start16:
                e2 = copy.copy(e)
                e2.offset -= start16
                ents.append(e2)
        text = arg
        # جایگزینی {آیدی} با ایموجی پریمیوم
        while True:
            m = re.search(r"\{(\d{6,})\}", text)
            if not m:
                break
            pos16 = _u16(text[:m.start()])
            old16 = _u16(m.group(0))
            for e in ents:
                if e.offset >= pos16 + old16:
                    e.offset -= old16 - 1
            ents.append(MessageEntityCustomEmoji(offset=pos16, length=1, document_id=int(m.group(1))))
            text = text[:m.start()] + "⭐" + text[m.end():]
        if not any(isinstance(e, MessageEntityCustomEmoji) for e in ents):
            return "❌ توی متن ایموجی پریمیوم پیدا نشد؛ از کیبوردت بذار یا {آیدی} بنویس."
        ents.sort(key=lambda e: e.offset)
        await client.send_message(ev.chat_id, text, formatting_entities=ents, parse_mode=None,
                                  reply_to=ev.reply_to_msg_id if ev.is_reply else None)
    except Exception as e:  # noqa: BLE001
        logger.info("self emoji %s: %s", uid, e)
        return "⚠️ ارسال نشد: " + str(e)[:120]
    try:
        await ev.delete()      # پیام دستور پاک می‌شه و فقط پیام ایموجی می‌مونه
    except Exception:
        pass
    return None


# ---------- دستورهای نقطه‌ای داخل چت (.پنل / .کلمه / .زمان / .دکمه / .گپ) ----------
# کاربرِ فعال‌شده از خود اکانتش (هر چتی؛ بهترین جا: Saved Messages) این دستورها رو می‌نویسه.
#   .پنل            → پنل تنظیمات بالا میاد
#   .کلمه فلان      → کلمه‌ی اسلات فعلی (اگه عدد اسلات بذاری: .کلمه 2 فلان)
#   .زمان فلان      → تایمر: 5 یا 10s یا 2 دقیقه
#   .دکمه فلان      → چپ | وسط | راست | خالی
#   .گپ             → همین گپی که توش می‌نویسی؛ یا .گپ اسم گروه
# بعد از تنظیم (کلمه + گپ + دکمه) سلف خودش کار می‌کنه.
_CMD = {"پنل": "panel", "panel": "panel", "منو": "panel",
        "ترجمه": "translate", "translate": "translate",
        "تگ": "tag", "tag": "tag", "ریاکشن": "react", "واکنش": "react",
        "react": "react", "reykt": "react", "reaction": "react",
        "ذخیره": "save", "save": "save",
        "ایموجی": "emoji", "emoji": "emoji", "ساعت": "clock", "clock": "clock",
        "کپی": "copy", "copy": "copy", "غیرفعال": "off", "غیر‌فعال": "off", "off": "off",
        # --- ابزارها و سکوت/بلاک (پیاده‌سازی در extras.py) ---
        "سکوت": "mute", "mute": "mute", "رفعسکوت": "unmute", "unmute": "unmute",
        "بلاک": "block", "block": "block", "آنبلاک": "unblock", "unblock": "unblock", "رفع": "undo",
        "حساب": "calc", "محاسبه": "calc", "ماشینحساب": "calc", "calc": "calc",
        "قیمت": "price", "price": "price", "تاریخ": "date", "date": "date",
        "کوتاه": "short", "کوتاهکن": "short", "short": "short", "shorten": "short",
        "موجودی": "balance", "balance": "balance",
        "اطلاعات": "info", "info": "info",
        "اتو": "autorep", "اتوریپ": "autorep", "autorep": "autorep",
        "خوش": "welcome", "خوشامد": "welcome", "خوشآمد": "welcome", "خوشآمدگویی": "welcome", "welcome": "welcome",
        "کارت": "card", "card": "card",
        "ویدیو": "vnote", "ویدیومسیج": "vnote", "گرد": "vnote", "vnote": "vnote",
        "عضویت": "joinall", "joinall": "joinall",
        "استارز": "stars", "استار": "stars", "stars": "stars",
        "میم": "meme", "meme": "meme",
        "افزودن": "addmode", "لیست": "listmode",
        "استیکر": "sticker", "sticker": "sticker", "متن": "stt", "stt": "stt", "ویس": "tts", "tts": "tts",
        "یوتیوب": "yt", "یوتوب": "yt", "youtube": "yt", "yt": "yt",
        "اینستا": "insta", "اینستاگرام": "insta", "insta": "insta", "instagram": "insta",
        "تیک": "tiktok", "تیکتاک": "tiktok", "tiktok": "tiktok", "tt": "tiktok",
        "پینترست": "pinterest", "پینترس": "pinterest", "pinterest": "pinterest",
        "استوری": "story", "story": "story", "کار": "work", "work": "work", "درس": "lesson", "lesson": "lesson",
        "پاکسازی": "purge", "پاکسازی‌پیام": "purge", "purge": "purge", "پاک": "purge",
        "پینگ": "ping", "ping": "ping", "حذفپیام": "delmsg", "حذف": "delmsg", "delmsg": "delmsg",
        "افزودنریاکشن": "bulkreact", "bulkreact": "bulkreact",
        "بکاپ": "backup", "بکآپ": "backup", "backup": "backup"}
_POSW = {"چپ": "l", "left": "l", "l": "l", "وسط": "m", "میانه": "m", "mid": "m", "middle": "m", "m": "m",
         "راست": "r", "right": "r", "r": "r", "خالی": "n", "هیچ": "n", "none": "n", "n": "n"}
_AR2FA = str.maketrans("يك", "یک")
_cur = {}          # uid -> اسلاتی که آخر دست زدیم (دستورهای بدون شماره روی همین اعمال می‌شن)
_cmd_hist = {}


def parse_dot(raw):
    """'.کلمه سلام' → ('word', 'سلام'). اگه دستور شناخته‌شده نبود None."""
    t = (raw or "").strip().translate(_AR2FA)
    if not (t.startswith(".") or t.startswith("/")):
        return None
    m = re.match(r"^[./]\s*(\S+)\s*(.*)$", t, re.S)
    if not m:
        return None
    cmd = _CMD.get(m.group(1).replace("\u200c", "").lower())
    if cmd and t.startswith("/") and cmd != "react":
        return None      # با / فقط دستور ریاکشن کار می‌کنه
    return (cmd, m.group(2).strip()) if cmd else None


def split_slot(arg):
    """'2 سلام' → (1, 'سلام'). بدون عدد اسلات → (None, arg)."""
    m = re.match(r"^([1-3\u06f1-\u06f3])\s+(\S.*)$", arg or "", re.S)
    if m:
        return int(fa2en(m.group(1))) - 1, m.group(2).strip()
    return None, (arg or "").strip()


def _slot_line(i, s):
    if not s or not s.get("word"):
        return f"{i + 1}️⃣ ➕ خالی"
    parts = [f"{i + 1}️⃣ «{s['word']}»", f"💬 {(s.get('title') or 'گپ؟')[:22]}",
             (f"🔁 هر {fmt_delay(s.get('delay', 0))}" if int(s.get('delay', 0) or 0) >= MIN_AUTO else "🔁 خاموش"),
             f"🎯 {POS[s.get('pos', 'n')]}"]
    return " | ".join(parts) + ("  ✅" if s.get("chat") else "  ⚠️ گپ تنظیم نشده")


def _missing(s):
    miss = []
    if not s.get("word"):
        miss.append(".کلمه فلان")
    if not s.get("chat"):
        miss.append(".گپ")
    if int(s.get("delay", 0) or 0) < MIN_AUTO:
        miss.append(".زمان 30 (هر چند ثانیه خودکار بگه؛ حداقل 5)")
    return miss


def _ready_text(s):
    miss = _missing(s)
    if not miss:
        tail = (f" و بعدش دکمه‌ی {POS[s.get('pos')]} رو می‌زنه." if s.get("pos", "n") != "n"
                else " (دکمه‌ای زده نمی‌شه؛ اگه می‌خوای: .دکمه چپ|وسط|راست).")
        return (f"🚀 فعاله! هر {fmt_delay(s.get('delay', 0))} خودش «{s['word']}» رو توی «{s.get('title') or 'گپ'}» می‌گه" + tail)
    return "📌 مونده: " + " | ".join(miss)


def _panel_text(uid):
    row = get_row(uid)
    sl = slots_of(row) if row else []
    on = uid in _clients
    lines = ["🤖 سلف روبی", "",
             f"🔌 اتصال: {'✅ وصل' if on else '❌ قطع'}   ⚙️ وضعیت: {'⏸ متوقف' if row and row.paused else '▶️ فعال'}", ""]
    for i in range(MAX_SLOTS):
        lines.append(_slot_line(i, sl[i] if i < len(sl) else {}))
    return "\n".join(lines)


async def _del_later(m, sec):
    try:
        await asyncio.sleep(sec)
        await m.delete()
    except Exception:
        pass


FMT_PAD = "⠀"   # خط خالیِ دیده‌نشدنی بالا و پایین متن تا توی نقل‌قول کمی فاصله بیفته؛ "" کن تا حذف شه


def quote_html(body_html):
    """قالب یکسان جواب همهٔ دستورها: پررنگ + نقل‌قول + کمی فاصله. ورودی باید HTML امن باشه."""
    pad = FMT_PAD + "\n" if FMT_PAD else ""
    tail = "\n" + FMT_PAD if FMT_PAD else ""
    return f"<blockquote><b>{pad}{body_html}{tail}</b></blockquote>"


def fancy_html(text):
    import html as _html
    return quote_html(_html.escape((text or "").strip()[:3800], quote=False))


async def _say(uid, ev, text, keep=False):
    """جواب رو همون‌جا (با ادیت پیام دستور) می‌ده (پررنگ + نقل‌قول) و بعد چند ثانیه پاکش می‌کنه."""
    try:
        try:
            m = await ev.edit(fancy_html(text), parse_mode="html")
        except Exception:
            m = await ev.edit(text, parse_mode=None)      # اگه قالب‌بندی رد شد، حداقل متن ساده
    except Exception:
        try:
            await _botref[0].send_message(uid, text)
        except Exception:
            pass
        return
    if not keep:
        asyncio.create_task(_del_later(m, 10))


def _pick_slot(uid, sl, explicit, word=None):
    if explicit is not None:
        return explicit
    if word:
        w = norm(word)
        for i, s in enumerate(sl):
            if s.get("word") and norm(s["word"]) == w:
                return i
        for i, s in enumerate(sl):
            if not s.get("word"):
                return i
    return min(max(_cur.get(uid, 0), 0), MAX_SLOTS - 1)


async def _find_chat(client, name):
    q = norm(name)
    ds = await _dialogs(client)
    exact = [d for d in ds if norm(d[1]) == q]
    if len(exact) == 1:
        return [exact[0]]
    return [d for d in ds if q in norm(d[1])]


_purging = set()
PURGE_MAX = int(os.environ.get("PURGE_MAX", "1000") or 1000)
PURGE_USER_MAX = 100


async def _purge(uid, ev, arg):
    """.پاکسازی 50 | .پاکسازی همه | ریپلای + .پاکسازی | ریپلای + .پاکسازی کاربر"""
    client = _clients.get(uid)
    if not client:
        await _say(uid, ev, "❌ اول اکانت سلف را وصل کن.")
        return
    if uid in _purging:
        await _say(uid, ev, "⏳ یه پاکسازی در حال انجامه؛ صبر کن تموم بشه.")
        return
    a = fa2en((arg or "").strip()).lower()
    reply = None
    if ev.is_reply:
        try:
            reply = await ev.get_reply_message()
        except Exception:  # noqa: BLE001
            reply = None
    kw = {}
    if a in ("همه", "all"):
        kw = {"from_user": "me", "limit": PURGE_MAX + 1}
    elif a.isdigit():
        kw = {"from_user": "me", "limit": max(1, min(int(a), PURGE_MAX)) + 1}
    elif a == "" and reply:
        kw = {"from_user": "me", "min_id": reply.id - 1, "limit": PURGE_MAX + 1}
    elif a in ("کاربر", "user") and reply:
        try:
            kw = {"from_user": await reply.get_input_sender(), "limit": PURGE_USER_MAX + 1}
        except Exception:  # noqa: BLE001
            kw = {}
        if not kw.get("from_user"):
            await _say(uid, ev, "❌ فرستندهٔ این پیام قابل شناسایی نیست.")
            return
    else:
        await _say(uid, ev, "🧹 پاکسازی\n.پاکسازی 50 ← ۵۰ پیام آخر خودت\n.پاکسازی همه ← همهٔ پیام‌های خودت توی این چت\n"
                            "ریپلای + .پاکسازی ← پیام‌های خودت از اون پیام تا آخر\nریپلای + .پاکسازی کاربر ← پیام‌های اون کاربر (نیاز به ادمین بودن)", keep=True)
        return
    _purging.add(uid)
    try:
        chat = await ev.get_input_chat()
        ids = []
        async for m in client.iter_messages(chat, **kw):
            if m.id != ev.id:
                ids.append(m.id)
        if not ids:
            await _say(uid, ev, "🧹 پیامی برای پاک‌کردن پیدا نشد.")
            return
        await _say(uid, ev, f"🧹 در حال پاک‌کردن {len(ids)} پیام…", keep=True)
        done, err = 0, ""
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            try:
                res = await client.delete_messages(chat, chunk, revoke=True)
                done += sum(getattr(x, "pts_count", 0) for x in (res or [])) or len(chunk)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)[:100]
                break
            await asyncio.sleep(1.0)       # مکث بین دسته‌ها؛ محافظت از اکانت
        msg = f"🧹 {done} پیام پاک شد ✅"
        if err:
            msg += "\n⚠️ ادامه نیافت: " + err
        await _say(uid, ev, msg)
    except Exception as exc:  # noqa: BLE001
        await _say(uid, ev, "❌ پاکسازی انجام نشد: " + str(exc)[:100])
    finally:
        _purging.discard(uid)


BACKUP_MAX = int(os.environ.get("BACKUP_MAX", "20000") or 20000)
_backing = set()


def _bk_kind(m):
    for attr, label in (("photo", "عکس"), ("video_note", "ویدیو مسیج"), ("video", "ویدیو"), ("voice", "ویس"),
                        ("sticker", "استیکر"), ("gif", "گیف"), ("audio", "آهنگ"), ("document", "فایل")):
        try:
            if getattr(m, attr, None):
                return label
        except Exception:  # noqa: BLE001
            pass
    if getattr(m, "poll", None):
        return "نظرسنجی"
    if getattr(m, "geo", None) or getattr(m, "venue", None):
        return "موقعیت مکانی"
    if getattr(m, "contact", None):
        return "مخاطب"
    return "رسانه" if getattr(m, "media", None) else ""


def _bk_time(dt):
    """زمان پیام به وقت ایران (UTC+3:30)."""
    from datetime import timedelta
    try:
        return (dt.astimezone(timezone.utc) + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return ""


def _bk_html(title, me_name, msgs, total_note=""):
    import html as _h
    esc = lambda x: _h.escape(str(x or ""), quote=False)
    out = []
    last_day = None
    for m in msgs:
        day = _bk_time(m.date)[:10]
        if day and day != last_day:
            last_day = day
            out.append(f'<div class="day">{esc(day)}</div>')
        if getattr(m, "action", None) is not None and not (m.message or ""):
            out.append(f'<div class="sys">پیام سیستمی • {esc(_bk_time(m.date)[11:])}</div>')
            continue
        mine = bool(getattr(m, "out", False))
        who = me_name if mine else title
        parts = []
        if getattr(m, "fwd_from", None) is not None:
            parts.append('<div class="meta">↪️ فوروارد شده</div>')
        if getattr(m, "reply_to_msg_id", None):
            parts.append(f'<div class="meta">↩️ پاسخ به پیام #{m.reply_to_msg_id}</div>')
        kind = _bk_kind(m)
        if kind:
            parts.append(f'<div class="media">📎 {esc(kind)}</div>')
        txt = m.message or ""
        if txt:
            parts.append(f'<div class="txt">{esc(txt)}</div>')
        if not parts:
            parts.append('<div class="media">(پیام خالی)</div>')
        edited = " • ویرایش‌شده" if getattr(m, "edit_date", None) else ""
        out.append(
            f'<div class="msg {"out" if mine else "in"}"><div class="who">{esc(who)}</div>'
            + "".join(parts)
            + f'<div class="time">#{m.id} • {esc(_bk_time(m.date))}{edited}</div></div>')
    head = (f'<h1>بکاپ چت با {esc(title)}</h1><div class="info">تعداد پیام: {len(msgs)} • '
            f'گرفته‌شده: {esc(_bk_time(datetime.now(timezone.utc)))} (وقت ایران){esc(total_note)}</div>')
    css = ("body{font-family:Tahoma,Vazirmatn,sans-serif;background:#e6ebee;margin:0;padding:16px;direction:rtl}"
           ".wrap{max-width:720px;margin:auto}h1{font-size:18px;text-align:center}"
           ".info{font-size:12px;color:#555;text-align:center;margin-bottom:14px}"
           ".day{text-align:center;margin:14px 0 8px;font-size:12px;color:#fff;background:#8a9aa6;border-radius:12px;padding:3px 12px;width:max-content;margin-inline:auto}"
           ".msg{max-width:80%;margin:6px 0;padding:8px 12px;border-radius:12px;box-shadow:0 1px 1px rgba(0,0,0,.12)}"
           ".msg.in{background:#fff;margin-inline-end:auto}.msg.out{background:#d7f0c2;margin-inline-start:auto}"
           ".who{font-size:12px;font-weight:bold;color:#2a7ab0;margin-bottom:3px}.out .who{color:#3b8a2a}"
           ".txt{white-space:pre-wrap;word-wrap:break-word;font-size:14px;line-height:1.7}"
           ".meta,.media{font-size:12px;color:#666;margin-bottom:2px}.media{color:#8a4b00}"
           ".time{font-size:11px;color:#888;margin-top:4px;text-align:left;direction:ltr}"
           ".sys{text-align:center;font-size:12px;color:#777;margin:6px 0}")
    return ('<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>بکاپ چت - {esc(title)}</title><style>{css}</style></head><body><div class="wrap">'
            + head + "".join(out) + "</div></body></html>")


async def _backup_chat(uid, ev):
    """.بکاپ چت ← همهٔ پیام‌های همین پی‌وی (حداکثر BACKUP_MAX تا) به‌صورت یک فایل HTML توی Saved Messages."""
    import io
    client = _clients.get(uid)
    if not client:
        await _say(uid, ev, "❌ اول اکانت سلف را وصل کن.")
        return
    if not ev.is_private:
        await _say(uid, ev, "❌ بکاپ فقط توی چت پی‌وی کار می‌کنه؛ این دستور رو توی همون پی‌وی بنویس.")
        return
    if uid in _backing:
        await _say(uid, ev, "⏳ یه بکاپ دیگه در حال انجامه؛ صبر کن تموم بشه.")
        return
    _backing.add(uid)
    try:
        await _say(uid, ev, "⏳ در حال بکاپ‌گیری از پیام‌های این چت...", keep=True)
        from telethon.utils import get_display_name
        chat = await ev.get_chat()
        title = get_display_name(chat) or str(ev.chat_id)
        me = await client.get_me()
        me_name = get_display_name(me) or "من"
        msgs = []
        async for m in client.iter_messages(ev.chat_id, limit=BACKUP_MAX + 1):
            if m.id == ev.id:
                continue
            msgs.append(m)
            if len(msgs) >= BACKUP_MAX:
                break
        msgs.reverse()
        if not msgs:
            await _say(uid, ev, "پیامی برای بکاپ پیدا نشد.")
            return
        note = f" • فقط {BACKUP_MAX} پیام آخر" if len(msgs) >= BACKUP_MAX else ""
        data = _bk_html(title, me_name, msgs, note).encode("utf-8")
        bio = io.BytesIO(data)
        safe = re.sub(r"[^\w\u0600-\u06FF-]+", "_", title)[:30].strip("_") or str(ev.chat_id)
        bio.name = f"chat_backup_{safe}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.html"
        await client.send_file("me", bio, force_document=True,
                               caption=f"💾 بکاپ چت با {title}\nتعداد پیام: {len(msgs)}{note}")
        await _say(uid, ev, f"✅ بکاپ {len(msgs)} پیام ساخته شد و توی Saved Messages فرستاده شد.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("self backup %s: %s", uid, exc)
        await _say(uid, ev, "❌ بکاپ انجام نشد: " + str(exc)[:100])
    finally:
        _backing.discard(uid)


async def _pvjoin_cmd(uid, ev, arg):
    """.عضویت اجباری روشن | .عضویت اجباری خاموش"""
    rest = re.sub(r"^(?:اجباری|اجبار)\s*", "", (arg or "").strip()).strip().lower()
    d = _pget(uid)
    if rest in ("روشن", "فعال", "on", "1"):
        d["pv_join"] = True
    elif rest in ("خاموش", "غیرفعال", "غیر فعال", "off", "0"):
        d["pv_join"] = False
    else:
        st = "روشن" if d.get("pv_join") else "خاموش"
        await _say(uid, ev, f"📢 عضویت اجباری پیوی الان {st} است.\nبنویس: .عضویت اجباری روشن  یا  .عضویت اجباری خاموش")
        return
    _pput(uid, d)
    if d["pv_join"]:
        chs = [x.strip().lstrip("@") for x in os.environ.get("SELF_PV_JOIN_CHANNELS", "selfsaz_iran,fox_frenzy").split(",") if x.strip()]
        await _say(uid, ev, "✅ عضویت اجباری پیوی روشن شد؛ کسی که عضو کانال‌های زیر نباشه نمی‌تونه بهت پیام بده:\n"
                   + "\n".join("@" + x for x in chs[:5]))
    else:
        await _say(uid, ev, "✅ عضویت اجباری پیوی خاموش شد.")



async def _handle_dot(uid, ev):
    raw = ev.raw_text or ""
    if raw.strip() == "پنل":
        raw = ".پنل"
    if not raw.lstrip().startswith((".", "/")):
        return False
    pc = parse_dot(raw)
    if not pc:
        return False
    cmd, arg = pc
    if cmd == "joinall" and re.match(r"^(?:اجباری|اجبار)(?:\s|$)", arg or ""):
        await _pvjoin_cmd(uid, ev, arg)      # «.عضویت همه» سر جاش می‌مونه؛ فقط «.عضویت اجباری ...» اینجا هندل می‌شه
        return True
    if cmd == "backup":
        await _backup_chat(uid, ev)
        return True
    if cmd == "addmode":
        # «.افزودن ری اکشن 🔥» (راهنمای قدیمیِ ریاکشن گروهی) هم کار کنه
        m_ = re.match(r"^(?:ری[\s\u200c]*اکشن|واکنش|react(?:ion)?)\s*(.*)$", arg or "", re.I | re.S)
        if m_:
            cmd, arg = "bulkreact", m_.group(1)
    import selfmodes
    rt = selfmodes.route(cmd, arg)
    if rt:
        text, keep = await selfmodes.run(uid, ev, rt)
        if text:
            await _say(uid, ev, text, keep=keep)
        return True
    if cmd in ("addmode", "listmode"):
        return False
    if cmd == "ping":
        client = _clients.get(uid)
        if not client:
            await _say(uid, ev, "🏓 اکانت سلف وصل نیست.")
        else:
            started = time.perf_counter()
            try:
                await client.get_me()
                ms = max(1, int((time.perf_counter() - started) * 1000))
                await _say(uid, ev, f"🏓 پینگ سلف: {ms} ms")
            except Exception as exc:
                await _say(uid, ev, "❌ پینگ ناموفق بود: " + str(exc)[:100])
        return True
    if cmd == "delmsg":
        if not ev.is_reply:
            await _say(uid, ev, "🗑 برای حذف پیام، روی پیام موردنظر ریپلای کن و بنویس .حذف پیام")
            return True
        try:
            r = await ev.get_reply_message()
            if r:
                await r.delete()
            try: await ev.delete()
            except Exception: pass
        except Exception as exc:
            await _say(uid, ev, "❌ حذف پیام انجام نشد: " + str(exc)[:100])
        return True
    if cmd == "purge":
        await _purge(uid, ev, arg)
        return True
    if cmd == "bulkreact":
        if not ev.is_reply:
            await _say(uid, ev, "🔥 روی پیام کاربر ریپلای کن و بنویس: .افزودن ری اکشن 🔥")
            return True
        client = _clients.get(uid)
        if not client:
            await _say(uid, ev, "❌ اول اکانت سلف را وصل کن.")
            return True
        emos = _EMO_RE.findall(arg or "")
        emoji = (emos[0].replace("\ufe0f", "") if emos else "🔥")
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            reply = await ev.get_reply_message()
            target = await reply.get_sender() if reply else None
            if not target:
                await _say(uid, ev, "❌ فرستندهٔ پیام پیدا نشد.")
                return True
            done = 0
            async for msg in client.iter_messages(ev.chat_id, limit=200):
                if msg.id == ev.id:
                    continue
                sender = await msg.get_sender()
                if getattr(sender, "id", None) == getattr(target, "id", None):
                    try:
                        await client(SendReactionRequest(peer=ev.chat_id, msg_id=msg.id,
                                                         reaction=[ReactionEmoji(emoticon=emoji)]))
                        done += 1
                        await asyncio.sleep(0.8)
                    except Exception as ex:
                        logger.info("bulk reaction skipped message %s: %s", msg.id, ex)
                    if done >= 20:
                        break
            try: await ev.delete()
            except Exception: pass
            await client.send_message(ev.chat_id, f"🔥 ریاکشن {emoji} روی {done} پیام اخیر کاربر اعمال شد (سقف هر بار: ۲۰ پیام).")
        except Exception as exc:
            await _say(uid, ev, "❌ ریاکشن گروهی انجام نشد: " + str(exc)[:100])
        return True
    if cmd == "translate":
        if not ev.is_reply:
            await _say(uid, ev, "روی متن موردنظر ریپلای کن و بعد .ترجمه را بفرست.")
            return True
        try:
            reply = await ev.get_reply_message()
            source = (reply.raw_text or "").strip()
            if not source:
                await _say(uid, ev, "پیام ریپلای‌شده متن قابل ترجمه ندارد.")
            else:
                result = await _translate_text(source)
                await ev.reply(fancy_html("ترجمه:\n" + result), parse_mode="html")
                try: await ev.delete()
                except Exception: pass
        except Exception as e:
            await _say(uid, ev, "ترجمه انجام نشد؛ اتصال اینترنت سرور را بررسی کن. " + str(e)[:80])
        return True
    if cmd == "tag" and norm(arg) in ("اعضا", "members", "members all", "") :
        try:
            if not ev.is_group and not ev.is_channel:
                await _say(uid, ev, "این دستور فقط در گروه قابل استفاده است.")
                return True
            from telethon.tl.types import User
            import html as _html
            mentions = []
            async for participant in _clients[uid].iter_participants(ev.chat_id, limit=10):
                if isinstance(participant, User) and not getattr(participant, "bot", False):
                    label = _html.escape(participant.first_name or "عضو", quote=False)
                    mentions.append(f'<a href="tg://user?id={participant.id}">{label}</a>')
            if mentions:
                await ev.reply(quote_html("اعضای گروه (حداکثر ۱۰ نفر):\n" + " ".join(mentions)), parse_mode="html")
            else:
                await _say(uid, ev, "عضوی برای منشن در دسترس نبود.")
            try: await ev.delete()
            except Exception: pass
        except Exception as e:
            await _say(uid, ev, "تگ اعضا انجام نشد؛ ممکن است تلگرام دسترسی اعضا را محدود کرده باشد. " + str(e)[:70])
        return True
    if cmd == "save":
        import extras
        await _say(uid, ev, await extras.save_replied(uid, ev))
        return True
    import extras
    if cmd in extras.CMD_WORDS:
        await extras.handle_cmd(uid, ev, cmd, arg)
        return True
    # تنظیمات سلف (کلمه/زمان/گپ/دکمه/ساعت) فقط از دکمه‌های .پنل انجام می‌شوند؛
    # دستورهای ریپلایی و سریع (.کپی، .غیرفعال کپی، .ریاکشن، .ایموجی، .ساعت) توی چت کار می‌کنن.
    if cmd not in ("panel", "copy", "off", "react", "emoji", "clock", "work", "lesson"):
        try:
            await ev.delete()
        except Exception:
            pass
        return True
    now = time.time()
    h = [t for t in _cmd_hist.get(uid, []) if now - t < 60]
    if len(h) >= 40:
        return True
    h.append(now)
    _cmd_hist[uid] = h
    try:
        if cmd == "panel":
            if await send_inline_panel(uid, ev):
                return True
            if ev.chat_id == uid:      # Saved Messages: پنل همون‌جا بالا میاد
                await ev.edit(_panel_text(uid), parse_mode=None)
            else:                      # گپ عمومی: پیام دستور پاک می‌شه و پنل می‌ره توی چت ربات
                try:
                    await ev.delete()
                except Exception:
                    pass
                await send_panel(_botref[0], uid)
            return True
        if cmd in FEATS:
            await _say(uid, ev, await feat_cmd(uid, ev, cmd, arg))
            return True
        if cmd == "off":
            what = norm(re.sub(r"^فعال\s*", "", arg or ""))
            if what in ("کپی", "copy"):
                await _say(uid, ev, await copy_off(uid))
            elif what in ("ساعت", "clock"):
                await _say(uid, ev, await clock_set(uid, on=False))
            else:
                await _say(uid, ev, "❓ مثل: .غیرفعال کپی   یا   .غیرفعال ساعت")
            return True
        if cmd == "react":
            msg_out = await react_start(uid, ev, arg)
            if msg_out:
                await _say(uid, ev, msg_out)
            return True
        if cmd == "emoji":
            msg_out = await emoji_send(uid, ev, arg)
            if msg_out:
                await _say(uid, ev, msg_out)
            return True
        if cmd == "copy":
            if norm(arg) in ("خاموش", "off", "غیرفعال"):
                await _say(uid, ev, await copy_off(uid))
            else:
                await _say(uid, ev, await copy_on(uid, ev))
            return True
        if cmd == "clock":
            a = norm(arg)
            if a in ("خاموش", "off", "غیرفعال"):
                await _say(uid, ev, await clock_set(uid, on=False))
            elif a in ("فونت", "فونت ها", "فونتها", "fonts", "font", "لیست"):
                await _say(uid, ev, clock_fonts_text(uid), keep=True)
            elif fa2en(a).isdigit():
                n = int(fa2en(a))
                if 1 <= n <= len(CLOCK_FONTS):
                    await _say(uid, ev, await clock_set(uid, on=True, font=n))
                else:
                    await _say(uid, ev, clock_fonts_text(uid), keep=True)
            elif a == "":
                d = _pget(uid)
                if d.get("clock_on"):
                    await _say(uid, ev, clock_fonts_text(uid), keep=True)
                else:
                    await _say(uid, ev, await clock_set(uid, on=True))
            else:
                await _say(uid, ev, "🕒 مثل: .ساعت   |   .ساعت 3   |   .ساعت فونت   |   .غیرفعال ساعت")
            return True
        explicit, rest = split_slot(arg)
        sl = _get_slots(uid)
        if cmd == "word":
            if not rest:
                await _say(uid, ev, "✏️ مثل: .کلمه سلام   (یا اسلات مشخص: .کلمه 2 سلام)")
                return True
            if is_banned_word(rest):
                await _say(uid, ev, "⛔ این کلمه مجاز نیست.")
                return True
            i = _pick_slot(uid, sl, explicit, rest)
            sl[i]["word"] = rest[:40]
            sl[i].setdefault("delay", 0)
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ کلمه‌ی اسلات {i + 1}: «{sl[i]['word']}»\n" + _ready_text(sl[i]))
        elif cmd == "time":
            d = parse_delay(rest)
            if d is None or 0 < d < MIN_AUTO:
                await _say(uid, ev, "⏱ هر چند ثانیه خودکار بگه؟ مثل: .زمان 30   یا   .زمان 2 دقیقه\n(حداقل 5 ثانیه، حداکثر ۶۰ دقیقه؛ .زمان 0 = خودکار خاموش)")
                return True
            i = _pick_slot(uid, sl, explicit)
            sl[i]["delay"] = d
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, (f"✅ زمان اسلات {i + 1}: هر {fmt_delay(d)}\n" if d else f"✅ حالت خودکار اسلات {i + 1} خاموش شد.\n") + _ready_text(sl[i]))
        elif cmd == "btn":
            pos = _POSW.get(norm(rest))
            if not pos:
                await _say(uid, ev, "🎯 مثل: .دکمه چپ   یا   .دکمه وسط   یا   .دکمه راست   یا   .دکمه خالی")
                return True
            i = _pick_slot(uid, sl, explicit)
            sl[i]["pos"] = pos
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ دکمه‌ی اسلات {i + 1}: {POS[pos]}\n" + _ready_text(sl[i]))
        elif cmd == "chat":
            client = _clients.get(uid)
            slot_only = re.fullmatch(r"[1-3\u06f1-\u06f3]", arg or "")
            if slot_only:
                explicit, rest = int(fa2en(arg)) - 1, ""
            if not rest:      # همین گپ
                chat = await ev.get_chat()
                is_bot = bool(ev.is_private and getattr(chat, "bot", False))
                if ev.chat_id in (uid, _bot_id[0]) or not (ev.is_group or is_bot):
                    await _say(uid, ev, "💬 این دستور رو داخل گپِ هدف (گروه یا چت ربات) بنویس، یا بنویس: .گپ اسم گروه")
                    return True
                cid = ev.chat_id
                title = getattr(chat, "title", None) or getattr(chat, "first_name", None) or str(cid)
            else:
                found = await _find_chat(client, rest) if client else []
                if not found:
                    await _say(uid, ev, f"❌ گپی با اسم «{rest[:30]}» پیدا نشد (فقط گروه‌ها و چت رباتا).")
                    return True
                if len(found) > 1:
                    names = "\n".join(f"• {d[1][:30]}" for d in found[:6])
                    await _say(uid, ev, "🔎 چند تا پیدا شد؛ اسم دقیق‌تر بنویس:\n" + names)
                    return True
                cid, title = found[0][0], found[0][1]
            i = _pick_slot(uid, sl, explicit)
            sl[i]["chat"] = cid
            sl[i]["title"] = str(title)[:40]
            _put_slots(uid, sl)
            _cur[uid] = i
            await _say(uid, ev, f"✅ گپ اسلات {i + 1}: «{sl[i]['title']}»\n" + _ready_text(sl[i]))
    except Exception as e:  # noqa: BLE001
        logger.info("self dot-cmd failed %s: %s", uid, e)
    return True


# ---------- آنلاین‌بودن و بازتاب تایپینگ ----------
def _sync_presence(uid):
    """شروع/توقف تازه‌سازی وضعیت آنلاین و تایپینگ بر اساس تنظیمات ذخیره‌شده."""
    client = _clients.get(uid)
    if not client:
        return
    tasks = _feature_tasks.setdefault(uid, [])
    # پاک‌کردن تسک‌های قبلی این قابلیت‌ها؛ تسک‌های ساعت/اسلات جدا هستند.
    for task in tasks:
        task.cancel()
    tasks.clear()
    d = _pget(uid)
    if d.get("always_online"):
        async def online_loop():
            from telethon.tl.functions.account import UpdateStatusRequest
            while uid in _clients and _pget(uid).get("always_online"):
                try:
                    await client(UpdateStatusRequest(offline=False))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.info("online refresh failed for %s: %s", uid, exc)
                await asyncio.sleep(120)
        tasks.append(asyncio.create_task(online_loop()))

async def _mirror_typing(uid, client, update):
    """وقتی هر کاربری در هر گفت‌وگویی تایپ می‌کند، تایپینگ را در مقصد انتخابی نشان بده."""
    try:
        d = _pget(uid)
        target = d.get("typing_target_id")
        if not d.get("typing_mode") or not target:
            return
        from telethon.tl.types import UpdateUserTyping, UpdateChatUserTyping, UpdateChannelUserTyping
        if not isinstance(update, (UpdateUserTyping, UpdateChatUserTyping, UpdateChannelUserTyping)):
            return
        # محدودسازی نرخ: از ایجاد درخواست‌های پیاپی در گروه‌های شلوغ جلوگیری می‌کند.
        now = time.monotonic()
        if now - _typing_emit_at.get(uid, 0) < 3.5:
            return
        _typing_emit_at[uid] = now
        from telethon.tl.functions.messages import SetTypingRequest
        from telethon.tl.types import SendMessageTypingAction, SendMessageCancelAction
        peer = await client.get_input_entity(int(target))
        await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
        async def clear_later():
            await asyncio.sleep(4)
            try:
                await client(SetTypingRequest(peer=peer, action=SendMessageCancelAction()))
            except Exception:
                pass
        asyncio.create_task(clear_later())
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("typing mirror failed for %s: %s", uid, exc)

# ---------- امکانات تکمیلی پنل ----------
# فونت‌های «حالت متن» توی selffonts.py هستن (نام‌ها: selffonts.FONTS)

async def _translate_text(text):
    # Google Translate endpoint; requires server internet access.
    import urllib.parse, urllib.request, json as _json
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fa&dt=t&q=" + urllib.parse.quote(text[:3500])
    def _get():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            data = _json.loads(r.read().decode("utf-8"))
        return "".join(part[0] for part in data[0] if part and part[0])
    return await asyncio.to_thread(_get)

# ---------- پنل کاربر ----------
def _panel(uid, name=""):
    """همون پنل اصلی (.پنل): کپشن کوتاه + دکمه‌ها؛ بدون متن توضیحی بلند."""
    return _inl_caption(name, uid), _inl_kb(uid, private=True)


async def send_panel(bot, uid, name=""):
    """صفحهٔ اول پنل (بنر با عکس پروفایل کاربر + دکمهٔ «دستورات سلف») توی چت ربات."""
    cap = _inl_caption(name, uid)
    kb = _blue(_cover_kb(uid))
    url = os.environ.get("SELF_BANNER_URL", "").strip()
    photo = await user_banner(uid, fresh=True) or url or _banner.get("file_id")
    if photo:
        try:
            return await bot.send_photo(uid, photo=photo, caption=cap[:1024], reply_markup=kb)
        except Exception as e:  # noqa: BLE001
            logger.info("send_panel photo: %s", e)
    return await bot.send_message(uid, cap, reply_markup=kb)


class _PhotoMsg:
    """جای q.message وقتی پنل به‌صورت عکس (بنر) فرستاده شده؛ ویرایش از طریق کپشن."""
    is_inline = False

    def __init__(self, msg):
        self.m = msg

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kw):
        try:
            await self.m.edit_caption(caption=text[:1024], reply_markup=_blue(reply_markup), parse_mode=parse_mode)
        except Exception as e:  # noqa: BLE001
            if "not modified" not in str(e).lower():
                logger.info("photo panel edit: %s", e)

    async def reply_text(self, text, **kw):
        return await self.m.reply_text(text, **kw)


def _slot_panel(uid, i):
    row = get_row(uid)
    sl = slots_of(row)
    s = sl[i] if i < len(sl) else {}
    t = (f"{i + 1}️⃣ <b>کلمه‌ی {i + 1}</b>\n\n📝 کلمه: {s.get('word') or '—'}\n"
         f"💬 گپ: {s.get('title') or '—'}\n"
         f"🔁 تکرار خودکار: {('هر ' + fmt_delay(s.get('delay', 0))) if s.get('word') and int(s.get('delay', 0) or 0) >= MIN_AUTO else '—'}\n🎯 دکمه: {POS[s.get('pos', 'n')]}")
    kb = KB([[B("📝 کلمه", callback_data=f"self:e:{i}:word"), B("💬 گپ", callback_data=f"self:cp:{i}:0")],
             [B("⏱ تایمر", callback_data=f"self:tm:{i}"), B("🎯 دکمه", callback_data=f"self:pm:{i}")],
             [B("🗑 حذف", callback_data=f"self:d:{i}"), B("🔙 بازگشت", callback_data="self:home")]])
    return t, kb


def _get_slots(uid):
    row = get_row(uid)
    sl = slots_of(row)
    while len(sl) < MAX_SLOTS:
        sl.append({})
    return sl


def _put_slots(uid, sl):
    save_row(uid, slots=json.dumps(sl, ensure_ascii=False))
    row = get_row(uid)
    if uid in _clients:
        _load_runtime(uid, row)


def _allowed(uid):
    r = get_row(uid)
    return bool(r and r.enabled)


async def cmd_self(update, context):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return
    if not _allowed(u.id):
        raise ApplicationHandlerStop   # برای کسی که سلف نداره هیچ پیامی نمی‌آد
    await send_panel(context.bot, u.id, u.first_name or "")


async def cb_user(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not await _owner_gate(q):
        return
    if not _allowed(uid):
        await q.answer()
        return
    p = _parse_cb(q)[0].split(":")
    a = p[1]
    ud = context.user_data
    qm = q.message or _InlineMsg(context.bot, q.inline_message_id, uid)
    if q.message is not None and getattr(q.message, "photo", None):
        qm = _PhotoMsg(q.message)
    await q.answer()
    if not (a == "e" and p[3] == "word"):
        _capture.pop(uid, None)

    import selfmodes
    if a in selfmodes.CB_ACTIONS:
        await selfmodes.handle_cb(uid, a, p, qm, context)
        return
    async def home():
        ud.pop("self_st", None)
        if getattr(qm, "is_inline", False):
            await _inl_edit(context.bot, qm.imid, _inl_caption(q.from_user.first_name or "", uid), _inl_kb(uid))
            return
        t, kb = _panel(uid, q.from_user.first_name or "")
        await qm.edit_text(t, reply_markup=kb)

    import extras
    if a in ("more", "mt"):          # دیگه صفحهٔ جدا نداریم؛ همه‌چیز توی صفحهٔ دستورات سلف
        if a == "mt" and len(p) > 2:
            extras.toggle(uid, p[2])
        await home()
        return
    if a in extras.CB_ACTIONS:
        await extras.handle_cb(uid, a, p, qm, context)
        return

    if a == "ping":
        client = _clients.get(uid)
        if not client:
            await q.answer("اول اکانت سلف را وصل کن.", show_alert=True)
        else:
            started = time.perf_counter()
            try:
                await client.get_me()
                await q.answer(f"پینگ: {max(1, int((time.perf_counter()-started)*1000))} ms", show_alert=True)
            except Exception as exc:
                await q.answer("پینگ ناموفق: " + str(exc)[:100], show_alert=True)
        return
    elif a == "delhelp":
        await qm.edit_text("🗑 حذف پیام\nروی پیام موردنظر ریپلای کن و از اکانت سلف بنویس: .حذف پیام",
                           reply_markup=KB([[B("بازگشت", callback_data="self:home")]]))
    elif a == "bulkhelp":
        await qm.edit_text("🔥 ریاکشن گروهی\nروی یکی از پیام‌های کاربر ریپلای کن و بنویس: .افزودن ری اکشن 🔥\nبرای هر بار حداکثر ۲۰ پیام اخیر همان کاربر بررسی می‌شود تا از فلود جلوگیری شود.",
                           reply_markup=KB([[B("بازگشت", callback_data="self:home")]]))
    elif a in ("home", "cmds"):
        await home()
    elif a == "tog":
        r = get_row(uid)
        save_row(uid, paused=not r.paused)
        if uid in _clients:
            _load_runtime(uid, get_row(uid))
        await home()
    elif a == "s":
        i = int(p[2])
        t, kb = _slot_panel(uid, i)
        await qm.edit_text(t, reply_markup=kb, parse_mode="HTML")
    elif a == "games":
        t, kb = _games_panel(uid)
        await qm.edit_text(t, reply_markup=kb)
    elif a in ("work", "workoff", "lesson", "lessonoff"):
        kind = a[:-3] if a.endswith("off") else a
        if not feat_get(uid, kind)["allowed"]:
            t, kb = _games_panel(uid)
            await qm.edit_text(t, reply_markup=kb)
            return
        if a.endswith("off"):
            feat_save(uid, kind, active=False, chat=None, title=None)
            _sync_feat(uid, kind)
        t, kb = _feat_panel(uid, kind)
        await qm.edit_text(t, reply_markup=kb)
    elif a == "features":
        await home()
    elif a == "ft":
        key = p[2]
        d = _pget(uid)
        d[key] = not bool(d.get(key))
        _pput(uid, d)
        if key in ("always_online", "typing_mode"):
            _sync_presence(uid)
            if key == "typing_mode" and d.get(key) and not d.get("typing_target_id"):
                # با روشن‌کردن تایپینگ، انتخاب مقصد را مستقیم باز کن.
                if uid not in _clients:
                    await qm.edit_text("اول اکانتت را وصل کن.", reply_markup=KB([[B("بازگشت", callback_data="self:home")]]))
                    return
                try:
                    dialogs = await _dialogs(_clients[uid])
                    rows = [[B(name[:50], callback_data=f"self:typing_set:{cid}")] for cid, name, _ in dialogs[:35]]
                    rows.append([B("بازگشت", callback_data="self:home")])
                    await qm.edit_text("گپی را انتخاب کن که وضعیت تایپ در آن نمایش داده شود:", reply_markup=KB(rows))
                    return
                except Exception:
                    pass
        await home()
    elif a == "typing_chats":
        if uid not in _clients:
            await qm.edit_text("اول اکانتت را وصل کن.", reply_markup=KB([[B("بازگشت", callback_data="self:home")]]))
            return
        dialogs = await _dialogs(_clients[uid])
        rows = [[B(name[:50], callback_data=f"self:typing_set:{cid}")] for cid, name, _ in dialogs[:35]]
        rows.append([B("بازگشت", callback_data="self:home")])
        await qm.edit_text("گپی را انتخاب کن که وضعیت تایپ در آن نمایش داده شود:", reply_markup=KB(rows))
    elif a == "typing_set":
        try:
            target_id = int(p[2])
            client = _clients.get(uid)
            entity = await client.get_entity(target_id) if client else None
            title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or getattr(entity, "username", None) or str(target_id)
            d = _pget(uid); d["typing_target_id"] = target_id; d["typing_target_name"] = str(title)[:70]; d["typing_mode"] = True; _pput(uid, d)
            _sync_presence(uid)
            await home()
        except Exception as exc:
            await qm.edit_text("انتخاب مقصد انجام نشد: " + str(exc)[:100], reply_markup=KB([[B("بازگشت", callback_data="self:home")]]))
    elif a == "typing_clear":
        d = _pget(uid); d.pop("typing_target_id", None); d.pop("typing_target_name", None); _pput(uid, d)
        _sync_presence(uid)
        await home()
    elif a == "fonts":
        page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        tx, kb = selffonts.menu(_pget(uid), page, B, KB)
        await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
    elif a == "font":
        page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        name = ":".join(p[3:])
        if name in selffonts.FONTS:
            d = _pget(uid); d["font_mode"] = name; _pput(uid, d)
        tx, kb = selffonts.menu(_pget(uid), page, B, KB)
        await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
    elif a in ("tfx", "tspd"):
        page = int(p[2]) if len(p) > 2 and p[2].isdigit() else 0
        d = _pget(uid)
        if a == "tfx":
            d["type_fx"] = not bool(d.get("type_fx"))
        else:
            cur = d.get("type_speed", 2)
            d["type_speed"] = 1 if cur >= 3 else (cur if cur in (1, 2) else 2) + 1
        _pput(uid, d)
        tx, kb = selffonts.menu(d, page, B, KB)
        await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
    elif a == "clk":
        tx, kb = _clock_panel(uid)
        await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
    elif a in ("clkt", "clkf"):
        if uid not in _clients:
            await q.answer("اول اکانتت رو وصل کن.", show_alert=True)
            return
        if a == "clkt":
            res = await clock_set(uid, on=not _pget(uid).get("clock_on"))
        else:
            res = await clock_set(uid, on=True, font=int(p[2]))
        if res.startswith(("⚠️", "❌")):
            await q.answer(res[:180], show_alert=True)
        tx, kb = _clock_panel(uid)
        try:
            await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    elif a == "tm":
        i = int(p[2])
        pre = [5, 10, 30, 60, 300, 600]
        kb = [[B(fmt_delay(x), callback_data=f"self:t:{i}:{x}") for x in pre[:3]],
              [B(fmt_delay(x), callback_data=f"self:t:{i}:{x}") for x in pre[3:]],
              [B("✏️ عدد دلخواه", callback_data=f"self:e:{i}:delay")], [B("🔙 بازگشت", callback_data=f"self:s:{i}")]]
        await qm.edit_text("⏱ هر چند ثانیه خودش کلمه رو بگه و دکمه رو بزنه؟", reply_markup=KB(kb))
    elif a == "t":
        i, v = int(p[2]), int(p[3])
        if 0 <= v <= MAX_DELAY:
            sl = _get_slots(uid)
            sl[i]["delay"] = v
            _put_slots(uid, sl)
        tx, kb = _slot_panel(uid, i)
        await qm.edit_text(tx, reply_markup=kb, parse_mode="HTML")
    elif a == "e" and p[3] == "word":
        i = int(p[2])
        sl = _get_slots(uid)
        if uid not in _clients:
            await q.answer("اول اکانتت رو وصل کن.", show_alert=True)
            return
        if not sl[i].get("chat"):
            await q.answer("اول گپ رو انتخاب کن (دکمه‌ی 💬 گپ).", show_alert=True)
            return
        _capture[uid] = {"slot": i, "chat": sl[i]["chat"], "ts": time.time()}
        await qm.edit_text(
            f"📝 الان از <b>اکانت خودت</b> توی گپ «{sl[i].get('title')}» کلمه‌ی دلخواهت رو بفرست.\n"
            "همون لحظه خودکار ثبت می‌شه؛ لازم نیست اینجا تایپ کنی. (۵ دقیقه فرصت داری)",
            parse_mode="HTML", reply_markup=KB([[B("🔙 انصراف", callback_data=f"self:s:{i}")]]))
    elif a == "e":
        i, f = int(p[2]), p[3]
        ud["self_st"] = {"step": f, "slot": i}
        ask = {"delay": "⏱ هر چند ثانیه خودکار بگه؟ مثلاً <code>30</code> (ثانیه) یا <code>5 دقیقه</code> (حداقل ۵ ثانیه، حداکثر ۶۰ دقیقه):"}[f]
        if getattr(qm, "is_inline", False):      # ورودی متنی باید توی چت خود ربات تایپ بشه
            await context.bot.send_message(uid, ask + "\n\n(همین‌جا توی چت ربات بنویس)", parse_mode="HTML")
            return
        await qm.edit_text(ask, parse_mode="HTML", reply_markup=KB([[B("🔙 بازگشت", callback_data=f"self:s:{i}")]]))
    elif a == "pm":
        i = int(p[2])
        await qm.edit_text("🎯 کدوم دکمه‌ی پیام ربات زده بشه؟\n(⬜ خالی = هیچ دکمه‌ای زده نشه)", reply_markup=KB([
            [B(POS["l"], callback_data=f"self:p:{i}:l"), B(POS["m"], callback_data=f"self:p:{i}:m"), B(POS["r"], callback_data=f"self:p:{i}:r")],
            [B(POS["n"], callback_data=f"self:p:{i}:n")], [B("🔙 بازگشت", callback_data=f"self:s:{i}")]]))
    elif a == "p":
        i, v = int(p[2]), p[3]
        if v in POS:
            sl = _get_slots(uid)
            sl[i]["pos"] = v
            sl[i].setdefault("delay", 0)
            _put_slots(uid, sl)
        t, kb = _slot_panel(uid, i)
        await qm.edit_text(t, reply_markup=kb, parse_mode="HTML")
    elif a == "cp":
        i, pg = int(p[2]), int(p[3])
        c = _clients.get(uid)
        if not c:
            await q.answer("اول اکانتت رو وصل کن.", show_alert=True)
            return
        if pg == 0 or "self_dl" not in ud:
            ud["self_dl"] = await _dialogs(c)
        pg = max(0, pg - 1)   # مقدار callback = شماره‌ی صفحه + ۱ (۰ یعنی تازه‌سازی)
        dl = ud["self_dl"]
        per = 8
        pg = max(0, min(pg, max(0, (len(dl) - 1) // per)))
        kb = [[B(("گروه | " if g else "ربات | ") + n[:28], callback_data=f"self:cs:{i}:{pg * per + k}")] for k, (cid, n, g) in enumerate(dl[pg * per:(pg + 1) * per])]
        nav = []
        if pg > 0:
            nav.append(B("◀️", callback_data=f"self:cp:{i}:{pg}"))
        if (pg + 1) * per < len(dl):
            nav.append(B("▶️", callback_data=f"self:cp:{i}:{pg + 2}"))
        if nav:
            kb.append(nav)
        kb.append([B("🔙 بازگشت", callback_data=f"self:s:{i}")])
        await qm.edit_text("💬 کلمه توی کدوم گپ گفته بشه؟ (گروه‌ها و چت ربات‌های دیگه)" if dl else "گپی پیدا نشد.", reply_markup=KB(kb))
    elif a == "cs":
        i, k = int(p[2]), int(p[3])
        dl = ud.get("self_dl") or []
        if k >= len(dl):
            await q.answer("لیست قدیمی شده؛ دوباره باز کن.", show_alert=True)
            return
        sl = _get_slots(uid)
        sl[i]["chat"], sl[i]["title"] = dl[k][0], dl[k][1][:40]
        sl[i].setdefault("delay", 0)
        _put_slots(uid, sl)
        t, kb = _slot_panel(uid, i)
        await qm.edit_text("✅ گپ انتخاب شد.\n\n" + t, reply_markup=kb, parse_mode="HTML")
    elif a == "d":
        i = int(p[2])
        sl = _get_slots(uid)
        sl[i] = {}
        _put_slots(uid, sl)
        await home()
    elif a == "conn":
        if not configured():
            await qm.edit_text("⚠️ سلف هنوز روی سرور راه‌اندازی نشده (SELF_API_ID / SELF_API_HASH). به پشتیبانی بگو.",
                                      reply_markup=KB([[B("🔙", callback_data="self:home")]]))
            return
        ud["self_st"] = {"step": "phone"}
        await qm.edit_text(
            "🔗 <b>اتصال اکانت</b>\n\n⚠️ سلف از اکانت شخصی خودت استفاده می‌کنه؛ ریسکش با خودته و روی روباهیو کار نمی‌کنه. "
            "هر وقت خواستی «قطع اتصال» رو بزن تا سشن کامل بسته بشه.\n\n👇 دکمه‌ی «اشتراک شماره من» رو از منوی پایین بزن.",
            parse_mode="HTML", reply_markup=KB([[B("🔙 بازگشت", callback_data="self:home")]]))
        await qm.reply_text("📱 شماره‌ی خودت رو با دکمه‌ی پایین بفرست:", reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 اشتراک شماره من", request_contact=True)], [KeyboardButton("لغو")]], resize_keyboard=True, one_time_keyboard=True))
    elif a in ("k", "kx", "ky", "kc"):
        st = ud.get("self_st")
        if not st or st.get("step") != "code":
            return
        code = ud.get("self_code", "")
        if a == "kc":
            ud.pop("self_st", None)
            ud.pop("self_code", None)
            await _drop_login(uid)
            await qm.edit_text("لغو شد.")
            return
        if a == "k" and len(code) < 8:
            code += p[2]
        elif a == "kx":
            code = code[:-1]
        elif a == "ky":
            if len(code) < 5:
                await context.bot.send_message(uid, "❗️ کد حداقل ۵ رقمه.")
                return
            await qm.edit_text("⏳ در حال بررسی کد...")
            ud["self_code"] = ""
            await _sign_in(uid, context, code=code)
            return
        ud["self_code"] = code
        await _send_keypad(context.bot, uid, context, edit_msg=qm)
    elif a == "disc":
        await qm.edit_text("🔌 اتصال قطع بشه و سشن اکانتت کامل بسته بشه؟",
                                  reply_markup=KB([[B("✅ بله، قطع کن", callback_data="self:discy"), B("لغو", callback_data="self:home")]]))
    elif a == "discy":
        await stop_client(uid, logout=True, restore=True)
        save_row(uid, session_enc=None, phone=None)
        await home()


async def _drop_login(uid):
    d = _login.pop(uid, None)
    if d:
        try:
            await d["client"].disconnect()
        except Exception:
            pass



def _keypad_kb(n):
    rows = [[B(str(d), callback_data=f"self:k:{d}") for d in r] for r in ("123", "456", "789")]
    rows.append([B("⌫", callback_data="self:kx"), B("0", callback_data="self:k:0"), B("✅ تأیید", callback_data="self:ky")])
    rows.append([B("❌ انصراف", callback_data="self:kc")])
    return KB(rows)


async def _send_keypad(bot, uid, context, edit_msg=None):
    code = context.user_data.get("self_code", "")
    dots = " ".join(["●"] * len(code) + ["○"] * max(0, 5 - len(code)))
    tx = f"🔢 کد تلگرامی که برات اومد رو با دکمه‌های زیر وارد کن (کد رو داخل چت تایپ نکن):\n\n<b>{dots}</b>"
    if edit_msg:
        await edit_msg.edit_text(tx, parse_mode="HTML", reply_markup=_keypad_kb(len(code)))
    else:
        await bot.send_message(uid, tx, parse_mode="HTML", reply_markup=_keypad_kb(len(code)))


async def _sign_in(uid, context, code=None, password=None):
    ud = context.user_data
    bot = context.bot
    d = _login.get(uid)
    if not d or time.time() - d["ts"] > 600:
        ud.pop("self_st", None)
        ud.pop("self_code", None)
        await _drop_login(uid)
        await bot.send_message(uid, "⌛ زمان اتصال تموم شد؛ دوباره از /self اتصال بزن.")
        return
    from telethon import errors
    try:
        if password is None:
            await d["client"].sign_in(d["phone"], code, phone_code_hash=d["hash"])
        else:
            await d["client"].sign_in(password=password)
    except errors.SessionPasswordNeededError:
        ud["self_st"] = {"step": "pw"}
        await bot.send_message(uid, "🔐 تأیید دومرحله‌ای داری؛ رمزش رو بفرست (پیامت بعد از خوندن پاک می‌شه).")
        return
    except (errors.PhoneCodeInvalidError, errors.PasswordHashInvalidError):
        if password is None:
            ud["self_code"] = ""
            await bot.send_message(uid, "❌ کد اشتباهه؛ دوباره وارد کن.")
            await _send_keypad(bot, uid, context)
        else:
            await bot.send_message(uid, "❌ رمز اشتباهه؛ دوباره بفرست (یا لغو).")
        return
    except errors.PhoneCodeExpiredError:
        ud.pop("self_st", None)
        ud.pop("self_code", None)
        await _drop_login(uid)
        await bot.send_message(uid, "⌛ کد منقضی شد؛ از /self دوباره اتصال بزن و کد جدید رو سریع با شماره‌گیر وارد کن.")
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("self sign_in: %s", e)
        await bot.send_message(uid, "⚠️ خطا: " + str(e)[:150])
        return
    save_row(uid, session_enc=enc(d["client"].session.save()), phone=d["phone"])
    await _drop_login(uid)
    ud.pop("self_st", None)
    ud.pop("self_code", None)
    ok = await start_client(uid)
    if not ok:
        await bot.send_message(uid, "⚠️ ذخیره شد ولی اتصال بالا نیومد.")
    await send_panel(bot, uid)


async def user_text(update, context):
    msg = update.effective_message
    u = update.effective_user
    if not msg or not u or update.effective_chat.type != "private" or not msg.text:
        return
    text = msg.text.strip()
    ud = context.user_data
    # --- ورودی‌های مدیر ---
    aa = ud.get("admin_action")
    if isinstance(aa, str) and aa.startswith("self_") and u.id in ADMIN_IDS:
        await _admin_input(update, context, aa, text)
        raise ApplicationHandlerStop
    if text == "سلف":
        await cmd_self(update, context)   # برای کاربر بدون سلف ساکت است
        raise ApplicationHandlerStop
    st = ud.get("self_st")
    if not st:
        return
    uid = u.id
    if not _allowed(uid):
        ud.pop("self_st", None)
        return
    import extras
    if st.get("step") in extras.TEXT_STEPS:
        ud.pop("self_st", None)
        await extras.handle_text(uid, st, text, msg, context)
        raise ApplicationHandlerStop
    raise_stop = True
    try:
        if text in ("لغو", "/cancel"):
            ud.pop("self_st", None)
            await _drop_login(uid)
            await msg.reply_text("لغو شد.", reply_markup=ReplyKeyboardRemove())
        elif st["step"] == "delay":
            i, f = st["slot"], st["step"]
            sl = _get_slots(uid)
            if f == "delay":
                v = parse_delay(text)
                if v is None or 0 < v < MIN_AUTO:
                    await msg.reply_text("❗️ عدد درست بفرست؛ مثلاً 30 یا 5 دقیقه (حداقل ۵ ثانیه، حداکثر ۶۰ دقیقه).")
                    raise ApplicationHandlerStop
            else:
                v = text[:40]
            sl[i][f] = v
            sl[i].setdefault("delay", 0)
            _put_slots(uid, sl)
            ud.pop("self_st", None)
            t, kb = _slot_panel(uid, i)
            await msg.reply_text("✅ ذخیره شد.\n\n" + t, reply_markup=kb, parse_mode="HTML")
        elif st["step"] == "phone":
            await msg.reply_text("📱 شماره رو تایپ نکن؛ فقط با دکمه‌ی «اشتراک شماره من» از منوی پایین بفرست.", reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 اشتراک شماره من", request_contact=True)], [KeyboardButton("لغو")]], resize_keyboard=True, one_time_keyboard=True))
        elif st["step"] == "code":
            try:
                await msg.delete()   # اگه کد رو تایپ کرد، فوراً پاک می‌شه
            except Exception:
                pass
            await msg.reply_text("🔢 کد رو تایپ نکن (تلگرام باطلش می‌کنه)؛ با شماره‌گیر زیر وارد کن 👇")
            await _send_keypad(context.bot, uid, context)
        elif st["step"] == "pw":
            await _sign_in(uid, context, password=text)
            try:
                await msg.delete()
            except Exception:
                pass
    except ApplicationHandlerStop:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("self user_text: %s", e)
        await msg.reply_text("⚠️ خطا: " + str(e)[:150])
    if raise_stop:
        raise ApplicationHandlerStop


async def on_contact(update, context):
    msg = update.effective_message
    u = update.effective_user
    if not msg or not msg.contact or not u or update.effective_chat.type != "private":
        return
    st = context.user_data.get("self_st")
    if not st or st.get("step") != "phone" or not _allowed(u.id):
        return
    c = msg.contact
    if c.user_id != u.id:   # فقط شماره‌ی خودِ کاربر؛ مخاطب دیگران پذیرفته نمی‌شه
        await msg.reply_text("❌ فقط شماره‌ی خودت قبوله؛ با دکمه‌ی «اشتراک شماره من» بفرست.")
        raise ApplicationHandlerStop
    phone = "+" + re.sub(r"\D", "", c.phone_number or "")
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        await _drop_login(u.id)
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        _login[u.id] = {"client": client, "phone": phone, "hash": sent.phone_code_hash, "ts": time.time()}
        st["step"] = "code"
        context.user_data["self_code"] = ""
        await msg.reply_text("📨 کد تلگرام برات اومد (توی اپ تلگرام، چت «Telegram»).", reply_markup=ReplyKeyboardRemove())
        await _send_keypad(context.bot, u.id, context)
    except Exception as e:  # noqa: BLE001
        logger.warning("self contact: %s", e)
        await msg.reply_text("⚠️ خطا: " + str(e)[:150], reply_markup=ReplyKeyboardRemove())
    raise ApplicationHandlerStop




# ---------- پنل اینلاین (وقتی «پنل» یا .پنل می‌نویسی، پیامت به پنل تصویری ربات تبدیل می‌شه) ----------
# نیازمند: توی BotFather برای ربات /setinline بزن (Inline mode روشن باشه).
# عکس بنر: فایل banner.jpg (یا banner.png) رو کنار selfbot.py توی ریپو بذار، یا SELF_BANNER_URL = آدرس https عکس؛ بدون اون یه بنر خودکار ساخته می‌شه.
_banner = {"file_id": None}
_panel_msg = {}     # uid -> (chat_id, message_id) آخرین پنلی که فرستاده شده

def _make_banner():
    import io
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1280, 360
    img = Image.new("RGB", (W, H))
    px = img.load()
    for x in range(W):
        for y in range(H):
            t = x / W
            px[x, y] = (int(18 + 70 * t), int(6 + 14 * t), int(48 + 120 * t * (1 - 0.35 * y / H)))
    d = ImageDraw.Draw(img)
    d.ellipse((50, 50, 310, 310), outline=(190, 150, 255), width=8)
    d.ellipse((72, 72, 288, 288), fill=(245, 242, 255))
    try:
        big, mid, small = (ImageFont.load_default(size=n) for n in (110, 46, 34))
    except Exception:  # noqa: BLE001
        big = mid = small = ImageFont.load_default()
    d.text((180, 180), "S", font=big, fill=(70, 30, 150), anchor="mm")
    d.line((360, 70, 360, 290), fill=(190, 150, 255), width=4)
    d.text((400, 130), "SELF PANEL", font=big, fill=(255, 255, 255), anchor="lm")
    d.text((404, 235), "fast   -   simple   -   private", font=mid, fill=(205, 185, 255), anchor="lm")
    d.rectangle((0, H - 10, W, H), fill=(150, 90, 255))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return buf


async def prepare_banner(app):
    """بنر رو یه بار می‌سازه و با آپلود به ادمین، file_id می‌گیره (پیام بعدش پاک می‌شه)."""
    if os.environ.get("SELF_BANNER_URL", "").strip() or not ADMIN_IDS:
        return
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        mine = next((os.path.join(here, n) for n in ("banner.jpg", "banner.jpeg", "banner.png") if os.path.exists(os.path.join(here, n))), None)
        if mine:       # عکس خودت: فایل banner.jpg کنار selfbot.py
            with open(mine, "rb") as f:
                m = await app.bot.send_photo(chat_id=min(ADMIN_IDS), photo=f.read(), disable_notification=True)
        else:
            m = await app.bot.send_photo(chat_id=min(ADMIN_IDS), photo=_make_banner(), disable_notification=True)
        _banner["file_id"] = m.photo[-1].file_id
        try:
            await m.delete()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.warning("self banner: %s", e)


# ---------- بنر شخصی: عکس پروفایل خود کاربر داخل دایرهٔ بنر ----------
# فایل banner_base.png (همین عکس سلف روباهیو، ۱۲۸۰×۷۲۰) باید کنار selfbot.py باشه.
_BASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banner_base.png")
_CIRCLE = (300, 450, 198)       # مرکز x، مرکز y، شعاع دایرهٔ داخلی بنر
_ub = {}                        # uid -> {"key": هش عکس, "fid": file_id, "ts": زمان بررسی}
_ub_lock = asyncio.Lock()


def _circle_banner(photo_bytes):
    import io
    from PIL import Image, ImageDraw
    base = Image.open(_BASE_PATH).convert("RGB")
    cx, cy, r = _CIRCLE
    if photo_bytes:
        ph = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
        w, h = ph.size
        m = min(w, h)
        ph = ph.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m)).resize((2 * r, 2 * r), Image.LANCZOS)
        S = 4   # لبهٔ صاف دایره
        mask = Image.new("L", (2 * r * S, 2 * r * S), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 2 * r * S - 1, 2 * r * S - 1), fill=255)
        mask = mask.resize((2 * r, 2 * r), Image.LANCZOS)
        base.paste(ph, (cx - r, cy - r), mask)
    buf = io.BytesIO()
    base.save(buf, "JPEG", quality=92)
    buf.seek(0)
    return buf


async def _profile_photo_bytes(uid):
    client = _clients.get(uid)
    if client:
        try:
            data = await client.download_profile_photo("me", file=bytes)
            if data:
                return bytes(data)
        except Exception as e:  # noqa: BLE001
            logger.info("profile photo (userbot) %s: %s", uid, e)
    bot = _botref[0]
    if bot:
        try:
            ps = await bot.get_user_profile_photos(uid, limit=1)
            if ps and ps.total_count:
                f = await bot.get_file(ps.photos[0][-1].file_id)
                return bytes(await f.download_as_bytearray())
        except Exception as e:  # noqa: BLE001
            logger.info("profile photo (bot) %s: %s", uid, e)
    return None


async def user_banner(uid, fresh=False):
    """file_id بنر مخصوص این کاربر (عکس پروفایل خودش داخل دایره). اگه نشد None → بنر پیش‌فرض."""
    bot = _botref[0]
    c = _ub.get(uid)
    if c and not fresh and time.time() - c["ts"] < 600:
        return c["fid"]
    if not bot or not os.path.exists(_BASE_PATH):
        return c["fid"] if c else None
    async with _ub_lock:
        c = _ub.get(uid)
        if c and not fresh and time.time() - c["ts"] < 600:
            return c["fid"]
        try:
            data = await asyncio.wait_for(_profile_photo_bytes(uid), 8)
            key = hashlib.sha1(data).hexdigest() if data else "none"
            if c and c["key"] == key:
                c["ts"] = time.time()
                return c["fid"]
            buf = await asyncio.to_thread(_circle_banner, data)
            target = min(ADMIN_IDS) if ADMIN_IDS else uid
            m = await bot.send_photo(chat_id=target, photo=buf.getvalue(), disable_notification=True)
            fid = m.photo[-1].file_id
            try:
                await m.delete()
            except Exception:  # noqa: BLE001
                pass
            _ub[uid] = {"key": key, "fid": fid, "ts": time.time()}
            return fid
        except Exception as e:  # noqa: BLE001
            logger.warning("user banner %s: %s", uid, e)
            return c["fid"] if c else None


def _blue(kb):
    """دکمه‌های پنل: ردیف‌ها یک‌درمیون؛ ردیف اول آبی (primary)، ردیف بعد بی‌رنگ (بدون style)، و همین‌طور تا آخر. بدون ایموجی."""
    if kb is None or not getattr(kb, "inline_keyboard", None):
        return kb
    rows = []
    for i, row in enumerate(kb.inline_keyboard):
        nr = []
        for b in row:
            kw = {k: v for k, v in (b.api_kwargs or {}).items() if k != "style"}
            if i % 2 == 0:
                kw["style"] = "primary"
            extra = {}
            if b.callback_data is not None:
                extra["callback_data"] = b.callback_data
            if getattr(b, "url", None):
                extra["url"] = b.url
            if getattr(b, "switch_inline_query", None) is not None:
                extra["switch_inline_query"] = b.switch_inline_query
            nr.append(_IKB(_clean(b.text), api_kwargs=kw, **extra))
        rows.append(nr)
    return KB(rows)


def _cover_kb(uid):
    """صفحهٔ اول پنل: فقط یک دکمه."""
    return KB([[B("دستورات سلف", callback_data="self:cmds", style="primary")]])


def _inl_caption(name, uid=None):
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(CLOCK_TZ)).strftime("%H:%M")
    except Exception:  # noqa: BLE001
        now = datetime.now(timezone.utc).strftime("%H:%M")
    cap = f"› Panel {name} {now}".replace("  ", " ")
    if uid is not None:
        # دستورها (.کپی، .حساب، .تاریخ...)، فونت، بازی و ساعت فقط وقتی کار می‌کنن که اکانت وصل باشه.
        cap += "\nاتصال اکانت: " + ("وصل ✅" if uid in _clients else "قطع ❌ — اول «اتصال اکانت» رو بزن (توی ربات /self)")
    return cap


_panel_ctx = {}     # uid -> {"chat":..., "reply": آیدی پیامی که پنل روش ریپلای شده}


def _inl_kb(uid, private=False):
    row = get_row(uid)
    clock = bool(_pget(uid).get("clock_on"))
    paused = bool(row and row.paused)
    rows = [
        [B("ساعت" + (" (روشن)" if clock else ""), callback_data="self:clk", style="success" if clock else "primary"),
         B("تنظیم بازی", callback_data="self:games", style="primary")],
        [B("ریاکشن", callback_data="selfi:react", style="primary"),
         B("کپی پروفایل", callback_data="selfi:copy", style="primary")],
        [B("فعال‌سازی" if paused else "توقف موقت", callback_data="self:tog", style="success" if paused else "danger")],
    ]
    if private:
        rows.pop(1)   # ریاکشن/کپی نیاز به ریپلای در چت دارند؛ در چت ربات نمایش داده نمی‌شوند
    d = _pget(uid)
    def _st(k): return "روشن" if d.get(k) else "خاموش"
    def _sty(k): return "success" if d.get(k) else "primary"
    rows.insert(2, [B("همیشه آنلاین: " + _st("always_online"), callback_data="self:ft:always_online", style=_sty("always_online")),
                    B("تایپینگ: " + _st("typing_mode"), callback_data="self:ft:typing_mode", style=_sty("typing_mode"))])
    tgt = d.get("typing_target_name")
    trow = [B(("گپ تایپینگ: " + str(tgt)[:18]) if tgt else "انتخاب گپ تایپینگ", callback_data="self:typing_chats", style="primary")]
    if tgt:
        trow.append(B("حذف مقصد", callback_data="self:typing_clear", style="danger"))
    rows.insert(3, trow)
    rows.insert(4, [B(selffonts.label(d), callback_data="self:fonts", style="primary")])
    rows.insert(2, [B("🏓 پینگ", callback_data="self:ping", style="primary"),
                    B("🗑 حذف پیام", callback_data="self:delhelp", style="danger")])
    rows.insert(3, [B("🔥 ریاکشن گروهی", callback_data="self:bulkhelp", style="primary")])
    rows.insert(4, [B("🔒 قفل پیوی: " + _st("pv_lock"), callback_data="self:ft:pv_lock", style=_sty("pv_lock")),
                    B("📢 عضویت اجباری پیوی: " + _st("pv_join"), callback_data="self:ft:pv_join", style=_sty("pv_join"))])
    try:
        import selfmodes
        rows.extend(selfmodes.panel_rows(d))
    except Exception as e:  # noqa: BLE001
        logger.warning("selfmodes panel: %s", e)
    try:
        import extras
        rows.extend(extras.more_rows(uid))      # همهٔ امکانات بیشتر توی همین صفحه
    except Exception as e:  # noqa: BLE001
        logger.warning("extras rows: %s", e)
        rows.append([B("امکانات بیشتر", callback_data="self:more")])
    last = []
    uname = getattr(_botref[0], "username", "") if _botref[0] else ""
    if private:
        rows.append([B("🔌 قطع اتصال" if uid in _clients else "🔗 اتصال اکانت",
                       callback_data="self:disc" if uid in _clients else "self:conn", style="danger" if uid in _clients else "success")])
    elif uname:
        last.insert(0, B("تنظیمات سلف", url=f"https://t.me/{uname}?start=panel", style="primary"))
    if last:
        rows.append(last)
    rows.append([B("بستن پنل", callback_data="selfi:close", style="danger")])
    return KB(rows)


def _inl_back_kb():
    return KB([[B("بازگشت", callback_data="self:home", style="primary")],
               [B("بستن پنل", callback_data="selfi:close", style="danger")]])


def _games_panel(uid):
    sl = slots_of(get_row(uid))
    lines = ["› تنظیم بازی", "", "هر اسلات: کلمه + گپ + تایمر + دکمه. بعد از تنظیم، سلف خودش کلمه رو می‌گه و دکمه رو می‌زنه.", ""]
    kb = []
    for i in range(MAX_SLOTS):
        s = sl[i] if i < len(sl) else {}
        lines.append(_slot_line(i, s))
        t = f"{i + 1}. «{s['word']}» {(s.get('title') or '')[:12]} {POS[s.get('pos', 'n')]}" if s.get("word") else f"{i + 1}. تنظیم کلمه"
        kb.append([B(t[:60], callback_data=f"self:s:{i}", style="primary")])
    for kind, f in FEATS.items():     # فقط برای کاربرانی که پشتیبانی فعال کرده
        w = feat_get(uid, kind)
        if w["allowed"]:
            kb.append([B(f["label"] + (" (روشن)" if w["active"] else ""), callback_data=f"self:{kind}",
                         style="success" if w["active"] else "primary")])
    kb.append([B("بازگشت", callback_data="self:home", style="primary")])
    return "\n".join(lines)[:1000], KB(kb)


# ---------- ایموجی پرمیوم با کد (برای همهٔ کاربرها، بدون نیاز به فعال‌بودن سلف) ----------
# کاربر می‌نویسه: @آیدی_ربات سلام [5260416394419265081]  ← زیرش گزینهٔ «تبدیل به ایموجی پرمیوم» میاد.
# نکته: تلگرام فقط به رباتی اجازهٔ custom_emoji می‌ده که یوزرنیم اضافه از Fragment خریده باشه.
_PE_RE = re.compile(r"\[\s*([0-9۰-۹٠-٩]{5,25})\s*\]")
_PE_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PE_FALLBACK = "⭐"


def _u16(t):
    return len(t.encode("utf-16-le")) // 2


async def _premium_results(bot, q):
    """اگه توی متن [کد] بود، نتیجهٔ اینلاین (تبدیل به ایموجی پرمیوم) برمی‌گردونه؛ وگرنه None."""
    ms = list(_PE_RE.finditer(q))[:50]
    if not ms:
        return None
    ids = [m.group(1).translate(_PE_DIG) for m in ms]
    emo = {}
    try:
        for st in await bot.get_custom_emoji_stickers(list(dict.fromkeys(ids))):
            emo[st.custom_emoji_id] = st.emoji or _PE_FALLBACK
        bad = [i for i in ids if i not in emo]
    except Exception as e:
        logger.info("premium emoji lookup: %s", e)
        bad = []        # بررسی نشد؛ بذار خود تلگرام موقع ارسال تصمیم بگیره
    if bad:
        msg = "❌ کد ایموجی معتبر نیست: " + bad[0]
        return [InlineQueryResultArticle(id="pebad" + hashlib.sha1(q.encode()).hexdigest()[:12], title=msg,
                                         description="کد را از ربات ایدی ایموجی پرمیوم بگیر",
                                         input_message_content=InputTextMessageContent(msg))]
    text, ents, last = "", [], 0
    for m, cid in zip(ms, ids):
        text += q[last:m.start()]
        ph = emo.get(cid, _PE_FALLBACK)
        ents.append(MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=_u16(text), length=_u16(ph), custom_emoji_id=cid))
        text += ph
        last = m.end()
    text += q[last:]
    return [InlineQueryResultArticle(
        id="pe" + hashlib.sha1(q.encode()).hexdigest()[:16],
        title="✨ تبدیل به ایموجی پرمیوم",
        description=text[:100],
        input_message_content=InputTextMessageContent(text, entities=ents))]


def _premium_hint():
    t = ("✨ ارسال ایموجی پرمیوم با کد\n\n"
         "آیدی ربات را بنویس، متنت را بنویس، و کد ایموجی را داخل [ ] بگذار:\n"
         "@" + (os.environ.get("BOT_USERNAME", "").strip().lstrip("@") or "آیدی_ربات") + " سلام [5260416394419265081]\n\n"
         "بعد گزینهٔ «تبدیل به ایموجی پرمیوم» را بزن.")
    return InlineQueryResultArticle(
        id="pehint", title="✨ ارسال ایموجی پرمیوم",
        description="متن [کد ایموجی] — راهنما",
        input_message_content=InputTextMessageContent(t))


async def on_inline_query(update, context):
    iq = update.inline_query
    uid = iq.from_user.id
    q = iq.query or ""
    pe = await _premium_results(context.bot, q)
    if pe is not None:
        await iq.answer(pe, cache_time=0, is_personal=True)
        return
    if not (_allowed(uid) or uid in ADMIN_IDS):
        await iq.answer([_premium_hint()] if not q.strip() else [], cache_time=0, is_personal=True)
        return
    cap = _inl_caption(iq.from_user.first_name or "", uid)
    kb = _lock_kb(_blue(_cover_kb(uid)), uid)
    rid = f"panel{int(time.time())}"
    url = os.environ.get("SELF_BANNER_URL", "").strip()
    fid = await user_banner(uid) or _banner["file_id"]
    if fid:
        res = InlineQueryResultCachedPhoto(id=rid, photo_file_id=fid, caption=cap, reply_markup=kb)
    elif url:
        res = InlineQueryResultPhoto(id=rid, photo_url=url, thumbnail_url=url, caption=cap, reply_markup=kb)
    else:
        res = InlineQueryResultArticle(id=rid, title="Panel", input_message_content=InputTextMessageContent(cap), reply_markup=kb)
    await iq.answer([res] + ([_premium_hint()] if not q.strip() else []), cache_time=0, is_personal=True)


_inl_owner = {}   # inline_message_id -> صاحب پنل


def _parse_cb(q):
    """callback_data → (داده‌ی اصلی، آیدی صاحب پنل یا None)."""
    base, _, owner = (q.data or "").partition("|")
    return base, (int(owner) if owner.isdigit() else None)


def _lock_kb(kb, uid):
    """به همه‌ی دکمه‌های پنل اینلاین آیدی صاحب رو می‌چسبونه تا کس دیگه‌ای نتونه بزنه."""
    if uid is None or kb is None or not getattr(kb, "inline_keyboard", None):
        return kb
    rows = []
    for row in kb.inline_keyboard:
        nr = []
        for b in row:
            cd = b.callback_data
            if cd and "|" not in cd and len(cd) + len(str(uid)) + 1 <= 64:
                b = _IKB(b.text, callback_data=f"{cd}|{uid}", api_kwargs=dict(b.api_kwargs or {}))
            nr.append(b)
        rows.append(nr)
    return KB(rows)


async def _owner_gate(q):
    """True یعنی اجازه‌ی ادامه؛ وگرنه به کاربرِ غیرصاحب پیام رد می‌ده."""
    base, owner = _parse_cb(q)
    if q.inline_message_id and owner is None:
        await q.answer("🔒 این پنل قدیمیه؛ توی چت دوباره «.پنل» بنویس.", show_alert=True)
        return False
    if owner is not None and q.from_user.id != owner:
        await q.answer("🔒 این پنل مال کس دیگه‌ایه. خودت توی ربات سلف رو فعال کن و پنل خودت رو بیار.", show_alert=True)
        return False
    if q.inline_message_id and owner is not None:
        _inl_owner[q.inline_message_id] = owner
    return True


async def _inl_edit(bot, imid, text, kb, parse_mode=None):
    kb = _lock_kb(_blue(kb), _inl_owner.get(imid))
    try:
        await bot.edit_message_caption(inline_message_id=imid, caption=text[:1024], reply_markup=kb, parse_mode=parse_mode)
        return
    except Exception as e:  # noqa: BLE001
        if "not modified" in str(e).lower():
            return
    try:
        await bot.edit_message_text(text, inline_message_id=imid, reply_markup=kb, parse_mode=parse_mode)
    except Exception as e:  # noqa: BLE001
        logger.info("self inline edit: %s", e)


class _InlineMsg:
    """جای q.message برای پیام‌های اینلاین (ربات فقط inline_message_id داره)."""
    is_inline = True

    def __init__(self, bot, imid, uid):
        self.bot, self.imid, self.uid = bot, imid, uid

    async def edit_text(self, text, reply_markup=None, parse_mode=None, **kw):
        await _inl_edit(self.bot, self.imid, text, reply_markup, parse_mode)

    async def reply_text(self, text, **kw):
        return await self.bot.send_message(self.uid, text, **kw)


class _FakeEv:
    is_reply = True

    def __init__(self, client, chat, mid):
        self.client, self.chat, self.mid = client, chat, mid

    async def get_reply_message(self):
        return await self.client.get_messages(self.chat, ids=self.mid)


_REACT_SETS = {"h": ["❤"], "f": ["🔥"], "l": ["👍"], "a": ["❤", "🔥", "👍"]}


async def cb_inline(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not await _owner_gate(q):
        return
    if not _allowed(uid):
        await q.answer("این پنل برای تو نیست.", show_alert=True)
        return
    p = _parse_cb(q)[0].split(":")
    a = p[1]
    imid = q.inline_message_id
    bot = context.bot
    ctx = _panel_ctx.get(uid) or {}
    client = _clients.get(uid)
    if a == "close":
        await q.answer()
        pm = _panel_msg.pop(uid, None)
        if pm and client:
            try:
                await client.delete_messages(pm[0], [pm[1]])
                return
            except Exception:  # noqa: BLE001
                pass
        if imid:
            await _inl_edit(bot, imid, "پنل بسته شد.", None)
        elif q.message:
            try:
                await q.message.delete()
            except Exception:  # noqa: BLE001
                pass
    elif a == "react" and imid:
        await q.answer()
        kb = KB([[B("قلب", callback_data="selfi:r:h", style="danger"), B("آتش", callback_data="selfi:r:f", style="danger"),
                  B("لایک", callback_data="selfi:r:l", style="success")],
                 [B("هر سه (پریمیوم)", callback_data="selfi:r:a", style="primary")],
                 [B("بازگشت", callback_data="self:home", style="primary")]])
        tx = "› ریاکشن\n\n" + ("روی پیامی که این پنل رو بهش ریپلای کردی ریاکشن می‌زنه." if ctx.get("reply")
                               else "اول روی پیام کاربر ریپلای کن و بنویس پنل؛ بعد ریاکشن بزن.")
        await _inl_edit(bot, imid, tx, kb)
    elif a == "r":
        if not client or not ctx.get("reply"):
            await q.answer("این پنل رو باید روی پیام کاربر ریپلای کنی (روی پیامش ریپلای بزن و بنویس پنل).", show_alert=True)
            return
        try:
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            me = await client.get_me()
            emos = _REACT_SETS.get(p[2], ["❤"])[: 3 if getattr(me, "premium", False) else 1]
            await client(SendReactionRequest(peer=ctx["chat"], msg_id=ctx["reply"], reaction=[ReactionEmoji(emoticon=e) for e in emos]))
            await q.answer("ریاکشن زده شد.")
        except Exception as e:  # noqa: BLE001
            logger.info("self panel react %s: %s", uid, e)
            await q.answer("نشد: " + str(e)[:150], show_alert=True)
    elif a == "copy" and imid:
        await q.answer()
        kb = KB([[B("کپی پروفایل کاربر", callback_data="selfi:cp1", style="success")],
                 [B("برگشت به پروفایل خودم", callback_data="selfi:cp0", style="danger")],
                 [B("بازگشت", callback_data="self:home", style="primary")]])
        tx = "› کپی پروفایل\n\n" + ("اسم، بیو و عکس صاحب اون پیام کپی می‌شه." if ctx.get("reply")
                                   else "اول روی پیام کاربر ریپلای کن و بنویس پنل؛ بعد کپی بزن.")
        await _inl_edit(bot, imid, tx, kb)
    elif a in ("cp1", "cp0") and imid:
        if a == "cp1" and (not client or not ctx.get("reply")):
            await q.answer("این پنل رو باید روی پیام کاربر ریپلای کنی.", show_alert=True)
            return
        await q.answer("در حال انجام...")
        if a == "cp1":
            res = await copy_on(uid, _FakeEv(client, ctx["chat"], ctx["reply"]))
        else:
            res = await copy_off(uid)
        await _inl_edit(bot, imid, res, _inl_back_kb())
    else:
        await q.answer()


async def send_inline_panel(uid, ev):
    """پیام دستور رو پاک می‌کنه و پنل اینلاین ربات رو توی همون گپ می‌فرسته. اگه نشد False."""
    client = _clients.get(uid)
    bot = _botref[0]
    uname = getattr(bot, "username", None) if bot else None
    if not client or not uname:
        return False
    try:
        await user_banner(uid, fresh=True)     # بنر شخصی از قبل آماده بشه تا پنل سریع بیاد
        res = await client.inline_query(uname, "panel", entity=ev.chat_id)
        if not res:
            return False
        sent = await res[0].click(ev.chat_id, reply_to=ev.reply_to_msg_id if ev.is_reply else None)
        _panel_ctx[uid] = {"chat": ev.chat_id, "reply": ev.reply_to_msg_id if ev.is_reply else None}
        if sent is not None:
            _panel_msg[uid] = (ev.chat_id, sent.id)
        try:
            await ev.delete()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("self inline panel %s: %s", uid, e)
        return False


# ---------- پنل مدیر ----------
ADMIN_IDS = set()
SUPPORT_IDS = set()   # فقط پنل پشتیبانی؛ هیچ دسترسی مدیریتی ندارند


def admin_menu_kb():
    return KB([[B("➕ فعال‌سازی برای کاربر", callback_data="selfa:on")],
               [B("⏹ غیرفعال‌سازی کاربر", callback_data="selfa:off")],
               [B("🗑 حذف کامل (خروج از سشن)", callback_data="selfa:del")],
               [B("📋 لیست کاربران سلف", callback_data="selfa:list")],
               [B("🎁 ساخت کد هدیه", callback_data="gift:new")],
               [B("🎟 کدهای هدیه", callback_data="gift:list")],
               [B("🏷 ساخت کد تخفیف", callback_data="adm:dnew")],
               [B("🏷 کدهای تخفیف", callback_data="adm:dlist")],
               [B("📊 آمار ربات", callback_data="adm:stats")],
               [B("📢 اطلاعیهٔ همگانی", callback_data="adm:bc")],
               [B("🔙 بازگشت", callback_data="admin:back")]])


async def cb_admin(update, context):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("دسترسی نداری.", show_alert=True)
        return
    a = q.data.split(":")[1]
    await q.answer()
    if a == "menu":
        context.user_data.pop("admin_action", None)
        await q.message.reply_text("🤖 مدیریت سلف" + ("" if configured() else "\n\n⚠️ SELF_API_ID / SELF_API_HASH تنظیم نشده."),
                                   reply_markup=admin_menu_kb())
    elif a in ("on", "off", "del"):
        context.user_data["admin_action"] = "self_" + a
        await q.message.reply_text("🆔 آیدی عددی کاربر رو بفرست:")
    elif a == "list":
        s = get_session()
        try:
            rows = s.query(SelfAccess).all()
            lines = [f"{'✅' if r.enabled else '⏹'} <code>{r.user_id}</code> {'🔗' if r.user_id in _clients else '—'}" for r in rows]
        finally:
            s.close()
        await q.message.reply_text("📋 کاربران سلف (✅ مجاز | 🔗 وصل):\n\n" + ("\n".join(lines) if lines else "خالیه."),
                                   parse_mode="HTML", reply_markup=admin_menu_kb())


async def _admin_input(update, context, action, text):
    context.user_data.pop("admin_action", None)
    context.user_data["ai_skip_msg"] = update.message.message_id
    t = fa2en(text).strip()
    if not t.isdigit():
        context.user_data["admin_action"] = action
        await update.message.reply_text("❗️ فقط آیدی عددی بفرست.")
        return
    uid = int(t)
    bot = context.bot
    if action == "self_on":
        save_row(uid, enabled=True)
        if get_row(uid).session_enc:
            await start_client(uid)
        await update.message.reply_text(f"✅ سلف برای {uid} فعال شد.", reply_markup=admin_menu_kb())
        try:
            await bot.send_message(uid, "✅ پشتیبانی «سلف» رو برات فعال کرد.\nبرای تنظیم، /self رو بزن یا بنویس «سلف».")
        except Exception:
            pass
    elif action == "self_off":
        if not get_row(uid):
            await update.message.reply_text("❌ این کاربر سلف نداره.", reply_markup=admin_menu_kb())
            return
        save_row(uid, enabled=False)
        await stop_client(uid, restore=True)
        await update.message.reply_text(f"⏹ سلف {uid} غیرفعال شد.", reply_markup=admin_menu_kb())
    else:
        r = get_row(uid)
        if not r:
            await update.message.reply_text("❌ این کاربر سلف نداره.", reply_markup=admin_menu_kb())
            return
        await stop_client(uid, logout=True, restore=True)
        s = get_session()
        try:
            x = s.get(SelfAccess, uid)
            if x:
                s.delete(x)
            y = s.get(SelfProfile, uid)
            if y:
                s.delete(y)
            for M in (SelfWork, SelfLesson):
                wk = s.get(M, uid)
                if wk:
                    s.delete(wk)
            s.commit()
        finally:
            s.close()
        await update.message.reply_text(f"🗑 سلف {uid} کامل حذف شد.", reply_markup=admin_menu_kb())


def register(app, admin_ids):
    ADMIN_IDS.clear()
    ADMIN_IDS.update(admin_ids)
    app.add_handler(CommandHandler("self", cmd_self), group=-5)
    app.add_handler(InlineQueryHandler(on_inline_query), group=-5)
    app.add_handler(CallbackQueryHandler(cb_inline, pattern=r"^selfi:"), group=-5)
    app.add_handler(CallbackQueryHandler(cb_user, pattern=r"^self:"), group=-5)
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^selfa:"), group=-5)
    app.add_handler(MessageHandler(filters.CONTACT & filters.ChatType.PRIVATE, on_contact), group=-5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_text), group=-5)
