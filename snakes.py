# -*- coding: utf-8 -*-
"""
بازی «مار و پله» (چندنفره، شرط‌بندی با الماس، بازی توی مینی‌اپ).

جریان:
  ۱) توی گروه (که ربات توشه) یکی می‌نویسه «مار و پله 10» → شرط ۱۰ الماس از سازنده کم می‌شه و پیام لابی میاد.
  ۲) بقیه «پیوستن» رو می‌زنن (شرط از هرکدوم کم می‌شه). «ورود به مینی‌اپ» لینک بازی رو برای بازیکن باز می‌کنه.
  ۳) سازنده «شروع بازی» رو می‌زنه، یا ۱۲۰ ثانیه بعد (اگه حداقل ۲ نفر باشن) خودکار شروع می‌شه؛ با ۴ نفر فوراً.
  ۴) نوبتی تاس می‌ندازن (سمت سرور، با secrets). ۶ = نوبت دوباره. رسیدن به خونهٔ ۱۰۰ = برنده.
  ۵) برنده کل جایزه رو منهای ۵٪ مالیات می‌گیره.

قوانین: شرط حداقل ۵ و حداکثر ۱٬۰۰۰٬۰۰۰ الماس. هر کاربر همزمان فقط توی یک بازی؛ هر گروه همزمان یک بازی.
نوبت ۳۰ ثانیه‌ست؛ تاس‌نینداختن = تاس خودکار؛ ۳ بار پشت‌سرهم = حذف (شرطش می‌سوزه).
اگه ربات وسط بازی ری‌استارت بشه، بازیِ نیمه‌کاره بعد از چند دقیقه لغو و شرط همه برمی‌گرده (جدول snake_games).
"""
import asyncio
import html
import json
import logging
import os
import random
import re
import secrets
import time
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import BigInteger, Column, Integer, String, Text

import database
from database import Base, get_session

logger = logging.getLogger(__name__)

MIN_BET = 5
MAX_BET = 1_000_000
MAX_PLAYERS = 4
LOBBY_SEC = 120
TURN_SEC = 30
MAX_SKIPS = 3
TAX = Decimal("0.05")
KEEP_DONE = 1800        # بازی تمام‌شده این‌قدر ثانیه توی حافظه می‌مونه تا نتیجه‌اش دیده بشه
ORPHAN_AGE = 150        # بازیِ ذخیره‌شده‌ای که این‌قدر آپدیت نشده و توی حافظه نیست = یتیم → لغو + برگشت شرط
BOARD = 100
# تخته مطابق عکس snakes_board.webp؛ ردیف پایین عکس «شروع» (بدون شماره) و ردیف بالاش خونه‌های ۱ تا ۱۰ه؛
# شماره‌گذاری ردیف‌به‌ردیف از چپ به راست تا ۱۰۰ بالا-راست. هر مار/نردبان با جای واقعی‌اش روی عکس هماهنگ شده
# (سر مار و پایین نردبان = کلید، دم مار و بالای نردبان = مقدار). مار آبی‌سبز دمش توی ردیف شروعه (۰ = برگشت به شروع).
SNAKES = {93: 64, 88: 58, 53: 33, 57: 47, 38: 29, 22: 12, 8: 0}
LADDERS = {51: 72, 69: 89, 39: 60, 25: 46, 3: 24, 9: 29}

_PAT = re.compile(r"^\s*مار[\s\u200c]*و[\s\u200c]*پله(?:[\s\u200c]+([0-9۰-۹٠-٩,٬،]+))?\s*$")
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

GAMES = {}              # id -> dict (وضعیت زندهٔ بازی‌ها)
_LOCK = asyncio.Lock()
_app = [None]
_last_scan = [0]


class SnakeGame(Base):
    __tablename__ = "snake_games"
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(12), default="lobby")
    updated = Column(Integer, default=0)
    data = Column(Text, default="{}")


