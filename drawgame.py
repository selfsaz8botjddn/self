# -*- coding: utf-8 -*-
"""
بازی «نقاشی» (چندنفره، شرط‌بندی با الماس، بازی توی مینی‌اپ) — هم‌سبک «مار و پله».

جریان:
  ۱) توی گروه یکی می‌نویسه «نقاشی 20» → شرط ۲۰ الماس از سازنده کم می‌شه و پیام لابی میاد.
  ۲) بقیه «پیوستن» رو می‌زنن (شرط از هرکدوم کم می‌شه). «ورود به مینی‌اپ» بازی رو براشون باز می‌کنه.
  ۳) سازنده «شروع بازی» رو می‌زنه، یا ۱۲۰ ثانیه بعد (اگه حداقل ۲ نفر باشن) خودکار شروع می‌شه؛ با ۴ نفر فوراً.
  ۴) بازی ۴ دوره. هر دور یه نفر (به‌نوبت) یه کلمهٔ مخفی می‌گیره و ۳۰ ثانیه نقاشی می‌کشه؛ بقیه بین ۱۸ گزینه حدس می‌زنن.
     هر کی زودتر درست حدس بزنه امتیاز بیشتری می‌گیره؛ نقاش هم به‌ازای هر حدس درست امتیاز می‌گیره.
  ۵) آخر هر دور ۵ ثانیه نتیجهٔ همون دور نشون داده می‌شه؛ آخر بازی نتیجهٔ کل + برنده و بازنده‌ها.
  ۶) برنده کل جایزه رو منهای ۵٪ مالیات می‌گیره (اگه مساوی بشن، بین بالاترین‌ها تقسیم می‌شه؛ اگه هیچ‌کس امتیاز نگرفت، شرط‌ها برمی‌گرده).

قوانین: شرط حداقل ۵ و حداکثر ۱٬۰۰۰٬۰۰۰ الماس. هر کاربر همزمان فقط توی یک نقاشی؛ هر گروه همزمان یک نقاشی.
هر بازیکن برای هر دور فقط یک بار می‌تونه حدس بزنه (غلط = قفل می‌شه). جواب درست و امتیازها سمت سرور حساب می‌شن.
اگه ربات وسط بازی ری‌استارت بشه، بازیِ نیمه‌کاره بعد از چند دقیقه لغو و شرط همه برمی‌گرده (جدول draw_games).

نصب: miniapp.setup(app) این ماژول رو خودش نصب می‌کنه؛ تغییر main.py لازم نیست.
"""
import asyncio
import html
import io
import json
import logging
import os
import random
import re
import time
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import Column, Integer, String, Text

import database
from database import Base, get_session

logger = logging.getLogger(__name__)

MIN_BET = 5
MAX_BET = 1_000_000
MIN_PLAYERS = 2
MAX_PLAYERS = 4
ROUNDS = 4
LOBBY_SEC = 120
DRAW_SEC = 30
RESULT_SEC = 5
TAX = Decimal("0.05")
KEEP_DONE = 1800
ORPHAN_AGE = 150

# امتیازها
BASE_PTS = 100          # حدس درست
PER_SEC = 3             # + برای هر ثانیهٔ باقی‌مونده
FIRST_BONUS = 25        # اولین حدس درست دور
DRAWER_PER = 40         # نقاش به‌ازای هر حدس درست
DRAWER_ALL_BONUS = 30   # اگه همه درست حدس بزنن

NCOL = 14               # تعداد رنگ‌های پالت (اندیس ۱۳ = پاک‌کن)
NWID = 3                # تعداد قلم‌ها
MAX_OPS = 3500          # سقف خط‌های هر دور
OPS_PER_REQ = 40
PTS_PER_OP = 240        # حداکثر عدد (۲ تا برای هر نقطه) توی هر خط
OPS_CHUNK = 500         # حداکثر خطی که توی یک پاسخ برمی‌گرده

_PAT = re.compile(r"^\s*نقاش[یي](?:[\s\u200c]+([0-9۰-۹٠-٩,٬،]+))?\s*$")
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

GAMES = {}
_LOCK = asyncio.Lock()
_app = [None]
_last_scan = [0]

