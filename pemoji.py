# -*- coding: utf-8 -*-
"""ایموجی پرمیوم روی همهٔ دکمه‌های ربات (💎 ✅ 🔗 ...).

چطور کار می‌کنه:
  هر دکمه‌ای که توی متنش یه ایموجی معمولی داشته باشه و برای اون ایموجی کد پرمیوم ثبت کرده باشی،
  بجای ایموجی معمولی، ایموجی پرمیوم کنار متن دکمه نشون داده می‌شه (icon_custom_emoji_id).

تنظیم کدها (فقط مدیر، توی چت خود ربات):
  /pemoji                      لیست کدهای ثبت‌شده + راهنما
  /pemoji 💎 5368324170671202286   ثبت کد برای 💎 (همون لحظه یه دکمهٔ تست میاد)
  /pemoji del 💎               حذف

یا با متغیر Railway (اختیاری):  PREMIUM_EMOJI_MAP = 💎=5368324170671202286,✅=5260416394419265081

نصب: توی main.py بعد از commerce.setup(app) :  import pemoji ; pemoji.install(app)

نکتهٔ تلگرام: ایموجی پرمیوم روی دکمه فقط وقتی دیده می‌شه که صاحب ربات اکانت پرمیوم داشته باشه
(یا ربات یوزرنیم اضافه از Fragment خریده باشه). بدون اون، دکمه عادی و بدون آیکون نشون داده می‌شه.
"""
import logging
import os

from sqlalchemy import Column, String
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler

import selfbot
from database import Base, engine, get_session

logger = logging.getLogger("pemoji")

_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_map = {}   # ایموجی (بدون FE0F) -> کد ایموجی پرمیوم


class PremiumEmojiMap(Base):
    __tablename__ = "premium_emoji_map"
    emoji = Column(String(32), primary_key=True)
    cid = Column(String(32), nullable=False)


def _key(e):
    return (e or "").replace("\ufe0f", "").strip()


def _load():
    _map.clear()
    for part in os.environ.get("PREMIUM_EMOJI_MAP", "").replace("،", ",").replace("\n", ",").split(","):
        if "=" in part:
            e, c = part.split("=", 1)
            c = c.strip().translate(_DIG)
            if _key(e) and c.isdigit():
                _map[_key(e)] = c
    s = get_session()
    try:
        for r in s.query(PremiumEmojiMap).all():
            _map[_key(r.emoji)] = r.cid
    except Exception as e:  # noqa: BLE001
        logger.warning("pemoji load: %s", e)
    finally:
        s.close()
    logger.info("premium emoji map: %d مورد", len(_map))


def icon_for(text):
    """اولین ایموجی ثبت‌شده توی متن دکمه → کد پرمیوم؛ وگرنه None."""
    if not _map or not text:
        return None
    t = text.replace("\ufe0f", "")
    best = None
    for k, cid in _map.items():
        i = t.find(k)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, cid)
    return best[1] if best else None


def _wrap(orig):
    def inner(text, *a, **kw):
        b = orig(text, *a, **kw)
        cid = icon_for(text)
        if not cid:
            return b
        try:
            api = dict(b.api_kwargs or {})
            api["icon_custom_emoji_id"] = cid
            return InlineKeyboardButton(b.text, callback_data=b.callback_data, url=b.url, api_kwargs=api)
        except Exception as e:  # noqa: BLE001
            logger.info("pemoji wrap: %s", e)
            return b
    return inner


async def cmd_pemoji(update, context):
    u = update.effective_user
    m = update.effective_message
    if not u or u.id not in selfbot.ADMIN_IDS or update.effective_chat.type != "private":
        return
    args = context.args or []
    if not args:
        lines = [f"{k}  ←  {v}" for k, v in _map.items()] or ["هنوز کدی ثبت نشده."]
        await m.reply_text("✨ ایموجی پرمیوم دکمه‌ها\n\n" + "\n".join(lines) +
                           "\n\nثبت:  /pemoji 💎 کد_ایموجی\nحذف:  /pemoji del 💎")
        return
    if args[0].lower() in ("del", "rm", "حذف") and len(args) > 1:
        k = _key(args[1])
        s = get_session()
        try:
            r = s.get(PremiumEmojiMap, k)
            if r:
                s.delete(r)
                s.commit()
        finally:
            s.close()
        _map.pop(k, None)
        await m.reply_text(f"🗑 {k} حذف شد.")
        return
    if len(args) < 2:
        await m.reply_text("مثل:  /pemoji 💎 5368324170671202286")
        return
    k, cid = _key(args[0]), args[1].translate(_DIG).strip("[] ")
    if not k or not cid.isdigit():
        await m.reply_text("❗️ فرمت: /pemoji ایموجی کد_عددی")
        return
    try:
        st = await context.bot.get_custom_emoji_stickers([cid])
    except Exception as e:  # noqa: BLE001
        st = []
        logger.info("pemoji lookup: %s", e)
    if not st:
        await m.reply_text("❌ این کد ایموجی معتبر نیست.")
        return
    s = get_session()
    try:
        s.merge(PremiumEmojiMap(emoji=k, cid=cid))
        s.commit()
    finally:
        s.close()
    _map[k] = cid
    api = {"icon_custom_emoji_id": cid, "style": "success"}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("تست دکمه", callback_data="pe:wait", api_kwargs=api)]])
    await m.reply_text(f"✅ ثبت شد: {k} ← {cid}\n(ایموجی اصلی این کد: {st[0].emoji or '؟'})\n"
                       "از این به بعد هر دکمه‌ای که 💎/{0} داشته باشه با ایموجی پرمیوم نشون داده می‌شه.\n"
                       "اگه آیکون توی دکمهٔ تست زیر نمی‌بینی، یعنی اکانت صاحب ربات پرمیوم نیست.".format(k), reply_markup=kb)


def install(app):
    Base.metadata.create_all(engine, tables=[PremiumEmojiMap.__table__])
    _load()
    import commerce
    selfbot.B = _wrap(selfbot.B)
    commerce.B = _wrap(commerce.B)
    app.add_handler(CommandHandler("pemoji", cmd_pemoji), group=-30)
    logger.info("pemoji installed")