class StickerPack(Base):
    __tablename__ = "snake_sticker_packs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String(64), nullable=False)
    title = Column(String(128), nullable=False)
    stickers = Column(Text, nullable=False, default="[]")


def _cm():
    import commerce
    return commerce


def _esc(s):
    return html.escape(str(s or ""), quote=False)


def _now():
    return int(time.time())


# ---------- ذخیره‌سازی ----------
def _save(g):
    g["updated"] = _now()
    s = get_session()
    try:
        r = s.get(SnakeGame, g["id"]) if g.get("id") else None
        if not r:
            r = SnakeGame(status=g["status"], updated=g["updated"], data="{}")
            s.add(r)
            s.flush()
            g["id"] = r.id
        r.status = g["status"]
        r.updated = g["updated"]
        r.data = json.dumps(g, ensure_ascii=False)
        s.commit()
    except Exception:
        s.rollback()
        logger.exception("snakes: save failed")
    finally:
        s.close()


def _pay(uid, amount):
    try:
        _cm().change(uid, Decimal(str(amount)))
    except Exception:
        logger.exception("snakes: pay failed uid=%s amount=%s", uid, amount)


def _refund_all(g):
    for p in g["players"]:
        _pay(p["id"], g["bet"])


# ---------- کمکی‌ها ----------
def _active_of(uid):
    for g in GAMES.values():
        if g["status"] in ("lobby", "playing") and any(p["id"] == uid for p in g["players"]):
            return g
    return None


def _chat_busy(chat_id):
    return any(g["chat"] == chat_id and g["status"] in ("lobby", "playing") for g in GAMES.values())


def _log(g, text):
    g["log"].append(text)
    del g["log"][:-8]


def _cur(g):
    return g["players"][g["turn"]]


def _next(g):
    n = len(g["players"])
    for _ in range(n):
        g["turn"] = (g["turn"] + 1) % n
        if not _cur(g)["out"]:
            break
    g["deadline"] = _now() + TURN_SEC


def _finish(g, w):
    pot = g["bet"] * len(g["players"])
    payout = (Decimal(pot) * (1 - TAX)).quantize(Decimal("0.1"), ROUND_DOWN)
    g["status"] = "done"
    g["winner"] = w["id"]
    g["payout"] = str(payout)
    g["tax"] = str(Decimal(pot) - payout)
    _log(g, f"🏆 {w['name']} برنده شد")
    _save(g)          # اول وضعیت «تمام‌شده» ذخیره می‌شه، بعد پرداخت (جلوگیری از پرداخت دوباره)
    _pay(w["id"], payout)


def _play(g, p, auto):
    r = secrets.randbelow(6) + 1
    frm = p["pos"]
    pos = frm + r
    ev = None
    if pos < BOARD:
        if pos in LADDERS:
            pos, ev = LADDERS[pos], "ladder"
        elif pos in SNAKES:
            pos, ev = SNAKES[pos], "snake"
    elif pos > BOARD:
        pos = BOARD
    if pos >= BOARD:
        pos = BOARD
    p["pos"] = pos
    g["seq"] += 1
    g["last"] = {"seq": g["seq"], "pid": p["id"], "r": r, "from": frm, "to": pos, "ev": ev, "auto": auto}
    _log(g, f"{p['name']} 🎲{r}: {frm}→{pos}" + (" 🪜" if ev == "ladder" else " 🐍" if ev == "snake" else "")
         + (" (خودکار)" if auto else ""))
    p["skips"] = p.get("skips", 0) + 1 if auto else 0
    if pos >= BOARD:
        _finish(g, p)
        return
    if auto and p["skips"] >= MAX_SKIPS:
        p["out"] = True
        _log(g, f"⛔ {p['name']} به‌خاطر بی‌تحرکی حذف شد")
        alive = [x for x in g["players"] if not x["out"]]
        if len(alive) == 1:
            _finish(g, alive[0])
            return
    if r == 6 and not p["out"]:
        g["deadline"] = _now() + TURN_SEC
    else:
        _next(g)
    _save(g)