WORDS = {
    "animals": ["گربه", "سگ", "ماهی", "اسب", "فیل", "زرافه", "خرگوش", "پروانه", "مار", "لاک‌پشت", "پنگوئن", "شیر",
                "میمون", "مرغ", "خرس", "عنکبوت", "دلفین", "کوسه", "جغد", "عقاب", "قورباغه", "مورچه", "اردک", "شتر",
                "روباه", "گاو", "خوک", "زنبور", "کانگورو", "نهنگ"],
    "food": ["سیب", "موز", "هندوانه", "پیتزا", "همبرگر", "بستنی", "کیک", "نان", "تخم‌مرغ", "هویج", "ذرت", "انگور",
             "آناناس", "قارچ", "چای", "قهوه", "پنیر", "شکلات", "پاپ‌کورن", "لیمو", "توت‌فرنگی", "گوجه", "کباب", "دونات",
             "انار", "هلو", "ساندویچ", "نوشابه"],
    "objects": ["کلید", "چتر", "عینک", "کفش", "کلاه", "ساعت", "قیچی", "چراغ", "تلفن", "کتاب", "مداد", "کیف", "قفل",
                "آینه", "شانه", "چکش", "تلویزیون", "یخچال", "صندلی", "میز", "تخت", "فنجان", "چاقو", "لامپ", "توپ",
                "جاروبرقی", "پنکه", "کوله‌پشتی", "مسواک", "پتو"],
    "nature": ["خورشید", "ماه", "ستاره", "ابر", "باران", "رنگین‌کمان", "کوه", "درخت", "گل", "آتش", "برف", "رعد و برق",
               "دریا", "جزیره", "کاکتوس", "برگ", "آتشفشان", "صدف", "غار", "موج", "کره زمین", "گردباد"],
    "vehicles": ["ماشین", "هواپیما", "قایق", "دوچرخه", "موشک", "قطار", "اتوبوس", "هلیکوپتر", "موتور", "کشتی", "بالن",
                 "کامیون", "تاکسی", "اسکیت", "زیردریایی", "آمبولانس"],
    "places": ["خانه", "مسجد", "برج", "پل", "قلعه", "چادر", "مدرسه", "فانوس دریایی", "کارخانه", "چرخ‌وفلک", "کلبه",
               "بیمارستان", "برج ایفل", "اهرام مصر"],
    "people": ["چشم", "دست", "گوش", "دندان", "بینی", "قلب", "پا", "لب", "ربات", "دلقک", "دزد", "فضانورد", "پادشاه",
               "جادوگر", "سرباز", "آشپز", "کشاورز", "غول", "فرشته", "اژدها"],
    "fun": ["گیتار", "پیانو", "کادو", "بادکنک", "تاج", "شمشیر", "گنج", "دوربین", "ماسک", "ویولن", "شطرنج", "تاس",
            "پرچم", "طبل", "بلیط", "نقشه", "کمان", "فوتبال", "بیلیارد", "تلسکوپ"],
}


class DrawReport(Base):
    __tablename__ = "draw_reports"
    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, nullable=False, index=True)
    round_no = Column(Integer, nullable=False)
    reporter_id = Column(Integer, nullable=False)
    drawer_id = Column(Integer, nullable=False)
    status = Column(String(12), default="pending")
    created = Column(Integer, default=0)
    decided_by = Column(Integer, nullable=True)
    decision = Column(String(12), nullable=True)


class DrawGame(Base):
    __tablename__ = "draw_games"
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(12), default="lobby")
    updated = Column(Integer, default=0)
    data = Column(Text, default="{}")


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
        r = s.get(DrawGame, g["id"]) if g.get("id") else None
        if not r:
            r = DrawGame(status=g["status"], updated=g["updated"], data="{}")
            s.add(r)
            s.flush()
            g["id"] = r.id
        r.status = g["status"]
        r.updated = g["updated"]
        # خط‌های نقاشی فقط توی حافظه می‌مونن (حجمشون زیاده و بعد از ری‌استارت بی‌فایده‌ان)
        r.data = json.dumps({k: v for k, v in g.items() if k not in ("ops", "dirty", "pm")}, ensure_ascii=False)
        s.commit()
    except Exception:
        s.rollback()
        logger.exception("draw: save failed")
    finally:
        s.close()


def _pay(uid, amount):
    try:
        _cm().change(uid, Decimal(str(amount)))
    except Exception:
        logger.exception("draw: pay failed uid=%s amount=%s", uid, amount)


