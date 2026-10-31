# -*- coding: utf-8 -*-
"""بازی شانسی دو نفره با الماس توی گروه‌ها.

- توی گروه بنویس «بازی 20» (۲۰ = مقدار شرط به الماس).
- الماسِ سازنده همون لحظه نگه داشته می‌شه و یه پنل با دکمهٔ «پیوستن» میاد؛ ۱۲۰ ثانیه مهلته.
- اگه کسی نیاد، الماس سازنده برمی‌گرده. اگه یه نفر دیگه بپیونده (و شرط رو داشته باشه)، بعد از ۴ ثانیه به‌شانس یکی برنده می‌شه.
- ۵٪ مالیات از کل جایزه کم می‌شه و برنده بقیه رو می‌گیره.
- اگه ربات وسط بازی ری‌استارت بشه (دیپلوی)، همهٔ بازی‌های نیمه‌کاره موقع بالا اومدن به صاحباشون برمی‌گرده.
"""
import html
import logging
import re
import secrets
import time
from decimal import Decimal

from sqlalchemy import BigInteger, Column, Integer, String
from telegram import InlineKeyboardMarkup as KB
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

import commerce
from commerce import B, Wallet, fmt_bal
from database import Base, get_session

logger = logging.getLogger(__name__)

JOIN_TIMEOUT = 120      # ثانیه
REVEAL_DELAY = 4        # ثانیه تا اعلام برنده
TAX_PERCENT = Decimal("5")
MIN_BET = 1
MAX_BET = 100000
_RE = re.compile(r"^\s*بازی\s+([0-9۰-۹٠-٩]{1,7})\s*$")
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class DiceGame(Base):
    __tablename__ = "dice_games"
    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    creator = Column(BigInteger, nullable=False)
    creator_name = Column(String(80), nullable=True)
    joiner = Column(BigInteger, nullable=True)
    joiner_name = Column(String(80), nullable=True)
    bet = Column(Integer, nullable=False)
    status = Column(String(12), default="open")     # open | running | done | cancelled
    created = Column(Integer, default=lambda: int(time.time()))


# ---------- کیف پول (همه همزمان و بدون await؛ پس اتمیکه) ----------
def _debit(uid, amount):
    """اگه موجودی کافی بود کم می‌کنه و True برمی‌گردونه."""
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if not w or Decimal(w.balance or 0) < Decimal(amount):
            return False
        w.balance = Decimal(w.balance) - Decimal(amount)
        s.commit()
        return True
    finally:
        s.close()


def _credit(uid, amount):
    commerce.change(uid, amount)


def _balance(uid):
    return commerce.wallet(uid)["balance"]


def _name(u):
    return (u.first_name or u.username or str(u.id))[:40]


def _mention(uid, name):
    return f'<a href="tg://user?id={uid}">{html.escape(name or str(uid))}</a>'


def _load(gid):
    s = get_session()
    try:
        g = s.get(DiceGame, gid)
        if g:
            s.expunge(g)
        return g
    finally:
        s.close()


def _set(gid, **kw):
    s = get_session()
    try:
        g = s.get(DiceGame, gid)
        for k, v in kw.items():
            setattr(g, k, v)
        s.commit()
    finally:
        s.close()


def _open_of(uid, chat_id):
    s = get_session()
    try:
        return s.query(DiceGame).filter(DiceGame.creator == uid, DiceGame.status.in_(("open", "running"))).first() is not None
    finally:
        s.close()


def _pot(bet):
    pot = Decimal(bet) * 2
    tax = (pot * TAX_PERCENT / 100).quantize(Decimal("0.1"))
    return pot, tax, pot - tax


def _join_kb(gid, bet):
    return KB([[B(f"🎮 پیوستن ({bet} الماس)", callback_data=f"dice:j:{gid}")],
               [B("❌ لغو (فقط سازنده)", callback_data=f"dice:c:{gid}")]])


def _open_text(g):
    pot, tax, prize = _pot(g.bet)
    return ("🎲 بازی شانسی دو نفره\n\n"
            f"👤 سازنده: {_mention(g.creator, g.creator_name)}\n"
            f"💎 شرط هر نفر: {g.bet} الماس\n"
            f"🏆 جایزهٔ برنده: {fmt_bal(prize)} الماس (بعد از {TAX_PERCENT}٪ مالیات)\n\n"
            f"⏳ تا {JOIN_TIMEOUT} ثانیه یه نفر باید «پیوستن» رو بزنه؛ وگرنه الماس سازنده برمی‌گرده.")