def _begin(g):
    random.shuffle(g["players"])
    g["status"] = "playing"
    g["turn"] = 0
    g["deadline"] = _now() + TURN_SEC
    _log(g, "🎲 بازی شروع شد")
    _save(g)


def _cancel(g, why):
    g["status"] = "cancelled"
    g["why"] = why
    _save(g)
    _refund_all(g)


# ---------- پیام‌های گروه ----------
def _link():
    return os.environ.get("MINIAPP_LINK", "").strip().rstrip("/")


def _btn(text, **kw):
    from telegram import InlineKeyboardButton
    style = kw.pop("style", None)
    if style:
        kw["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(text, **kw)


def _app_btn(g):
    lk = _link()
    if lk:
        return _btn("🎮 ورود به مینی‌اپ", url=f"{lk}?startapp=snk_{g['id']}", style="primary")
    return _btn("🎮 ورود به مینی‌اپ", callback_data=f"snk:app:{g['id']}", style="primary")


def _text(g):
    names = "\n".join(f"{i + 1}. {_esc(p['name'])}" + (" ⛔" if p["out"] else "") for i, p in enumerate(g["players"]))
    pot = g["bet"] * len(g["players"])
    head = f"🎲🐍 <b>مار و پله</b>\n\n💎 شرط هر نفر: <b>{g['bet']:,}</b> الماس\n💰 جایزهٔ فعلی: <b>{pot:,}</b> الماس\n"
    if g["status"] == "lobby":
        left = max(0, g["deadline"] - _now())
        return (head + f"👥 بازیکنان ({len(g['players'])}/{MAX_PLAYERS}):\n{names}\n\n"
                f"⏳ {left} ثانیه برای پیوستن (حداقل ۲ نفر). «پیوستن» رو بزن و بعد وارد مینی‌اپ شو.")
    if g["status"] == "playing":
        return head + f"👥 بازیکنان:\n{names}\n\n🎮 بازی شروع شده؛ از «ورود به مینی‌اپ» بازی کن."
    if g["status"] == "done":
        w = next((p for p in g["players"] if p["id"] == g.get("winner")), None)
        return (head + f"👥 بازیکنان:\n{names}\n\n🏆 برنده: <b>{_esc(w['name']) if w else '—'}</b>\n"
                f"🎁 جایزه: <b>{Decimal(g.get('payout', '0')).normalize():f}</b> الماس (۵٪ مالیات کسر شد)")
    if g["status"] == "refunded":
        return head + "\n♻️ بازی به‌خاطر ری‌استارت ربات لغو شد و شرط همه برگشت."
    return head + "\n❌ بازی لغو شد و شرط بازیکن‌ها برگشت."


def _kb(g):
    from telegram import InlineKeyboardMarkup
    gid = g["id"]
    if g["status"] == "lobby":
        return InlineKeyboardMarkup([[_btn("✋ پیوستن", callback_data=f"snk:join:{gid}", style="success"), _app_btn(g)],
                                     [_btn("▶️ شروع بازی", callback_data=f"snk:go:{gid}"),
                                      _btn("لغو", callback_data=f"snk:x:{gid}", style="danger")]])
    if g["status"] == "playing":
        return InlineKeyboardMarkup([[_app_btn(g)]])
    return None


async def _edit(bot, g):
    if not g.get("msg"):
        return
    try:
        await bot.edit_message_text(chat_id=g["chat"], message_id=g["msg"], text=_text(g), parse_mode="HTML",
                                    reply_markup=_kb(g))
    except Exception as e:  # noqa: BLE001
        if "not modified" not in str(e).lower():
            logger.info("snakes: edit failed: %s", e)


def _open_kb(gid):
    from telegram import InlineKeyboardMarkup, WebAppInfo
    import miniapp
    return InlineKeyboardMarkup([[_btn("🎮 ورود به بازی", web_app=WebAppInfo(url=f"{miniapp.app_url()}?g={gid}"))]])


async def _pm_players(bot, g):
    for p in g["players"]:
        try:
            await bot.send_message(p["id"], f"🎲🐍 مار و پله شروع شد! شرط: {g['bet']:,} الماس\nبرای بازی دکمهٔ زیر رو بزن 👇",
                                   reply_markup=_open_kb(g["id"]))
        except Exception:
            pass


async def _announce(bot, g):
    """بعد از تغییر وضعیت (شروع/پایان/لغو) پیام گروه رو آپدیت می‌کنه."""
    await _edit(bot, g)
    if g["status"] == "playing":
        await _pm_players(bot, g)


# ---------- دستور ساخت بازی ----------
async def cmd_new(update, context):
    m = update.effective_message
    u = update.effective_user
    if not m or not u or not m.text or update.effective_chat.type == "private":
        return
    cm = _cm()
    import miniapp
    mt = _PAT.match(m.text)
    raw = (mt.group(1) if mt else None) or ""
    usage = (f"🎲🐍 برای ساخت مار و پله بنویس: «مار و پله 10» (شرط به الماس)\n"
             f"حداقل {MIN_BET} و حداکثر {MAX_BET:,} الماس.")
    if not raw:
        await m.reply_text(usage)
        return
    try:
        bet = int(raw.translate(_DIG).replace(",", "").replace("٬", "").replace("،", ""))
    except ValueError:
        await m.reply_text(usage)
        return
    if bet < MIN_BET:
        await m.reply_text(f"❌ حداقل شرط {MIN_BET} الماسه.")
        return
    if bet > MAX_BET:
        await m.reply_text(f"❌ سقف شرط {MAX_BET:,} الماسه.")
        return
    if not miniapp.app_url():
        await m.reply_text("❌ مینی‌اپ هنوز تنظیم نشده؛ به پشتیبانی خبر بده.")
        return
    if not await cm.is_member(context.bot, u.id):
        await m.reply_text("برای بازی باید عضو کانال‌های ربات باشی؛ ربات رو استارت کن و عضویت رو تأیید کن.")
        return
    async with _LOCK:
        if _chat_busy(m.chat_id):
            await m.reply_text("⏳ توی این گروه یه مار و پله در جریانه؛ صبر کن تموم بشه.")
            return
        if _active_of(u.id):
            await m.reply_text("تو الان توی یه مار و پلهٔ دیگه‌ای؛ اول اون تموم بشه.")
            return
        bal = cm.wallet(u.id)["balance"]
        if bal < bet:
            await m.reply_text(f"❌ الماس کافی نداری.\n💎 موجودی: {cm.fmt_bal(bal)} | شرط: {bet:,}")
            return
        cm.change(u.id, -bet)
        g = {"id": None, "chat": m.chat_id, "msg": None, "creator": u.id, "bet": bet, "status": "lobby",
             "players": [{"id": u.id, "name": (u.first_name or "بازیکن")[:20], "pos": 0, "out": False, "skips": 0}],
             "turn": 0, "deadline": _now() + LOBBY_SEC, "seq": 0, "last": None, "log": [], "winner": None,
             "created": _now()}
        _save(g)
        if not g["id"]:        # ذخیره نشد (خطای دیتابیس) → شرط برگرده
            cm.change(u.id, bet)
            await m.reply_text("❌ خطای داخلی؛ دوباره امتحان کن.")
            return
        GAMES[g["id"]] = g
        await _send_board(m)
        try:
            sent = await m.reply_text(_text(g), parse_mode="HTML", reply_markup=_kb(g))
            g["msg"] = sent.message_id
            _save(g)
        except Exception:
            logger.exception("snakes: lobby message failed")
            del GAMES[g["id"]]
            _cancel(g, "send failed")


CHAT_IMG = "snakes_chat.jpg"        # عکس تخته برای گپ (JPEG چون تلگرام WebP رو به‌عنوان عکس قبول نمی‌کنه)
_PHOTO = [None]                     # بعد از اولین ارسال، file_id تلگرام نگه داشته می‌شه تا دوباره آپلود نشه


async def _send_board(m):
    """عکس تخته (همون عکس مینی‌اپ) رو قبل از پیام بازی توی گپ می‌فرسته؛ خطاش هیچ‌وقت بازی رو خراب نمی‌کنه."""
    for _ in range(2):
        try:
            src = _PHOTO[0]
            if src is None:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), CHAT_IMG), "rb") as f:
                    src = f.read()
            sent = await m.reply_photo(src)
            if sent.photo:
                _PHOTO[0] = sent.photo[-1].file_id
            return
        except Exception:
            if _PHOTO[0] is None:
                logger.warning("snakes: sending board photo failed", exc_info=True)
                return
            _PHOTO[0] = None       # file_id قدیمی/نامعتبر؛ یک بار از روی فایل دوباره امتحان کن