def _refund_all(g):
    for p in g["players"]:
        _pay(p["id"], g["bet"])


def _active_of(uid):
    for g in GAMES.values():
        if g["status"] in ("lobby", "playing") and any(p["id"] == uid for p in g["players"]):
            return g
    return None


def _chat_busy(chat_id):
    return any(g["chat"] == chat_id and g["status"] in ("lobby", "playing") for g in GAMES.values())


def _pl(g, uid):
    return next((p for p in g["players"] if p["id"] == uid), None)


def _drawer(g):
    return g["players"][g["drawer"]]


# ---------- منطق بازی ----------
def _pick_word(g):
    used = set(g.get("used") or [])
    pool = [(c, w) for c, ws in WORDS.items() for w in ws if w not in used]
    if not pool:
        pool = [(c, w) for c, ws in WORDS.items() for w in ws]
    cat, word = random.choice(pool)
    same = [w for w in WORDS[cat] if w != word]
    other = [w for c, ws in WORDS.items() if c != cat for w in ws if w != word]
    for _ in range(20):
        opts = random.sample(same, min(8, len(same))) + random.sample(other, 9) + [word]
        if len(set(opts)) == 18:
            break
    random.shuffle(opts)
    return word, opts


def _start_round(g):
    g["rnd"] += 1
    g["drawer"] = (g["rnd"] - 1) % len(g["players"])
    word, opts = _pick_word(g)
    g["word"], g["options"] = word, opts
    g.setdefault("used", []).append(word)
    g["guesses"] = {}
    g["ops"] = []
    g["phase"] = "draw"
    g["deadline"] = _now() + DRAW_SEC
    g["dirty"] = True
    _save(g)


def _begin(g):
    random.shuffle(g["players"])
    g["status"] = "playing"
    g["rnd"] = 0
    g["pm"] = True
    _start_round(g)


def _end_round(g):
    dr = _drawer(g)
    items, ok_n = [], 0
    for p in g["players"]:
        if p["id"] == dr["id"]:
            continue
        gu = (g.get("guesses") or {}).get(str(p["id"]))
        if gu is None:
            items.append({"id": p["id"], "name": p["name"], "st": "none", "pts": 0})
        elif gu["ok"]:
            items.append({"id": p["id"], "name": p["name"], "st": "ok", "pts": gu["pts"]})
            ok_n += 1
        else:
            items.append({"id": p["id"], "name": p["name"], "st": "wrong", "pts": 0})
    bonus = 0
    if ok_n and ok_n == len(g["players"]) - 1:
        bonus = DRAWER_ALL_BONUS
        dr["score"] += bonus
    g["last_round"] = {"rnd": g["rnd"], "drawer": dr["id"], "drawer_name": dr["name"], "word": g["word"],
                       "items": items, "drawer_pts": ok_n * DRAWER_PER + bonus, "empty": not g.get("ops")}
    g["phase"] = "result"
    g["deadline"] = _now() + RESULT_SEC
    g["dirty"] = True
    _save(g)


def _finish(g):
    ps = g["players"]
    top = max(p["score"] for p in ps)
    n = len(ps)
    pot = g["bet"] * n
    g["status"] = "done"
    g["phase"] = "end"
    g["dirty"] = True
    if top <= 0:
        # هیچ‌کس امتیاز نگرفت → شرط‌ها برمی‌گرده
        g["all_zero"] = True
        g["winners"] = []
        g["payout"] = "0"
        g["each"] = "0"
        g["tax"] = "0"
        g["net"] = {str(p["id"]): "0" for p in ps}
        _save(g)
        _refund_all(g)
        return
    winners = [p for p in ps if p["score"] == top]
    payout = (Decimal(pot) * (1 - TAX)).quantize(Decimal("0.1"), ROUND_DOWN)
    each = (payout / len(winners)).quantize(Decimal("0.1"), ROUND_DOWN)
    paid = each * len(winners)
    g["winners"] = [p["id"] for p in winners]
    g["payout"] = str(paid)
    g["each"] = str(each)
    g["tax"] = str(Decimal(pot) - paid)
    g["net"] = {str(p["id"]): str(each - g["bet"]) if p["score"] == top else str(-g["bet"]) for p in ps}
    _save(g)            # اول وضعیت «تمام‌شده» ذخیره می‌شه، بعد پرداخت (جلوگیری از پرداخت دوباره)
    for p in winners:
        _pay(p["id"], each)