async def _edit(bot, g, text, kb=None):
    try:
        await bot.edit_message_text(text, chat_id=g.chat_id, message_id=g.message_id, parse_mode="HTML", reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        logger.info("dice edit: %s", e)


# ---------- ساخت بازی ----------
async def on_new(update, context):
    m = update.effective_message
    u = update.effective_user
    if not m or not u or not m.text:
        return
    mt = _RE.match(m.text)
    if not mt:
        return
    bet = int(mt.group(1).translate(_DIG))
    if not MIN_BET <= bet <= MAX_BET:
        await m.reply_text(f"💎 مقدار شرط باید بین {MIN_BET} تا {MAX_BET} الماس باشه. مثل: بازی 20")
        return
    if _open_of(u.id, m.chat_id):
        await m.reply_text("⏳ تو یه بازی نیمه‌کاره داری؛ صبر کن تموم بشه یا لغوش کن.")
        return
    if not _debit(u.id, bet):
        await m.reply_text(f"❌ الماس کافی نداری. موجودی تو: {fmt_bal(_balance(u.id))} الماس")
        return
    s = get_session()
    try:
        g = DiceGame(chat_id=m.chat_id, creator=u.id, creator_name=_name(u), bet=bet, status="open")
        s.add(g)
        s.commit()
        gid = g.id
    finally:
        s.close()
    try:
        sent = await m.reply_text(_open_text(_load(gid)), parse_mode="HTML", reply_markup=_join_kb(gid, bet))
    except Exception as e:  # noqa: BLE001
        logger.warning("dice send failed: %s", e)
        _credit(u.id, bet)
        _set(gid, status="cancelled")
        return
    _set(gid, message_id=sent.message_id)
    context.job_queue.run_once(_expire, JOIN_TIMEOUT, data=gid, name=f"dice-exp-{gid}")


def _cancel_jobs(context, gid):
    for j in context.job_queue.get_jobs_by_name(f"dice-exp-{gid}"):
        j.schedule_removal()


async def _expire(context):
    gid = context.job.data
    g = _load(gid)
    if not g or g.status != "open":
        return
    _set(gid, status="cancelled")
    _credit(g.creator, g.bet)
    await _edit(context.bot, g, f"⌛ بازی لغو شد؛ کسی نیومد.\n💎 {g.bet} الماسِ {_mention(g.creator, g.creator_name)} برگشت.")


# ---------- پیوستن / لغو ----------
async def on_join(update, context):
    q = update.callback_query
    u = q.from_user
    try:
        gid = int(q.data.split(":")[2])
    except Exception:
        await q.answer()
        return
    g = _load(gid)
    if not g or g.status != "open":
        await q.answer("این بازی دیگه باز نیست.", show_alert=True)
        return
    if u.id == g.creator:
        await q.answer("نمی‌تونی به بازی خودت بپیوندی؛ یه نفر دیگه باید بزنه.", show_alert=True)
        return
    if not _debit(u.id, g.bet):
        await q.answer(f"الماس کافی نداری. موجودی تو: {fmt_bal(_balance(u.id))} الماس", show_alert=True)
        return
    _set(gid, joiner=u.id, joiner_name=_name(u), status="running")
    _cancel_jobs(context, gid)
    await q.answer("پیوستی! قرعه‌کشی شروع شد 🎲")
    g = _load(gid)
    await _edit(context.bot, g,
                "🎲 بازی شروع شد!\n\n"
                f"👤 {_mention(g.creator, g.creator_name)}  ⚔️  {_mention(g.joiner, g.joiner_name)}\n"
                f"💎 شرط هر نفر: {g.bet} الماس\n\n"
                f"🎰 قرعه‌کشی... تا {REVEAL_DELAY} ثانیه دیگه برنده معلوم می‌شه")
    context.job_queue.run_once(_reveal, REVEAL_DELAY, data=gid, name=f"dice-rev-{gid}")


async def on_cancel(update, context):
    q = update.callback_query
    try:
        gid = int(q.data.split(":")[2])
    except Exception:
        await q.answer()
        return
    g = _load(gid)
    if not g or g.status != "open":
        await q.answer("این بازی دیگه باز نیست.", show_alert=True)
        return
    if q.from_user.id != g.creator:
        await q.answer("فقط سازندهٔ بازی می‌تونه لغوش کنه.", show_alert=True)
        return
    _set(gid, status="cancelled")
    _cancel_jobs(context, gid)
    _credit(g.creator, g.bet)
    await q.answer("لغو شد و الماست برگشت.")
    await _edit(context.bot, g, f"❌ بازی توسط سازنده لغو شد.\n💎 {g.bet} الماس به {_mention(g.creator, g.creator_name)} برگشت.")


# ---------- اعلام برنده ----------
async def _reveal(context):
    gid = context.job.data
    g = _load(gid)
    if not g or g.status != "running" or not g.joiner:
        return
    _set(gid, status="done")
    win_first = secrets.randbelow(2) == 0
    wid, wname = (g.creator, g.creator_name) if win_first else (g.joiner, g.joiner_name)
    lid, lname = (g.joiner, g.joiner_name) if win_first else (g.creator, g.creator_name)
    pot, tax, prize = _pot(g.bet)
    _credit(wid, prize)
    await _edit(context.bot, g,
                "🎲 نتیجهٔ بازی\n\n"
                f"🏆 برنده: {_mention(wid, wname)}  (+{fmt_bal(prize)} الماس)\n"
                f"💔 بازنده: {_mention(lid, lname)}  (−{g.bet} الماس)\n"
                f"🏛 مالیات {TAX_PERCENT}٪: {fmt_bal(tax)} الماس\n\n"
                f"💎 کل جایزه: {fmt_bal(pot)} الماس")


# ---------- بازگشت الماس بازی‌های نیمه‌کاره بعد از ری‌استارت ----------
def refund_unfinished():
    s = get_session()
    n = 0
    try:
        for g in s.query(DiceGame).filter(DiceGame.status.in_(("open", "running"))).all():
            _credit(g.creator, g.bet)
            if g.status == "running" and g.joiner:
                _credit(g.joiner, g.bet)
            g.status = "cancelled"
            n += 1
        s.commit()
    finally:
        s.close()
    if n:
        logger.warning("dice: %s بازی نیمه‌کاره بعد از ری‌استارت به صاحباشون برگشت", n)


# ---------- انتقال الماس توی گروه: ریپلای روی کاربر + «انتقال 20» ----------
_TR_RE = re.compile(r"^\s*انتقال\s+([0-9۰-۹٠-٩]+(?:[.,٫][0-9۰-۹٠-٩]+)?)\s*$")


def _transfer(src_id, dst_id, amount):
    """اتمیک: اگه موجودی کافی بود کم و اضافه می‌کنه."""
    import time as _t
    s = get_session()
    try:
        src = s.get(Wallet, src_id)
        if not src or Decimal(src.balance or 0) < amount:
            return False
        dst = s.get(Wallet, dst_id)
        if not dst:
            dst = Wallet(user_id=dst_id, balance=Decimal("0.0"), last_charge=int(_t.time()))
            s.add(dst)
        src.balance = Decimal(src.balance) - amount
        dst.balance = Decimal(dst.balance or 0) + amount
        s.commit()
        return True
    finally:
        s.close()


async def on_transfer(update, context):
    m = update.effective_message
    u = update.effective_user
    if not m or not u or not m.text:
        return
    mt = _TR_RE.match(m.text)
    if not mt:
        return
    rep = m.reply_to_message
    to = rep.from_user if rep else None
    if not to:
        await m.reply_text("↩️ روی پیام کاربری که می‌خوای الماس بدی ریپلای کن و بنویس: انتقال 20")
        return
    if to.is_bot or to.id == u.id:
        await m.reply_text("❌ نمی‌تونی به خودت یا یه ربات الماس منتقل کنی.")
        return
    raw = mt.group(1).translate(_DIG).replace(",", ".").replace("٫", ".")
    try:
        amount = Decimal(raw)
    except Exception:
        amount = Decimal("0")
    if amount <= 0 or amount * 10 != (amount * 10).to_integral_value():
        await m.reply_text("❌ مقدار نامعتبره (حداکثر یک رقم اعشار). مثل: انتقال 20")
        return
    if not _transfer(u.id, to.id, amount):
        await m.reply_text(f"❌ الماس کافی نداری. موجودی تو: {fmt_bal(_balance(u.id))} الماس")
        return
    await m.reply_text(f"✅ {fmt_bal(amount)} الماس از {_mention(u.id, _name(u))} به {_mention(to.id, _name(to))} منتقل شد.",
                       parse_mode="HTML")


def register(app):
    Base.metadata.create_all(__import__("database").engine)
    refund_unfinished()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS & filters.Regex(_RE), on_new), group=-11)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS & filters.Regex(_TR_RE), on_transfer), group=-11)
    app.add_handler(CallbackQueryHandler(on_join, pattern=r"^dice:j:"), group=-11)
    app.add_handler(CallbackQueryHandler(on_cancel, pattern=r"^dice:c:"), group=-11)