# ---------- دکمه‌ها ----------
async def cb(update, context):
    q = update.callback_query
    uid = q.from_user.id
    parts = (q.data or "").split(":")
    try:
        act, gid = parts[1], int(parts[2])
    except Exception:
        await q.answer()
        return
    cm = _cm()
    note, alert, edit, begin = None, False, False, False
    async with _LOCK:
        g = GAMES.get(gid)
        if not g:
            note, alert = "این بازی دیگه فعال نیست.", True
        elif act == "app":
            if not any(p["id"] == uid for p in g["players"]):
                note, alert = ("اول «پیوستن» رو بزن." if g["status"] == "lobby" else "این بازی شروع شده و تو توش نیستی."), True
            elif g["status"] not in ("lobby", "playing"):
                note, alert = "این بازی تموم شده.", True
            else:
                try:
                    await context.bot.send_message(uid, "🎮 مار و پله — برای ورود دکمهٔ زیر رو بزن 👇", reply_markup=_open_kb(gid))
                    note = "لینک بازی به پیوی ربات فرستاده شد 👌"
                except Exception:
                    note, alert = "اول ربات رو توی پیوی استارت کن (/start) و دوباره بزن.", True
        elif g["status"] != "lobby":
            note, alert = "این بازی دیگه توی مرحلهٔ پیوستن نیست.", True
        elif act == "join":
            if any(p["id"] == uid for p in g["players"]):
                note = "تو قبلاً پیوستی."
            elif len(g["players"]) >= MAX_PLAYERS:
                note, alert = "ظرفیت پر شده.", True
            elif _active_of(uid):
                note, alert = "تو توی یه بازی دیگه‌ای.", True
            elif not await cm.is_member(context.bot, uid):
                note, alert = "اول باید عضو کانال‌های ربات بشی.", True
            elif cm.wallet(uid)["balance"] < g["bet"]:
                note, alert = f"الماس کافی نداری (شرط {g['bet']:,}).", True
            else:
                cm.change(uid, -g["bet"])
                g["players"].append({"id": uid, "name": (q.from_user.first_name or "بازیکن")[:20], "pos": 0,
                                     "out": False, "skips": 0})
                _save(g)
                note, edit = "پیوستی ✅ حالا «ورود به مینی‌اپ» رو بزن.", True
                if len(g["players"]) >= MAX_PLAYERS:
                    _begin(g)
                    begin = True
        elif act == "go":
            if uid != g["creator"]:
                note, alert = "فقط سازندهٔ بازی می‌تونه شروع کنه.", True
            elif len(g["players"]) < 2:
                note, alert = "حداقل ۲ بازیکن لازمه.", True
            else:
                _begin(g)
                begin = True
        elif act == "x":
            if uid != g["creator"]:
                note, alert = "فقط سازندهٔ بازی می‌تونه لغو کنه.", True
            else:
                _cancel(g, "creator")
                edit = True
        if note is None and not edit and not begin:
            note = ""
    try:
        await q.answer(note or None, show_alert=alert)
    except Exception:
        pass
    if g and (edit or begin):
        await _announce(context.bot, g)