def _cancel(g, why):
    g["status"] = "cancelled"
    g["why"] = why
    g["dirty"] = True
    _save(g)
    _refund_all(g)


def _step(g):
    """وضعیت بازی رو بر اساس زمان جلو می‌بره. باید زیر _LOCK صدا زده بشه."""
    now = _now()
    if g["status"] == "lobby" and now >= g["deadline"]:
        if len(g["players"]) >= MIN_PLAYERS:
            _begin(g)
        else:
            _cancel(g, "timeout")
    elif g["status"] == "playing" and now >= g["deadline"]:
        if g["phase"] == "draw":
            _end_round(g)
        elif g["phase"] == "result":
            if g["rnd"] >= ROUNDS:
                _finish(g)
            else:
                _start_round(g)


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
        return _btn("🎨 ورود به مینی‌اپ", url=f"{lk}?startapp=drw_{g['id']}", style="primary")
    return _btn("🎨 ورود به مینی‌اپ", callback_data=f"drw:app:{g['id']}", style="primary")


def _board(g):
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    rk = sorted(g["players"], key=lambda p: -p["score"])
    return "\n".join(f"{medals[i]} {_esc(p['name'])} — <b>{p['score']}</b>" for i, p in enumerate(rk))


def _text(g):
    names = "\n".join(f"{i + 1}. {_esc(p['name'])}" for i, p in enumerate(g["players"]))
    pot = g["bet"] * len(g["players"])
    head = (f"🎨 <b>نقاشی و حدس</b>\n\n💎 شرط هر نفر: <b>{g['bet']:,}</b> الماس\n"
            f"💰 جایزهٔ فعلی: <b>{pot:,}</b> الماس\n")
    if g["status"] == "lobby":
        return (head + f"👥 بازیکنان ({len(g['players'])}/{MAX_PLAYERS}):\n{names}\n\n"
                f"🖌 {ROUNDS} دور؛ هر دور یکی نقاشی می‌کشه و بقیه حدس می‌زنن.\n"
                f"⏳ حداکثر ۲ دقیقه برای پیوستن (حداقل ۲ نفر). «پیوستن» رو بزن و بعد وارد مینی‌اپ شو.")
    if g["status"] == "playing":
        if g.get("phase") == "end":
            return head
        dr = _drawer(g)
        return (head + f"\n🖌 دور <b>{g['rnd']}</b> از {ROUNDS} — نقاش: <b>{_esc(dr['name'])}</b>\n\n"
                f"🏅 امتیازها:\n{_board(g)}\n\n🎮 بازی شروع شده؛ از «ورود به مینی‌اپ» بازی کن.")
    if g["status"] == "done":
        if g.get("all_zero"):
            return (head + f"\n🏁 <b>بازی تموم شد</b>\n\n🏅 امتیازها:\n{_board(g)}\n\n"
                    "🤝 هیچ‌کس امتیازی نگرفت؛ شرط همه برگشت.")
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        rk = sorted(g["players"], key=lambda p: -p["score"])
        lines = []
        for i, p in enumerate(rk):
            win = p["id"] in g["winners"]
            net = Decimal(g["net"].get(str(p["id"]), "0"))
            sign = "+" if net > 0 else ""
            lines.append(f"{medals[i]} {_esc(p['name'])} — <b>{p['score']}</b> امتیاز  "
                         f"{'🏆 برنده' if win else '😵 بازنده'} ({sign}{net.normalize():f} 💎)")
        return (head + "\n🏁 <b>نتیجهٔ نهایی</b>\n\n" + "\n".join(lines) +
                f"\n\n🎁 جایزه: <b>{Decimal(g['payout']).normalize():f}</b> الماس (۵٪ مالیات کسر شد)")
    if g["status"] == "refunded":
        return head + "\n♻️ بازی به‌خاطر ری‌استارت ربات لغو شد و شرط همه برگشت."
    return head + "\n❌ بازی لغو شد و شرط بازیکن‌ها برگشت."


