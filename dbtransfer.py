# -*- coding: utf-8 -*-
"""انتقال کامل دیتابیس ربات بین دو سرور (مثلاً دو اکانت Railway) فقط با تلگرام.

فقط مالک (OWNER_ID) و فقط توی پی‌وی ربات:
  /backup   ← همهٔ جدول‌ها رو توی یه فایل فشرده می‌فرسته (کاربرها، الماس‌ها، سشن‌های سلف، تنظیمات...)
  /restore  ← فایل بکاپ رو می‌فرستی؛ بعد از تأیید، دیتابیس این ربات با اون جایگزین می‌شه

نکته‌ها:
  • BOT_TOKEN و SELF_KEY سرور جدید باید دقیقاً همون سرور قدیمی باشه، وگرنه سشن‌ها خراب می‌شن.
  • بعد از /restore یک بار سرویس رو Redeploy کن تا سلف‌های فعال دوباره وصل بشن.
"""
import asyncio
import base64
import datetime
import gzip
import io
import json
import logging
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import text
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as KB
from telegram.ext import (
    ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters,
)

import database
from database import Base, engine

logger = logging.getLogger("dbtransfer")

FORMAT = "rubahio-db-v1"
MAX_BYTES = 19 * 1024 * 1024   # تلگرام اجازه‌ی دانلود فایل بزرگ‌تر از ~۲۰ مگ رو به ربات نمی‌ده
_OWNERS = set()


# ───────────────────────── تبدیل مقدارها ─────────────────────────
def _to_json(v):
    if v is None:
        return None
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(v)).decode("ascii")
    if isinstance(v, Decimal):
        return str(v)
    return v


def _from_json(col, v):
    if v is None:
        return None
    t = col.type
    if isinstance(t, sa.DateTime):
        return datetime.datetime.fromisoformat(v)
    if isinstance(t, sa.Date):
        return datetime.date.fromisoformat(v)
    if isinstance(t, sa.Time):
        return datetime.time.fromisoformat(v)
    if isinstance(t, sa.LargeBinary):
        return base64.b64decode(v)
    if isinstance(t, sa.Numeric) and not isinstance(t, sa.Float):
        return Decimal(str(v))
    return v


# ───────────────────────── بکاپ ─────────────────────────
def make_backup():
    """(bytes فشرده، {نام_جدول: تعداد_ردیف})"""
    tables = {}
    counts = {}
    with engine.connect() as conn:
        for t in Base.metadata.sorted_tables:
            rows = []
            for r in conn.execute(t.select()):
                m = r._mapping
                rows.append({c.key: _to_json(m[c.key]) for c in t.columns})
            tables[t.name] = {"rows": rows}
            counts[t.name] = len(rows)
    payload = {
        "format": FORMAT,
        "created": datetime.datetime.utcnow().isoformat() + "Z",
        "tables": tables,
    }
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return gzip.compress(raw, 6), counts


def _fmt_counts(counts):
    items = [(k, v) for k, v in counts.items()]
    total = sum(v for _, v in items)
    lines = [f"• {k}: {v}" for k, v in items if v]
    return total, ("\n".join(lines) if lines else "هیچ ردیفی نیست")


# ───────────────────────── بازگردانی ─────────────────────────
def parse_backup(data):
    raw = gzip.decompress(data)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != FORMAT or "tables" not in payload:
        raise ValueError("این فایل بکاپ این ربات نیست")
    return payload


def count_payload(payload):
    return {name: len(info.get("rows") or []) for name, info in payload["tables"].items()}


def restore_payload(payload):
    tables = list(Base.metadata.sorted_tables)
    done = {}
    with engine.begin() as conn:            # همه‌چیز یا هیچی
        for t in reversed(tables):
            conn.execute(t.delete())
        for t in tables:
            rows = (payload["tables"].get(t.name) or {}).get("rows") or []
            if not rows:
                continue
            cols = {c.key: c for c in t.columns}
            conv = [{k: _from_json(cols[k], v) for k, v in r.items() if k in cols} for r in rows]
            for i in range(0, len(conv), 500):
                conn.execute(t.insert(), conv[i:i + 500])
            done[t.name] = len(rows)
    _fix_sequences(tables)
    return done


def _fix_sequences(tables):
    """بعد از ریختن ردیف‌ها با id مشخص، شمارندهٔ خودکار Postgres رو جلو می‌بریم تا ردیف‌های جدید تکراری نشن."""
    if engine.dialect.name != "postgresql":
        return
    q = engine.dialect.identifier_preparer.quote
    for t in tables:
        pk = list(t.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, sa.Integer):
            continue
        tn, cn = q(t.name), q(pk[0].name)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence(:t, :c), "
                        f"COALESCE((SELECT MAX({cn}) FROM {tn}), 1), "
                        f"(SELECT MAX({cn}) FROM {tn}) IS NOT NULL)"
                    ),
                    {"t": tn, "c": pk[0].name},
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("sequence fix %s: %s", t.name, e)


# ───────────────────────── هندلرهای تلگرام ─────────────────────────
def _is_owner(update):
    u = update.effective_user
    return bool(u and u.id in _OWNERS)


def _sqlite_warn():
    if getattr(database, "_is_sqlite", False):
        return ("\n\n⚠️ این ربات الان روی دیتابیس موقت (SQLite) کار می‌کنه و با هر دیپلوی پاک می‌شه. "
                "اول توی Railway یه Postgres اضافه کن و DATABASE_URL رو ست کن.")
    return ""


