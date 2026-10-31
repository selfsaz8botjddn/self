# -*- coding: utf-8 -*-
"""ایموجی پرمیوم با کد برای همهٔ کاربرها (اینلاین).

کاربر می‌نویسه:  @آیدی_ربات سلام [5260416394419265081]
زیرش گزینهٔ «تبدیل به ایموجی پرمیوم» میاد؛ بعد از انتخاب، ربات پیام رو ویرایش می‌کنه و ایموجی‌ها پرمیوم می‌شن.

نصب (۳ خط):
  main.py:  import premium_emoji
            premium_emoji.register(app)          # درست بعد از selfbot.register(app, ADMIN_IDS)
            allowed_updates=[..., "chosen_inline_result"]
  BotFather: /setinlinefeedback ← Enabled (100%)
"""
import hashlib
import logging
import re

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                      InputTextMessageContent, MessageEntity)
from telegram.ext import ApplicationHandlerStop, ChosenInlineResultHandler, InlineQueryHandler, MessageHandler, filters

logger = logging.getLogger("premium_emoji")

_RE = re.compile(r"\[\s*([0-9۰-۹٠-٩]{5,25})\s*\]")
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FALLBACK = "⭐"
# دکمهٔ موقت: تلگرام فقط وقتی پیام اینلاین دکمه داشته باشه inline_message_id می‌ده تا بشه ویرایشش کرد.
_KB = InlineKeyboardMarkup([[InlineKeyboardButton("⏳", callback_data="pe:wait")]])


def _u16(t):
    return len(t.encode("utf-16-le")) // 2


async def _build(bot, q):
    """(text, entities, bad_id) یا None اگه [کد] نبود."""
    ms = list(_RE.finditer(q))[:50]
    if not ms:
        return None
    ids = [m.group(1).translate(_DIG) for m in ms]
    emo, bad = {}, None
    try:
        for st in await bot.get_custom_emoji_stickers(list(dict.fromkeys(ids))):
            emo[st.custom_emoji_id] = st.emoji or _FALLBACK
        bad = next((i for i in ids if i not in emo), None)
    except Exception as e:
        logger.info("emoji lookup: %s", e)
    text, ents, last = "", [], 0
    for m, cid in zip(ms, ids):
        text += q[last:m.start()]
        ph = emo.get(cid, _FALLBACK)
        ents.append(MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=_u16(text), length=_u16(ph), custom_emoji_id=cid))
        text += ph
        last = m.end()
    text += q[last:]
    return text, ents, bad


async def on_inline(update, context):
    iq = update.inline_query
    q = iq.query or ""
    built = await _build(context.bot, q)
    if built is None:
        return                      # [کد] نبود؛ بذار هندلر اصلی (پنل) کار خودش رو بکنه
    text, ents, bad = built
    h = hashlib.sha1(q.encode()).hexdigest()[:16]
    if bad:
        msg = "❌ کد ایموجی معتبر نیست: " + bad
        res = InlineQueryResultArticle(id="x:" + h, title=msg, description="کد را از ربات آیدی ایموجی پرمیوم بگیر",
                                       input_message_content=InputTextMessageContent(msg))
    else:
        res = InlineQueryResultArticle(
            id="pe:" + h, title="✨ تبدیل به ایموجی پرمیوم", description=text[:100],
            input_message_content=InputTextMessageContent(text, entities=ents), reply_markup=_KB)
    await iq.answer([res], cache_time=0, is_personal=True)
    raise ApplicationHandlerStop


async def on_chosen(update, context):
    cr = update.chosen_inline_result
    imid = cr.inline_message_id
    logger.info("premium emoji chosen: id=%s inline_message_id=%s", cr.result_id, bool(imid))
    if not imid or not cr.result_id.startswith("pe:"):
        return
    built = await _build(context.bot, cr.query or "")
    if not built:
        return
    text, ents, _ = built
    try:
        # ویرایش بدون دکمه: هم ایموجی‌ها رو می‌ذاره هم دکمهٔ ⏳ رو برمی‌داره
        await context.bot.edit_message_text(text, inline_message_id=imid, entities=ents)
        logger.info("premium emoji edit ok")
    except Exception as e:
        logger.warning("premium emoji edit failed: %s", e)
        try:
            await context.bot.edit_message_reply_markup(inline_message_id=imid)
        except Exception:
            pass


async def on_private(update, context):
    """پیوی ربات: متن با [کد] بفرست، ربات همون متن رو با ایموجی پرمیوم جواب می‌ده (بعد فوروارد کن)."""
    m = update.effective_message
    built = await _build(context.bot, m.text or "")
    if not built:
        return
    text, ents, bad = built
    if bad:
        await m.reply_text("❌ کد ایموجی معتبر نیست: " + bad)
    else:
        await m.reply_text(text, entities=ents)
        logger.info("premium emoji private reply sent")
    raise ApplicationHandlerStop


async def on_wait(update, context):
    await update.callback_query.answer()
    raise ApplicationHandlerStop


def register(app):
    from telegram.ext import CallbackQueryHandler
    app.add_handler(InlineQueryHandler(on_inline), group=-6)
    app.add_handler(ChosenInlineResultHandler(on_chosen), group=-6)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & filters.Regex(_RE.pattern), on_private), group=-6)
    app.add_handler(CallbackQueryHandler(on_wait, pattern=r"^pe:wait$"), group=-6)