def _kb(g):
    from telegram import InlineKeyboardMarkup
    gid = g["id"]
    if g["status"] == "lobby":
        return InlineKeyboardMarkup([[_btn("✋ پیوستن", callback_data=f"drw:join:{gid}", style="success"), _app_btn(g)],
                                     [_btn("▶️ شروع بازی", callback_data=f"drw:go:{gid}"),
                                      _btn("لغو", callback_data=f"drw:x:{gid}", style="danger")]])
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
            logger.info("draw: edit failed: %s", e)


def _open_kb(gid):
    from telegram import InlineKeyboardMarkup, WebAppInfo
    import miniapp
    return InlineKeyboardMarkup([[_btn("🎨 ورود به بازی", web_app=WebAppInfo(url=f"{miniapp.app_url()}?d={gid}"))]])


async def _pm_players(bot, g):
    for p in g["players"]:
        try:
            await bot.send_message(p["id"], f"🎨 نقاشی شروع شد! شرط: {g['bet']:,} الماس\nبرای بازی دکمهٔ زیر رو بزن 👇",
                                   reply_markup=_open_kb(g["id"]))
        except Exception:
            pass


async def _announce(bot, g):
    await _edit(bot, g)
    if g.pop("pm", False):
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
    usage = (f"🎨 برای ساخت نقاشی بنویس: «نقاشی 20» (شرط به الماس)\n"
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
            await m.reply_text("⏳ توی این گروه یه نقاشی در جریانه؛ صبر کن تموم بشه.")
            return
        if _active_of(u.id):
            await m.reply_text("تو الان توی یه نقاشی دیگه‌ای؛ اول اون تموم بشه.")
            return
        bal = cm.wallet(u.id)["balance"]
        if bal < bet:
            await m.reply_text(f"❌ الماس کافی نداری.\n💎 موجودی: {cm.fmt_bal(bal)} | شرط: {bet:,}")
            return
        cm.change(u.id, -bet)
        g = {"id": None, "chat": m.chat_id, "msg": None, "creator": u.id, "bet": bet, "status": "lobby",
             "phase": "lobby", "players": [{"id": u.id, "name": (u.first_name or "بازیکن")[:20], "score": 0}],
             "rnd": 0, "drawer": 0, "word": None, "options": [], "guesses": {}, "ops": [], "used": [],
             "last_round": None, "deadline": _now() + LOBBY_SEC, "created": _now()}
        _save(g)
        if not g["id"]:
            cm.change(u.id, bet)
            await m.reply_text("❌ خطای داخلی؛ دوباره امتحان کن.")
            return
        GAMES[g["id"]] = g
        try:
            sent = await m.reply_text(_text(g), parse_mode="HTML", reply_markup=_kb(g))
            g["msg"] = sent.message_id
            _save(g)
        except Exception:
            logger.exception("draw: lobby message failed")
            del GAMES[g["id"]]
            _cancel(g, "send failed")


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
                    await context.bot.send_message(uid, "🎨 نقاشی — برای ورود دکمهٔ زیر رو بزن 👇", reply_markup=_open_kb(gid))
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
                note, alert = "تو توی یه نقاشی دیگه‌ای.", True
            elif not await cm.is_member(context.bot, uid):
                note, alert = "اول باید عضو کانال‌های ربات بشی.", True
            elif cm.wallet(uid)["balance"] < g["bet"]:
                note, alert = f"الماس کافی نداری (شرط {g['bet']:,}).", True
            else:
                cm.change(uid, -g["bet"])
                g["players"].append({"id": uid, "name": (q.from_user.first_name or "بازیکن")[:20], "score": 0})
                _save(g)
                note, edit = "پیوستی ✅ حالا «ورود به مینی‌اپ» رو بزن.", True
                if len(g["players"]) >= MAX_PLAYERS:
                    _begin(g)
                    begin = True
        elif act == "go":
            if uid != g["creator"]:
                note, alert = "فقط سازندهٔ بازی می‌تونه شروع کنه.", True
            elif len(g["players"]) < MIN_PLAYERS:
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
        if edit or begin:
            g.pop("dirty", None)      # همین‌جا خودمون پیام رو آپدیت می‌کنیم
    try:
        await q.answer(note or None, show_alert=alert)
    except Exception:
        pass
    if g and (edit or begin):
        await _announce(context.bot, g)


# ---------- API مینی‌اپ ----------
def state_for(g, uid, data=None):
    data = data or {}
    now = _now()
    playing = g["status"] == "playing"
    drawer = _drawer(g)["id"] if playing else None
    ops = g.get("ops") or []
    total = len(ops)
    try:
        since = int(data.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    reset = data.get("rnd") != g.get("rnd")
    start = 0 if reset else max(0, min(since, total))
    gs = g.get("guesses") or {}
    mine = gs.get(str(uid)) if playing else None
    out = {
        "id": g["id"], "status": g["status"], "phase": g.get("phase"), "bet": g["bet"],
        "pot": g["bet"] * len(g["players"]), "creator": g["creator"], "me": uid,
        "rnd": g.get("rnd", 0), "rounds": ROUNDS, "drawer": drawer,
        "draw_sec": DRAW_SEC, "result_sec": RESULT_SEC, "lobby_sec": LOBBY_SEC,
        "left": max(0, g["deadline"] - now) if g["status"] in ("lobby", "playing") else 0,
        "players": [{"id": p["id"], "idx": i, "name": p["name"], "score": p["score"],
                     "ans": bool(playing and g.get("phase") == "draw" and str(p["id"]) in gs)}
                    for i, p in enumerate(g["players"])],
        "word": g.get("word") if playing and (uid == drawer or g.get("phase") == "result") else None,
        "options": (g.get("options") or []) if playing and uid != drawer else [],
        "my": ({"opt": mine["opt"], "ok": mine["ok"], "pts": mine["pts"]} if mine else None),
        "last_round": g.get("last_round"),
        "winners": g.get("winners"), "payout": g.get("payout"), "each": g.get("each"), "tax": g.get("tax"),
        "net": g.get("net"), "all_zero": bool(g.get("all_zero")),
        "ops": ops[start:start + OPS_CHUNK], "from": start, "total": total, "reset": reset,
    }
    return out


def active_summary(uid):
    g = _active_of(uid)
    return {"id": g["id"], "status": g["status"]} if g else None


def _clean_ops(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for o in raw[:OPS_PER_REQ]:
        if not isinstance(o, dict):
            continue
        if o.get("clr"):
            out.append({"clr": 1})
            continue
        c, w, p = o.get("c"), o.get("w"), o.get("p")
        if isinstance(c, bool) or isinstance(w, bool) or not isinstance(c, int) or not isinstance(w, int):
            continue
        if not (0 <= c < NCOL and 0 <= w < NWID) or not isinstance(p, list) or len(p) < 2:
            continue
        pts, bad = [], False
        for v in p[:PTS_PER_OP]:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                bad = True
                break
            pts.append(max(0, min(1000, int(v))))
        if bad:
            continue
        if len(pts) % 2:
            pts.pop()
        if len(pts) >= 2:
            out.append({"c": c, "w": w, "p": pts})
    return out


async def api(path, uid, data):
    gid = data.get("gid")
    g = GAMES.get(gid) if isinstance(gid, int) and not isinstance(gid, bool) else None
    if g and not any(p["id"] == uid for p in g["players"]):
        return {"game": None, "error": "not_player"}
    if not g:
        g = _active_of(uid)
    if not g:
        return {"game": None, "error": "no_game"}
    err = None
    async with _LOCK:
        _step(g)
        if path == "/api/drw/ops":
            if g["status"] != "playing" or g.get("phase") != "draw":
                err = "not_draw"
            elif _drawer(g)["id"] != uid:
                err = "not_drawer"
            elif data.get("rnd") != g["rnd"]:
                err = "stale"
            elif _now() >= g["deadline"]:
                err = "not_draw"
            else:
                for o in _clean_ops(data.get("ops")):
                    if len(g["ops"]) >= MAX_OPS:
                        break
                    if o.get("clr") and (not g["ops"] or g["ops"][-1].get("clr")):
                        continue
                    g["ops"].append(o)
        elif path == "/api/drw/guess":
            if g["status"] != "playing" or g.get("phase") != "draw" or _now() >= g["deadline"]:
                err = "not_draw"
            elif _drawer(g)["id"] == uid:
                err = "drawer"
            elif str(uid) in g["guesses"]:
                err = "already"
            elif not g["ops"]:
                err = "wait_drawing"
            else:
                opt = data.get("opt")
                if isinstance(opt, bool) or not isinstance(opt, int) or not 0 <= opt < len(g["options"]):
                    err = "bad_option"
                else:
                    ok = g["options"][opt] == g["word"]
                    pts = 0
                    if ok:
                        left = max(0, g["deadline"] - _now())
                        first = not any(x["ok"] for x in g["guesses"].values())
                        pts = BASE_PTS + left * PER_SEC + (FIRST_BONUS if first else 0)
                        _pl(g, uid)["score"] += pts
                        _drawer(g)["score"] += DRAWER_PER
                    g["guesses"][str(uid)] = {"opt": opt, "ok": ok, "pts": pts}
                    if all(str(p["id"]) in g["guesses"] for p in g["players"] if p["id"] != _drawer(g)["id"]):
                        _end_round(g)       # همه جواب دادن → دور زودتر تموم می‌شه
                    else:
                        _save(g)
        elif path == "/api/drw/report":
            if g["status"] != "playing" or g.get("phase") != "draw" or _drawer(g)["id"] == uid:
                err = "report_unavailable"
            elif not g.get("ops"):
                err = "no_drawing"
            else:
                result = await _create_report(g, uid, _app[0].bot)
                if result != "ok":
                    err = result
        elif path != "/api/drw/state":
            return {"game": None, "error": "not_found"}
        res = {"game": state_for(g, uid, data)}
    if err:
        res["error"] = err
    return res


# ---------- کار پس‌زمینه ----------
async def _orphans(bot):
    """بازی‌هایی که توی دیتابیس نیمه‌کاره موندن ولی توی این پروسه نیستن (ری‌استارت/دیپلوی) → لغو و برگشت شرط."""
    cutoff = _now() - ORPHAN_AGE
    s = get_session()
    try:
        rows = [(r.id, r.data) for r in s.query(DrawGame).filter(DrawGame.status.in_(["lobby", "playing"]),
                                                                  DrawGame.updated < cutoff).all()]
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
        logger.info("draw: orphan game %s refunded", gid)


async def tick(context):
    bot = context.bot
    now = _now()
    todo = []
    try:
        async with _LOCK:
            for g in list(GAMES.values()):
                _step(g)
                if g.get("dirty"):
                    g["dirty"] = False
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
        logger.exception("draw: tick failed")


async def _report_decision(update, context):
    q = update.callback_query
    parts = (q.data or "").split(":")
    if len(parts) != 4:
        await q.answer()
        return
    try:
        rid, decision = int(parts[2]), parts[3]
    except Exception:
        await q.answer("درخواست نامعتبره.", show_alert=True)
        return
    try:
        import selfbot
        allowed = set(getattr(selfbot, "SUPPORT_IDS", set()))
        allowed.update(getattr(selfbot, "ADMIN_IDS", set()))
    except Exception:
        allowed = set()
    if q.from_user.id not in allowed:
        await q.answer("فقط پشتیبانی می‌تونه بررسی کنه.", show_alert=True)
        return
    s = get_session()
    try:
        r = s.get(DrawReport, rid)
        if not r or r.status != "pending":
            await q.answer("این گزارش قبلاً بررسی شده یا وجود نداره.", show_alert=True)
            return
        r.status = "approved" if decision == "ok" else "rejected"
        r.decided_by = q.from_user.id
        r.decision = decision
        s.commit()
        amount = 5 if decision == "ok" else -2
        _cm().change(r.reporter_id, Decimal(amount))
        try:
            await context.bot.send_message(r.reporter_id,
                "✅ گزارش نقاشی تأیید شد؛ ۵ الماس به حسابت اضافه شد." if decision == "ok"
                else "❌ گزارش نقاشی رد شد؛ ۲ الماس از حسابت کم شد.")
        except Exception:
            pass
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.answer("بررسی ثبت شد.")
    except Exception:
        s.rollback()
        logger.exception("draw: report decision failed")
        await q.answer("خطا در ثبت بررسی.", show_alert=True)
    finally:
        s.close()


async def _create_report(g, uid, bot):
    s = get_session()
    try:
        exists = s.query(DrawReport).filter_by(game_id=g["id"], round_no=g.get("rnd", 0),
                                               reporter_id=uid).first()
        if exists:
            return "duplicate"
        r = DrawReport(game_id=g["id"], round_no=g.get("rnd", 0), reporter_id=uid,
                       drawer_id=_drawer(g)["id"], status="pending", created=_now())
        s.add(r)
        s.commit()
        rid = r.id
    except Exception:
        s.rollback()
        logger.exception("draw: report save failed")
        return "db_error"
    finally:
        s.close()
    try:
        import selfbot
        support = set(getattr(selfbot, "SUPPORT_IDS", set()))
        support.update(getattr(selfbot, "ADMIN_IDS", set()))
    except Exception:
        support = set()
    if not support:
        s = get_session()
        try:
            r = s.get(DrawReport, rid)
            if r:
                r.status = "unrouted"
                s.commit()
        finally:
            s.close()
        return "no_support"
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    dr = _drawer(g)
    summary = (f"⚠️ <b>گزارش نقاشی</b>\\nبازی: <code>{g['id']}</code> | دور: {g.get('rnd')}\\n"
               f"گزارش‌دهنده: <code>{uid}</code>\\nنقاش: <code>{dr['id']}</code> ({_esc(dr['name'])})\\n"
               f"کلمهٔ مخفی: <b>{_esc(g.get('word'))}</b>\\nتعداد خطوط ثبت‌شده: {len(g.get('ops') or [])}")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأیید (+۵ الماس)", callback_data=f"drw:report:{rid}:ok"),
        InlineKeyboardButton("❌ رد (−۲ الماس)", callback_data=f"drw:report:{rid}:no")
    ]])
    # پیش‌نمایش واقعی نقاشی برای اینکه پشتیبانی بتواند محتوا را بررسی کند.
    preview = None
    try:
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (700, 700), "#fffef9")
        pen = ImageDraw.Draw(image)
        palette = ["#111111", "#ff1744", "#ff6d00", "#ffd600", "#76ff03", "#00c853",
                   "#00e5ff", "#2979ff", "#651fff", "#d500f9", "#ff4081", "#8d5524", "#9e9e9e", "#fffef9"]
        widths = [6, 14, 34]
        for op in (g.get("ops") or [])[:MAX_OPS]:
            if op.get("clr"):
                image = Image.new("RGB", (700, 700), "#fffef9")
                pen = ImageDraw.Draw(image)
                continue
            pts = op.get("p") or []
            if len(pts) < 2:
                continue
            coords = [(max(0, min(700, int(pts[i] * .7))), max(0, min(700, int(pts[i+1] * .7)))
                       ) for i in range(0, len(pts)-1, 2)]
            if len(coords) == 1:
                x, y = coords[0]
                w = widths[max(0, min(2, int(op.get("w", 1))))]
                c = palette[max(0, min(13, int(op.get("c", 0))))]
                pen.ellipse((x-w/2, y-w/2, x+w/2, y+w/2), fill=c)
            else:
                pen.line(coords, fill=palette[max(0, min(13, int(op.get("c", 0))))],
                         width=widths[max(0, min(2, int(op.get("w", 1))))], joint="curve")
        bio = io.BytesIO()
        image.save(bio, format="PNG")
        bio.seek(0)
        preview = bio
    except Exception:
        logger.exception("draw: report preview generation failed")
    sent = 0
    for sid in support:
        try:
            if preview:
                preview.seek(0)
                await bot.send_photo(sid, photo=preview, caption=summary, parse_mode="HTML", reply_markup=kb)
            else:
                await bot.send_message(sid, summary, parse_mode="HTML", reply_markup=kb)
            sent += 1
        except Exception:
            logger.warning("draw: cannot notify support %s", sid)
    if not sent:
        s = get_session()
        try:
            r = s.get(DrawReport, rid)
            if r:
                r.status = "unrouted"
                s.commit()
        finally:
            s.close()
        return "no_support"
    return "ok"


def setup(app):
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters
    _app[0] = app
    database.Base.metadata.create_all(database.engine)
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS & filters.Regex(_PAT), cmd_new), group=-12)
    app.add_handler(CallbackQueryHandler(_report_decision, pattern=r"^drw:report:\\d+:(ok|no)$"), group=-22)
    app.add_handler(CallbackQueryHandler(cb, pattern=r"^drw:(?!report:).+"), group=-21)
    app.job_queue.run_repeating(tick, interval=1, first=8, name="draw-tick")