async def cmd_backup(update, context):
    if not _is_owner(update) or update.effective_chat.type != "private":
        return
    msg = update.effective_message
    wait = await msg.reply_text("⏳ دارم بکاپ می‌گیرم...")
    try:
        data, counts = await asyncio.to_thread(make_backup)
    except Exception as e:  # noqa: BLE001
        logger.exception("backup failed")
        await wait.edit_text(f"❌ بکاپ نشد: {e}")
        return
    if len(data) > MAX_BYTES:
        await wait.edit_text("❌ حجم بکاپ از ۱۹ مگابایت بیشتره و از طریق ربات قابل انتقال نیست. به پشتیبان‌ت خبر بده.")
        return
    total, lines = _fmt_counts(counts)
    name = "rubahio_backup_" + datetime.datetime.now().strftime("%Y%m%d_%H%M") + ".json.gz"
    await msg.reply_document(
        document=io.BytesIO(data), filename=name,
        caption=(f"✅ بکاپ کامل ({total} ردیف)\n{lines}\n\n"
                 "🔒 این فایل شامل سشن‌های کاربرهاست؛ به کسی نده و بعد از انتقال از چت پاکش کن."
                 + _sqlite_warn())[:1000],
    )
    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass


async def cmd_restore(update, context):
    if not _is_owner(update) or update.effective_chat.type != "private":
        return
    context.user_data["db_restore_wait"] = True
    context.user_data.pop("db_restore_payload", None)
    await update.effective_message.reply_text(
        "📥 فایل بکاپ (rubahio_backup_....json.gz) رو همین‌جا بفرست.\n"
        "بعد از دریافت، قبل از جایگزینی ازت تأیید می‌گیرم." + _sqlite_warn()
    )


async def on_document(update, context):
    if not _is_owner(update) or update.effective_chat.type != "private":
        return
    if not context.user_data.get("db_restore_wait"):
        return      # بقیهٔ هندلرها کارشون رو بکنن
    msg = update.effective_message
    doc = msg.document
    if not doc:
        return
    if doc.file_size and doc.file_size > MAX_BYTES:
        await msg.reply_text("❌ فایل از ۱۹ مگابایت بزرگ‌تره و ربات نمی‌تونه دانلودش کنه.")
        raise ApplicationHandlerStop
    try:
        f = await doc.get_file()
        data = bytes(await f.download_as_bytearray())
        payload = await asyncio.to_thread(parse_backup, data)
    except Exception as e:  # noqa: BLE001
        await msg.reply_text(f"❌ این فایل خونده نشد: {e}\nفایل درست رو بفرست یا دوباره /restore بزن.")
        raise ApplicationHandlerStop
    context.user_data["db_restore_wait"] = False
    context.user_data["db_restore_payload"] = payload
    total, lines = _fmt_counts(count_payload(payload))
    await msg.reply_text(
        f"📦 بکاپ خونده شد ({total} ردیف):\n{lines}\n\n"
        "⚠️ با ادامه، همهٔ دیتای فعلی این ربات پاک و با این بکاپ جایگزین می‌شه. مطمئنی؟",
        reply_markup=KB([[B("✅ بله، جایگزین کن", callback_data="dbt:yes"),
                          B("❌ لغو", callback_data="dbt:no")]]),
    )
    raise ApplicationHandlerStop


async def cb_restore(update, context):
    q = update.callback_query
    if not q or q.from_user.id not in _OWNERS:
        if q:
            await q.answer()
        return
    await q.answer()
    payload = context.user_data.pop("db_restore_payload", None)
    if q.data == "dbt:no" or not payload:
        await q.edit_message_text("لغو شد." if q.data == "dbt:no" else "بکاپی در انتظار نیست؛ دوباره /restore بزن.")
        return
    await q.edit_message_text("⏳ دارم جایگزین می‌کنم...")
    # قبل از پاک کردن، یه نسخهٔ امن از دیتای فعلی (اگه چیزی توش بود) می‌فرستیم
    try:
        old, oc = await asyncio.to_thread(make_backup)
        if sum(oc.values()) and len(old) <= MAX_BYTES:
            await q.message.reply_document(
                document=io.BytesIO(old),
                filename="before_restore_" + datetime.datetime.now().strftime("%Y%m%d_%H%M") + ".json.gz",
                caption="🗄 نسخهٔ دیتای قبلی (برای احتیاط)",
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("pre-restore backup failed: %s", e)
    try:
        done = await asyncio.to_thread(restore_payload, payload)
    except Exception as e:  # noqa: BLE001
        logger.exception("restore failed")
        await q.message.reply_text(f"❌ بازگردانی انجام نشد و دیتای قبلی دست‌نخورده موند:\n{e}")
        return
    total, lines = _fmt_counts(done)
    await q.message.reply_text(
        f"✅ بازگردانی کامل شد ({total} ردیف):\n{lines}\n\n"
        "حالا آخرین قدم: توی Railway روی سرویس ربات برو Deployments ← سه‌نقطهٔ دیپلوی آخر ← Redeploy "
        "تا سلف کاربرها دوباره وصل بشن."
    )


def register(app, owner_ids):
    _OWNERS.clear()
    _OWNERS.update(int(x) for x in owner_ids)
    app.add_handler(CommandHandler("backup", cmd_backup), group=-20)
    app.add_handler(CommandHandler("restore", cmd_restore), group=-20)
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, on_document), group=-20)
    app.add_handler(CallbackQueryHandler(cb_restore, pattern=r"^dbt:(yes|no)$"), group=-20)