# ---------- API مینی‌اپ ----------
def state_for(g, uid):
    now = _now()
    cur = _cur(g)["id"] if g["status"] == "playing" else None
    return {
        "id": g["id"], "status": g["status"], "bet": g["bet"], "pot": g["bet"] * len(g["players"]),
        "creator": g["creator"], "me": uid,
        "players": [{"id": p["id"], "idx": i, "name": p["name"], "pos": p["pos"], "out": p["out"]}
                    for i, p in enumerate(g["players"])],
        "turn": cur, "left": max(0, g["deadline"] - now) if g["status"] in ("lobby", "playing") else 0,
        "turn_sec": TURN_SEC, "last": g.get("last"), "log": g["log"][-6:], "winner": g.get("winner"),
        "payout": g.get("payout"), "tax": g.get("tax"), "seq": g["seq"],
        "snakes": SNAKES, "ladders": LADDERS, "chat": (g.get("messages") or [])[-30:],
    }


def active_summary(uid):
    g = _active_of(uid)
    return {"id": g["id"], "status": g["status"]} if g else None


async def api(path, uid, data):
    gid = data.get("gid")
    g = GAMES.get(gid) if isinstance(gid, int) and not isinstance(gid, bool) else None
    if g and path not in ("/api/snk/packs", "/api/snk/pack_add") and not any(p["id"] == uid for p in g["players"]):
        return {"game": None, "error": "not_player"}
    if not g:
        g = _active_of(uid)
    if path == "/api/snk/packs":
        s = get_session()
        try:
            rows = s.query(StickerPack).filter(StickerPack.owner_id == uid).order_by(StickerPack.id.desc()).all()
            return {"packs": [{"id": r.id, "name": r.name, "title": r.title,
                               "stickers": json.loads(r.stickers)} for r in rows]}
        finally:
            s.close()
    if path == "/api/snk/pack_add":
        raw = str(data.get("url") or "").strip()
        mt = re.search(r"(?:t\.me|telegram\.me)/(?:addstickers|stickers)/([A-Za-z0-9_]{3,64})", raw)
        if not mt:
            return {"error": "bad_pack_link"}
        pack_name = mt.group(1)
        try:
            st = await _app[0].bot.get_sticker_set(pack_name)
            stickers = [{"file_id": x.file_id, "emoji": x.emoji or "🙂",
                         "animated": bool(x.is_animated), "video": bool(x.is_video)}
                        for x in st.stickers[:120]]
            if not stickers:
                return {"error": "empty_pack"}
            s = get_session()
            try:
                old = s.query(StickerPack).filter(StickerPack.owner_id == uid,
                                                   StickerPack.name == pack_name).first()
                if old:
                    old.title, old.stickers = st.title[:128], json.dumps(stickers, ensure_ascii=False)
                else:
                    s.add(StickerPack(owner_id=uid, name=pack_name, title=st.title[:128],
                                      stickers=json.dumps(stickers, ensure_ascii=False)))
                s.commit()
            finally:
                s.close()
            return {"ok": True, "title": st.title, "count": len(stickers)}
        except Exception as e:
            logger.info("snakes sticker pack load failed: %s", e)
            return {"error": "pack_not_found"}
    if path == "/api/snk/chat":
        if not g or not any(p["id"] == uid for p in g["players"]):
            return {"error": "not_player"}
        kind = str(data.get("kind") or "text")
        msg = {"uid": uid, "name": next((p["name"] for p in g["players"] if p["id"] == uid), "بازیکن"),
               "at": _now()}
        if kind == "text":
            value = " ".join(str(data.get("text") or "").split())[:300]
            if not value:
                return {"error": "empty_message"}
            msg.update({"kind": "text", "text": value})
        elif kind == "sticker":
            try:
                pack_id, ix = int(data.get("pack_id")), int(data.get("index"))
            except (TypeError, ValueError):
                return {"error": "bad_sticker"}
            s = get_session()
            try:
                pack = s.query(StickerPack).filter(StickerPack.id == pack_id,
                                                    StickerPack.owner_id == uid).first()
                if not pack:
                    return {"error": "pack_not_found"}
                stickers = json.loads(pack.stickers)
                if not 0 <= ix < len(stickers):
                    return {"error": "bad_sticker"}
                chosen = stickers[ix]
                msg.update({"kind": "sticker", "file_id": chosen["file_id"],
                            "emoji": chosen.get("emoji", "🙂")})
            finally:
                s.close()
        else:
            return {"error": "bad_request"}
        g.setdefault("messages", []).append(msg)
        del g["messages"][:-50]
        _save(g)
        if kind == "sticker":
            try:
                await _app[0].bot.send_sticker(chat_id=g["chat"], sticker=msg["file_id"])
            except Exception:
                logger.info("snakes: group sticker delivery failed", exc_info=True)
        return {"ok": True, "game": state_for(g, uid)}
    if path == "/api/snk/state":
        return {"game": state_for(g, uid) if g else None}
    if path == "/api/snk/roll":
        bot = _app[0].bot
        async with _LOCK:
            if not g or g["status"] != "playing":
                return {"game": state_for(g, uid) if g else None, "error": "no_game"}
            p = _cur(g)
            if p["id"] != uid:
                return {"game": state_for(g, uid), "error": "not_your_turn"}
            _play(g, p, False)
        if g["status"] == "done":
            await _announce(bot, g)
        return {"game": state_for(g, uid)}
    return {"game": None, "error": "not_found"}


# ---------- کار پس‌زمینه ----------
async def _orphans(bot):
    """بازی‌هایی که توی دیتابیس نیمه‌کاره موندن ولی توی این پروسه نیستن (ری‌استارت/دیپلوی) → لغو و برگشت شرط."""
    cutoff = _now() - ORPHAN_AGE
    s = get_session()
    try:
        rows = [(r.id, r.data) for r in s.query(SnakeGame).filter(SnakeGame.status.in_(["lobby", "playing"]),
                                                                    SnakeGame.updated < cutoff).all()]
    finally:
        s.close()
    for gid, data in rows:
        if gid in GAMES:
            continue
        try:
            g = json.loads(data)
        except Exception:
            continue
        g["id"] = gid
        g["status"] = "refunded"
        _save(g)
        _refund_all(g)
        await _edit(bot, g)
        logger.info("snakes: orphan game %s refunded", gid)


async def tick(context):
    bot = context.bot
    now = _now()
    todo = []
    try:
        async with _LOCK:
            for g in list(GAMES.values()):
                if g["status"] == "lobby" and now >= g["deadline"]:
                    if len(g["players"]) >= 2:
                        _begin(g)
                    else:
                        _cancel(g, "timeout")
                    todo.append(g)
                elif g["status"] == "playing" and now >= g["deadline"]:
                    _play(g, _cur(g), True)
                    if g["status"] == "done":
                        todo.append(g)
                elif g["status"] in ("done", "cancelled", "refunded") and now - g["updated"] > KEEP_DONE:
                    GAMES.pop(g["id"], None)
        for g in todo:
            await _announce(bot, g)
        if now - _last_scan[0] >= 60:
            _last_scan[0] = now
            async with _LOCK:
                await _orphans(bot)
    except Exception:
        logger.exception("snakes: tick failed")


def setup(app):
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters
    _app[0] = app
    database.Base.metadata.create_all(database.engine)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS & filters.Regex(_PAT), cmd_new), group=-11)
    app.add_handler(CallbackQueryHandler(cb, pattern=r"^snk:"), group=-20)
    app.job_queue.run_repeating(tick, interval=3, first=8, name="snakes-tick")
