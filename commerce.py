# -*- coding: utf-8 -*-
"""منو، عضویت اجباری، الماس، دعوت، انتقال و رسید پرداخت."""
import asyncio
import os
import re
import secrets
import time
from decimal import Decimal
from sqlalchemy import Column, BigInteger, String, Numeric, Integer, Boolean, inspect, text
from telegram import InlineKeyboardButton as _B, InlineKeyboardMarkup as KB, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from database import Base, get_session
import selfbot

CHANNELS = [('@selfsaz_iran', 'https://t.me/selfsaz_iran'), ('@fox_frenzy', 'https://t.me/fox_frenzy')]
SUPPORT = '@escotch'
CARD = '6219 8618 5116 0068'
OWNER = 'ظریفی'
ACTIVATE_COST = 5
# هدیهٔ شروع: کاربری که ربات رو استارت کنه و عضو کانال‌ها بشه، یک‌بار ۲۵۰ الماس (۵ روز سلف) می‌گیره.
# پیش‌فرض روشنه؛ برای خاموش‌کردنش توی Railway بذار SELF_TRIAL=0
TRIAL_DAYS = 7          # (قدیمی) فقط برای کاربرانی که قبلاً دورهٔ زمانی گرفتن
TRIAL_BONUS = 250       # هدیهٔ شروع: ۲۵۰ الماس یک‌بار برای هر کاربر (معادل ۵ روز)
TRIAL_ENABLED = os.environ.get('SELF_TRIAL', '1').strip().lower() not in ('0', 'false', 'no', 'off')

_EMOJI_RE = __import__('re').compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\uFE0F\u200d]")
def B(text, *args, **kwargs):
    text = _EMOJI_RE.sub("", text or "").strip()
    api = kwargs.pop("api_kwargs", {}) or {}
    api.setdefault("style", "danger" if any(x in text for x in ("رد", "لغو", "خاموش", "حذف")) else "success" if any(x in text for x in ("تأیید", "روشن", "عضو شدم")) else "primary")
    return _B(text, *args, api_kwargs=api, **kwargs)
class Wallet(Base):
    __tablename__ = 'diamond_wallets'
    user_id = Column(BigInteger, primary_key=True)
    balance = Column(Numeric(12, 1), nullable=False, default=Decimal('0.0'))
    referrer = Column(BigInteger, nullable=True)
    referred_awarded = Column(Boolean, default=False)
    last_charge = Column(Integer, default=0)
    self_started = Column(Boolean, default=False)
    trial_until = Column(Integer, nullable=True)
    trial_used = Column(Boolean, default=False)
    spent = Column(Numeric(12, 1), default=Decimal('0.0'))
class Payment(Base):
    __tablename__ = 'diamond_payments'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    plan = Column(String(20), nullable=False)
    discount = Column(Integer, default=0)
    code = Column(String(32), nullable=True)
    file_id = Column(String(255), nullable=True)
    file_kind = Column(String(10), nullable=True)
    claimed_by = Column(BigInteger, nullable=True)
    claimed_name = Column(String(64), nullable=True)
    status = Column(String(20), default='pending')
    created = Column(Integer, default=lambda: int(time.time()))

class Ticket(Base):
    __tablename__ = 'support_tickets'
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    status = Column(String(10), default='open')
    subject = Column(String(200), nullable=True)
    created = Column(Integer, default=lambda: int(time.time()))
    last = Column(Integer, default=lambda: int(time.time()))
class DiscountCode(Base):
    __tablename__ = 'discount_codes'
    code = Column(String(32), primary_key=True)
    percent = Column(Integer, nullable=False)
    max_uses = Column(Integer, nullable=False, default=1)
    used = Column(Integer, nullable=False, default=0)
    expires = Column(Integer, nullable=True)
    created = Column(Integer, default=lambda: int(time.time()))
class DiscountUse(Base):
    __tablename__ = 'discount_uses'
    code = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    ts = Column(Integer, default=lambda: int(time.time()))
class GiftCode(Base):
    __tablename__ = 'gift_codes'
    code = Column(String(32), primary_key=True)
    amount = Column(Numeric(12, 1), nullable=False)
    max_uses = Column(Integer, nullable=False, default=1)
    used = Column(Integer, nullable=False, default=0)
    expires = Column(Integer, nullable=True)
    created = Column(Integer, default=lambda: int(time.time()))
    creator = Column(BigInteger, nullable=True)
class GiftRedeem(Base):
    __tablename__ = 'gift_redeems'
    code = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, primary_key=True)
    ts = Column(Integer, default=lambda: int(time.time()))

class Setting(Base):
    __tablename__ = 'bot_settings'
    key = Column(String(40), primary_key=True)
    val = Column(String(200), nullable=True)
class StaffMember(Base):
    __tablename__ = 'support_staff'
    user_id = Column(BigInteger, primary_key=True)
    perms = Column(String(300), nullable=False, default='')
    added_by = Column(BigInteger, nullable=True)
    created = Column(Integer, default=lambda: int(time.time()))

def get_setting(key, default=None):
    s = get_session()
    try:
        r = s.get(Setting, key)
        return r.val if r and r.val is not None else default
    finally:
        s.close()

def set_setting(key, val):
    s = get_session()
    try:
        r = s.get(Setting, key)
        if not r:
            r = Setting(key=key); s.add(r)
        r.val = str(val); s.commit()
    finally:
        s.close()

# ---------- دسترسی‌های پشتیبانی ----------
# ادمین‌ها (ADMIN_IDS) همیشه همهٔ دسترسی‌ها رو دارن. پشتیبان‌های قدیمی (SUPPORT_IDS) دسترسی پایه دارن
# و بقیه رو هر کس که دسترسی «مدیریت پشتیبان‌ها» داره می‌تونه تنظیم کنه.
PERMS = (('receipts', 'رسیدها'), ('tickets', 'تیکت‌ها'), ('lookup', 'بررسی کاربر'), ('balance', 'تغییر الماس کاربر'),
         ('gift', 'کد هدیه'), ('discount', 'کد تخفیف'), ('price', 'قیمت الماس'), ('broadcast', 'پیام همگانی'),
         ('memehelp', 'دستورات میم'), ('staff', 'مدیریت پشتیبان‌ها'))
PERM_KEYS = [k for k, _ in PERMS]
BASIC_PERMS = {'receipts', 'tickets', 'lookup', 'memehelp', 'broadcast', 'gift', 'discount'}
NEW_STAFF_PERMS = {'receipts', 'tickets'}

def _staff_row(uid):
    s = get_session()
    try:
        r = s.get(StaffMember, uid)
        return None if r is None else {x for x in (r.perms or '').split(',') if x in PERM_KEYS}
    finally:
        s.close()

def perms_of(uid):
    if uid in selfbot.ADMIN_IDS:
        return set(PERM_KEYS)
    row = _staff_row(uid)
    if row is not None:
        return row
    if uid in selfbot.SUPPORT_IDS:
        return set(BASIC_PERMS)
    return set()

def has_perm(uid, perm):
    return perm in perms_of(uid)

def staff_ids(perm):
    ids = set(selfbot.ADMIN_IDS)
    s = get_session()
    try:
        db = {r.user_id: {x for x in (r.perms or '').split(',')} for r in s.query(StaffMember).all()}
    finally:
        s.close()
    for u in set(selfbot.SUPPORT_IDS) | set(db):
        if perm in db.get(u, BASIC_PERMS):
            ids.add(u)
    return ids

def save_perms(uid, perms, by=None):
    s = get_session()
    try:
        r = s.get(StaffMember, uid)
        if not r:
            r = StaffMember(user_id=uid, added_by=by); s.add(r)
        r.perms = ','.join(k for k in PERM_KEYS if k in perms); s.commit()
    finally:
        s.close()

def all_staff():
    s = get_session()
    try:
        db = {r.user_id: {x for x in (r.perms or '').split(',') if x in PERM_KEYS} for r in s.query(StaffMember).all()}
    finally:
        s.close()
    out = dict(db)
    for u in selfbot.SUPPORT_IDS:
        out.setdefault(u, set(BASIC_PERMS))
    for u in selfbot.ADMIN_IDS:
        out.pop(u, None)
    return out

async def _notify_admins(context, text, exclude=None):
    for a in set(selfbot.ADMIN_IDS):
        if a == exclude:
            continue
        try:
            await context.bot.send_message(a, text)
        except Exception:
            pass

def adjust_balance(uid, delta):
    """(قبلی، جدید)؛ موجودی هیچ‌وقت منفی نمی‌شه."""
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if not w:
            w = Wallet(user_id=uid, balance=Decimal('0.0'), last_charge=int(time.time())); s.add(w)
        old = Decimal(w.balance or 0)
        new = max(Decimal('0.0'), old + Decimal(str(delta)))
        w.balance = new; s.commit()
        return old, new
    finally:
        s.close()

def _migrate_wallet():
    # create_all does not add columns to existing SQLite/Postgres tables.
    try:
        insp = inspect(__import__('database').engine)
        if 'diamond_wallets' not in insp.get_table_names():
            return
        cols = {c['name'] for c in insp.get_columns('diamond_wallets')}
        dialect = __import__('database').engine.dialect.name
        with __import__('database').engine.begin() as conn:
            if 'trial_until' not in cols:
                conn.execute(text('ALTER TABLE diamond_wallets ADD COLUMN trial_until INTEGER'))
            if 'trial_used' not in cols:
                conn.execute(text('ALTER TABLE diamond_wallets ADD COLUMN trial_used BOOLEAN DEFAULT FALSE'))
            if 'spent' not in cols:
                conn.execute(text('ALTER TABLE diamond_wallets ADD COLUMN spent NUMERIC(12,1) DEFAULT 0'))
        if 'diamond_payments' in insp.get_table_names():
            pcols = {c['name'] for c in insp.get_columns('diamond_payments')}
            with __import__('database').engine.begin() as conn:
                if 'discount' not in pcols:
                    conn.execute(text('ALTER TABLE diamond_payments ADD COLUMN discount INTEGER DEFAULT 0'))
                if 'code' not in pcols:
                    conn.execute(text('ALTER TABLE diamond_payments ADD COLUMN code VARCHAR(32)'))
                for col, ddl in (('file_id', 'VARCHAR(255)'), ('file_kind', 'VARCHAR(10)'), ('claimed_by', 'BIGINT'), ('claimed_name', 'VARCHAR(64)')):
                    if col not in pcols:
                        conn.execute(text(f'ALTER TABLE diamond_payments ADD COLUMN {col} {ddl}'))
    except Exception:
        import logging; logging.getLogger(__name__).exception('wallet migration failed')

def start_trial(uid):
    """هدیهٔ شروع (۲۵۰ الماس) فقط یک‌بار و فقط بعد از تأیید عضویت کانال‌ها. خروجی: (None, آیا همین الان داده شد)"""
    now = int(time.time())
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if not w:
            w = Wallet(user_id=uid, balance=Decimal('0.0'), last_charge=now)
            s.add(w)
        if not w.trial_used:
            w.trial_used = True
            w.balance = Decimal(w.balance or 0) + Decimal(TRIAL_BONUS)
            s.commit()
            # توجه: اینجا دسترسی سلف باز نمی‌شه. کاربر باید «فعالسازی سلف» رو بزنه تا وضعیت «روشن» ثبت بشه
            # و ساعتی ۲ الماس از همین موجودی (از جمله ۲۵۰ الماس هدیه) کم بشه.
            return None, True
        return None, False
    finally:
        s.close()

def sync_self_state():
    """سلفِ فعال (دسترسی باز + نشست وصل‌شده) که توی کیف پول «روشن» ثبت نشده، روشن ثبت می‌شه تا ساعتی ۲ الماس کم بشه.
    دسترسی بازِ بدون نشست (فقط از هدیهٔ قدیمی) بسته می‌شه؛ کاربر باید «فعالسازی سلف» بزنه. ادمین‌ها و کاربرانی که
    هدیهٔ شروع نگرفتن (دسترسی دستیِ ادمین) دست نمی‌خورن. خروجی: تعداد کاربرِ اصلاح‌شده."""
    now = int(time.time()); n = 0
    s = get_session()
    try:
        rows = s.query(Wallet).filter(Wallet.trial_used.is_(True), Wallet.self_started.isnot(True)).all()
        for w in rows:
            if w.user_id in selfbot.ADMIN_IDS:
                continue
            r = selfbot.get_row(w.user_id)
            if not (r and r.enabled):
                continue
            if r.session_enc and Decimal(w.balance or 0) > 0:
                w.self_started = True; w.last_charge = now; n += 1
            else:
                selfbot.save_row(w.user_id, enabled=False); n += 1
        s.commit()
    except Exception:
        s.rollback()
        import logging; logging.getLogger(__name__).exception('sync_self_state failed')
    finally:
        s.close()
    return n

def add_spent(uid, amount):
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if w:
            w.spent = Decimal(w.spent or 0) + Decimal(str(amount)); s.commit()
    finally:
        s.close()

def wallet(uid, ref=None):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w:
            w=Wallet(user_id=uid,balance=Decimal('0.0'),last_charge=int(time.time()))
            if ref and ref != uid and s.get(Wallet,ref): w.referrer=ref
            s.add(w); s.commit()
        return {'balance': Decimal(w.balance or 0), 'referrer':w.referrer,'self_started':bool(w.self_started),'last_charge':w.last_charge or int(time.time()), 'trial_until':getattr(w,'trial_until',None), 'trial_used':bool(getattr(w,'trial_used',False)), 'spent': Decimal(getattr(w,'spent',0) or 0)}
    finally:s.close()
def change(uid, amount):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w: w=Wallet(user_id=uid,balance=Decimal('0.0'),last_charge=int(time.time())); s.add(w)
        w.balance=Decimal(w.balance or 0)+Decimal(str(amount)); s.commit(); return Decimal(w.balance)
    finally:s.close()

def award_referral(uid):
    s=get_session()
    try:
        w=s.get(Wallet,uid)
        if not w or not w.referrer or w.referred_awarded: return False
        ref=s.get(Wallet,w.referrer)
        if ref:
            w.balance=Decimal(w.balance or 0)+Decimal('50.0')
            ref.balance=Decimal(ref.balance or 0)+Decimal('50.0')
            w.referred_awarded=True
            s.commit()
            return True
        return False
    finally:s.close()

def btn(text, data=None, url=None, style="primary"):
    kw = {"api_kwargs": {"style": style}}
    if url:
        return B(text, url=url, **kw)
    return B(text, callback_data=data, **kw)

# منوی اصلی؛ همون چیدمان عکس: کیبورد ثابت پایین چت (و نسخهٔ اینلاین برای دکمهٔ «منوی اصلی»).
# (متن دکمه، کلید، رنگ؛ None = پیش‌فرض/خاکستری). ترتیب هر ردیف همون ترتیب نمایش روی صفحه‌ست.
MENU_ROWS = [
    [('فعالسازی سلف', 'activate', 'success')],
    [('داشبورد', 'board', None), ('حساب کاربری', 'account', None)],
    [('خرید الماس', 'buy', 'primary')],
    [('انتقال الماس', 'transfer', 'primary'), ('الماس رایگان', 'free', None), ('راهنما و پشتیبانی', 'help', 'primary')],
]
MENU_TEXT = {t: k for row in MENU_ROWS for t, k, _ in row}
MENU_DATA = {'activate': 'shop:create', 'account': 'shop:dash', 'board': 'shop:board', 'buy': 'shop:buy',
             'transfer': 'shop:transfer', 'free': 'shop:free', 'help': 'shop:helpmenu'}

def main_reply_kb():
    return ReplyKeyboardMarkup([[KeyboardButton(t, api_kwargs=({'style': st} if st else None)) for t, k, st in row] for row in MENU_ROWS],
                               resize_keyboard=True, is_persistent=True, input_field_placeholder='یکی از گزینه‌ها رو انتخاب کن')

def app_row():
    """دکمهٔ مینی‌اپ (فقط وقتی آدرس عمومی تنظیم شده باشه)."""
    try:
        import miniapp
        from telegram import WebAppInfo
        u = miniapp.app_url()
        return [_B('📊 مینی‌اپ داشبورد', web_app=WebAppInfo(url=u))] if u else None
    except Exception:
        return None

def home_kb():
    rows = [[_B(t, callback_data=MENU_DATA[k], api_kwargs=({'style': st} if st else None)) for t, k, st in row] for row in MENU_ROWS]
    ar = app_row()
    if ar: rows.append(ar)
    return KB(rows)
def join_kb():
    rows=[[B('عضویت در '+ch, url=url)] for ch,url in CHANNELS]
    rows.append([B('✅ عضو شدم',callback_data='shop:check')]); return KB(rows)
async def is_member(bot,uid):
    for ch,_ in CHANNELS:
        try:
            m=await bot.get_chat_member(ch,uid)
            if m.status in ('left','kicked'): return False
        except Exception:
            return False
    return True
PRICES = {'1': 75000, '2': 120000}      # (قدیمی) فقط برای رسیدهای قبلی
DIAMONDS = {'1': 1000, '2': 1800}
MIN_QTY, MAX_QTY, QTY_STEP, DEFAULT_QTY = 500, 100000, 500, 1000
DEFAULT_UNIT = 75        # تومان برای هر الماس؛ پشتیبانی از پنلش عوضش می‌کنه

def unit_price():
    try:
        v = int(get_setting('unit_price', DEFAULT_UNIT))
        return v if v > 0 else DEFAULT_UNIT
    except Exception:
        return DEFAULT_UNIT

def plan_info(plan):
    """(تعداد الماس، قیمت پایه به تومان). پلن جدید: c<تعداد>x<قیمت هر الماس>."""
    if plan in PRICES:
        return DIAMONDS[plan], PRICES[plan]
    m = re.fullmatch(r'c(\d+)x(\d+)', plan or '')
    if m:
        n, u = int(m.group(1)), int(m.group(2))
        return n, n * u
    return 0, 0

def _qty(context):
    try:
        return min(MAX_QTY, max(MIN_QTY, int(context.user_data.get('buy_n', DEFAULT_QTY))))
    except Exception:
        return DEFAULT_QTY
_warn_level = {}   # uid -> سطح هشدار کمبود الماس (۱: کمتر از ۲۴ ساعت، ۲: کمتر از ۳ ساعت)

def money(n):
    return f'{int(n):,}'.replace(',', '٬')

def _pct(context):
    d = context.user_data.get('disc')
    return int(d['pct']) if d else 0

def buy_text(pct=0, n=DEFAULT_QTY):
    u = unit_price(); base = n * u
    price = (f'{money(base)} ← {money(base * (100 - pct) // 100)} تومان ({pct}٪ تخفیف 🏷)' if pct else f'{money(base)} تومان')
    return ('💎 خرید الماس\n\n'
            f'💎 تعداد: {n} الماس\n💰 مبلغ: {price}\n(قیمت هر الماس: {money(u)} تومان)\n\n'
            'با دکمه‌های مثبت و منفی تعداد رو تغییر بده یا مقدار دلخواه رو دستی وارد کن.\n'
            + (f'🏷 کد تخفیف فعاله: {pct}٪\n' if pct else '')
            + f'\nشماره کارت: {CARD}\nبه نام: {OWNER}\nبعد از تأیید تعداد، مبلغ رو واریز کن و رسید رو همین‌جا بفرست.')

def buy_kb(n=DEFAULT_QTY):
    return KB([[btn(f'{n} الماس', 'shop:q:noop')],
               [btn(f'− {QTY_STEP}', 'shop:q:sub', style='danger'), btn(f'+ {QTY_STEP}', 'shop:q:add', style='success')],
               [btn('تنظیم دستی تعداد الماس', 'shop:q:manual')],
               [btn('کد تخفیف', 'shop:disc')],
               [btn('تأیید تعداد و ادامه خرید', 'shop:q:ok', style='success')],
               [btn('بازگشت', 'shop:home', style='danger')]])

async def _show_pay(q, context, plan):
    context.user_data['payment_plan'] = plan
    dia, base = plan_info(plan)
    pct = _pct(context); final = base * (100 - pct) // 100
    await show_panel(q, f'💳 خرید {dia} الماس\n💰 مبلغ قابل پرداخت: {money(final)} تومان' + (f' (با {pct}٪ تخفیف)' if pct else '')
                     + f'\nشماره کارت: {CARD}\nبه نام: {OWNER}\n\nحالا عکس یا فایل رسید پرداخت رو ارسال کن. برای لغو بنویس «لغو».')

def make_discount(percent, max_uses, hours=None):
    s = get_session()
    try:
        for _ in range(10):
            code = 'OFF-' + ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
            if not s.get(DiscountCode, code):
                break
        exp = int(time.time()) + int(hours) * 3600 if hours else None
        s.add(DiscountCode(code=code, percent=int(percent), max_uses=int(max_uses), used=0, expires=exp))
        s.commit()
        return code
    finally:
        s.close()

def check_discount(uid, code):
    """('ok', درصد) یا (علت, None)."""
    code = (code or '').strip().upper()
    s = get_session()
    try:
        d = s.get(DiscountCode, code)
        if not d:
            return ('invalid', None)
        if d.expires and int(time.time()) > d.expires:
            return ('expired', None)
        if (d.used or 0) >= d.max_uses:
            return ('finished', None)
        if s.get(DiscountUse, (code, uid)):
            return ('already', None)
        return ('ok', int(d.percent))
    finally:
        s.close()

def consume_discount(uid, code):
    """ثبت مصرف کد هنگام ارسال رسید؛ درصد یا ۰ برمی‌گردونه."""
    code = (code or '').strip().upper()
    s = get_session()
    try:
        d = s.query(DiscountCode).filter(DiscountCode.code == code).with_for_update().one_or_none()
        if (not d or (d.expires and int(time.time()) > d.expires) or (d.used or 0) >= d.max_uses
                or s.get(DiscountUse, (code, uid))):
            return 0
        d.used = (d.used or 0) + 1
        s.add(DiscountUse(code=code, user_id=uid))
        s.commit()
        return int(d.percent)
    except Exception:
        s.rollback()
        return 0
    finally:
        s.close()

def release_discount(uid, code):
    if not code:
        return
    s = get_session()
    try:
        d = s.get(DiscountCode, code)
        u = s.get(DiscountUse, (code, uid))
        if d and u:
            d.used = max(0, (d.used or 0) - 1)
            s.delete(u)
            s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()

def stats_text():
    import json as _json
    s = get_session()
    try:
        users = s.query(Wallet).count()
        bal = sum((Decimal(w.balance or 0) for w in s.query(Wallet).all()), Decimal('0'))
        started = s.query(Wallet).filter(Wallet.self_started.is_(True)).count()
        pays = s.query(Payment).all()
        ok = [p for p in pays if p.status == 'approved']
        pend = len([p for p in pays if p.status == 'pending'])
        rev = sum(plan_info(p.plan)[1] * (100 - int(p.discount or 0)) // 100 for p in ok)
        sold = sum(plan_info(p.plan)[0] for p in ok)
        enabled = s.query(selfbot.SelfAccess).filter(selfbot.SelfAccess.enabled.is_(True)).count()
        gifts = s.query(GiftRedeem).count()
        disc_used = s.query(DiscountUse).count()
        labels = {'always_online': 'همیشه آنلاین', 'typing_mode': 'تایپینگ', 'clock_on': 'ساعت زنده', 'ar_on': 'پاسخ خودکار',
                  'del_on': 'حذف/ویرایش', 'ttl_on': 'مدیای تایمردار', 'mn_on': 'منشن‌گیر', 'wl_on': 'ناظر'}
        cnt = {k: 0 for k in labels}
        for r in s.query(selfbot.SelfProfile).all():
            try:
                d = _json.loads(r.data or '{}')
            except Exception:
                continue
            for k in labels:
                if d.get(k):
                    cnt[k] += 1
        slot_users = 0
        for r in s.query(selfbot.SelfAccess).all():
            if any(x.get('word') for x in selfbot.slots_of(r)):
                slot_users += 1
        cnt_txt = '\n'.join(f'• {labels[k]}: {v}' for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]))
        return ('📊 آمار ربات\n\n'
                f'👥 کاربران: {users}\n🔌 سلف روشن (خریداری‌شده): {started}\n✅ دسترسی فعال: {enabled}\n🟢 اتصال زنده الان: {len(selfbot._clients)}\n'
                f'🎮 کاربران با اسلات بازی: {slot_users}\n\n'
                f'💎 مجموع الماس موجود در حساب‌ها: {fmt_bal(bal)}\n'
                f'💰 فروش تأییدشده: {len(ok)} پرداخت | {sold} الماس | {money(rev)} تومان\n⏳ رسید در انتظار: {pend}\n'
                f'🎁 کد هدیه استفاده‌شده: {gifts} | 🏷 کد تخفیف استفاده‌شده: {disc_used}\n\n'
                f'🔥 پرمصرف‌ترین قابلیت‌ها (تعداد کاربر روشن‌کننده):\n{cnt_txt}')
    finally:
        s.close()

def broadcast_ids(audience):
    s = get_session()
    try:
        if audience == 'self':
            return [r.user_id for r in s.query(selfbot.SelfAccess).filter(selfbot.SelfAccess.enabled.is_(True)).all()]
        return [w.user_id for w in s.query(Wallet).all()]
    finally:
        s.close()

def _bc_kb():
    n_all=len(broadcast_ids('all')); n_self=len(broadcast_ids('self'))
    return KB([[B('ارسال به همه کاربران ('+str(n_all)+')',callback_data='adm:bcall')],
               [B('ارسال به کاربران سلف فعال ('+str(n_self)+')',callback_data='adm:bcself')],
               [B('➕ افزودن دکمهٔ لینک',callback_data='adm:bclink')],
               [B('لغو',callback_data='adm:bccancel')]])

async def _broadcast(bot, admin_id, from_chat, msg_id, ids, buttons=None):
    markup = KB([[B(t_, url=u_)] for t_, u_ in (buttons or [])]) if buttons else None
    ok = fail = 0
    for i in ids:
        try:
            await bot.copy_message(chat_id=i, from_chat_id=from_chat, message_id=msg_id, reply_markup=markup)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.06)
    for rid in {admin_id} | set(selfbot.ADMIN_IDS):
        try:
            await bot.send_message(rid, f'📢 ارسال همگانی تموم شد' + ('' if rid == admin_id else f' (فرستنده: {admin_id})') + f'.\n✅ موفق: {ok}\n❌ ناموفق (ربات رو بلاک کرده یا دسترسی نیست): {fail}')
        except Exception:
            pass

_CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

def make_gift(creator, amount, max_uses, hours=None):
    s = get_session()
    try:
        for _ in range(10):
            code = 'RUBI-' + ''.join(secrets.choice(_CODE_CHARS) for _ in range(8))
            if not s.get(GiftCode, code):
                break
        exp = int(time.time()) + int(hours) * 3600 if hours else None
        s.add(GiftCode(code=code, amount=Decimal(str(amount)), max_uses=int(max_uses), used=0, expires=exp, creator=creator))
        s.commit()
        return code
    finally:
        s.close()

def redeem_gift(uid, code):
    """('ok', amount, balance) یا (علت, None, None)."""
    code = (code or '').strip().upper()
    s = get_session()
    try:
        g = s.query(GiftCode).filter(GiftCode.code == code).with_for_update().one_or_none()
        if not g:
            return ('invalid', None, None)
        now = int(time.time())
        if g.expires and now > g.expires:
            return ('expired', None, None)
        if (g.used or 0) >= g.max_uses:
            return ('finished', None, None)
        if s.get(GiftRedeem, (code, uid)):
            return ('already', None, None)
        w = s.get(Wallet, uid)
        if not w:
            w = Wallet(user_id=uid, balance=Decimal('0.0'), last_charge=now)
            s.add(w)
        w.balance = Decimal(w.balance or 0) + Decimal(g.amount)
        g.used = (g.used or 0) + 1
        s.add(GiftRedeem(code=code, user_id=uid))
        s.commit()
        return ('ok', Decimal(g.amount), Decimal(w.balance))
    except Exception:
        s.rollback()
        return ('invalid', None, None)
    finally:
        s.close()

def fmt_bal(x):
    d = Decimal(x or 0).quantize(Decimal('0.1'))
    t = format(d, 'f')
    return t[:-2] if t.endswith('.0') else t

def welcome_text(name, balance):
    lines = [
        f'سلام {name} 👋',
        'به «سلف‌ساز روباهیو» خوش اومدی ⚡️',
        '',
        'اکانت تلگرام خودت رو تبدیل کن به یه دستیار خودکار و حرفه‌ای:',
        '🤖 بازی‌ها رو خودش انجام می‌ده؛ کلمه می‌گه، تایمر می‌ذاره و دکمه می‌زنه',
        '🕒 ساعت زنده کنار اسمت',
        '🪞 کپی پروفایل فقط با یه ریپلای',
        '🟢 همیشه آنلاین  •  ⌨️ تایپینگ  •  🔤 حالت متن  •  🌐 ترجمه  •  🏷 تگ اعضا',
        '🎛 همه‌چیز از یه پنل شیک؛ کافیه توی هر چتی بنویسی «.پنل»',
        '',
        f'💎 سلف با الماس کار می‌کنه: فعال‌سازی {ACTIVATE_COST} الماس، بعدش ساعتی ۲ الماس (روشن) یا ۰٫۲ الماس (خاموش).',
        f'🎁 هدیهٔ شروع: {TRIAL_BONUS} الماس (یک‌بار، بعد از عضویت کانال‌ها).',
        '🔒 بدون الماس سلف فعال نمی‌شه.',
        '',
        f'💰 موجودی تو: {fmt_bal(balance)} الماس',
    ]
    if Decimal(balance or 0) < ACTIVATE_COST:
        lines.append(f'⚠️ برای فعال‌سازی حداقل {ACTIVATE_COST} الماس لازمه؛ از «خرید الماس» شارژ کن یا دوستات رو دعوت کن (هر دعوت ۵۰ الماس برای هر دو نفر).')
    else:
        lines.append('✅ الماس کافی داری؛ «ساخت سلف» رو بزن و شروع کن.')
    lines.append('')
    lines.append('💬 فرستادن «موجودی» توی هر چت، موجودیت رو نشون می‌ده.')
    return '\n'.join(lines)

async def start(update,context):
    u=update.effective_user
    if context.args and context.args[0]=='panel':
        await selfbot.cmd_self(update,context); return
    ref=None
    if context.args:
        try: ref=int(context.args[0].replace('ref',''))
        except: pass
    wallet(u.id,ref)
    if not await is_member(context.bot,u.id):
        await update.effective_message.reply_text('برای استفاده از ربات، اول عضو هر دو کانال شو و بعد «عضو شدم» رو بزن.',reply_markup=join_kb()); return
    until, trial_new = start_trial(u.id) if TRIAL_ENABLED else (None, False)
    awarded = award_referral(u.id)
    if awarded:
        wref = wallet(u.id)['referrer']
        try:
            await context.bot.send_message(u.id, 'با لینک دعوت وارد شدی؛ ۵۰ الماس پاداش گرفتی.')
            if wref:
                await context.bot.send_message(wref, f'{u.first_name} با لینک دعوت تو وارد شد؛ هر دو ۵۰ الماس گرفتید.')
        except Exception:
            pass
    trial_msg = f'\n🎁 {TRIAL_BONUS} الماس هدیهٔ شروع گرفتی (معادل ۵ روز سلف)!' if trial_new else ''
    await update.effective_message.reply_text(welcome_text(u.first_name or '', wallet(u.id)['balance']) + trial_msg, reply_markup=main_reply_kb())
def _span(hours):
    h = int(hours)
    d, h = divmod(h, 24)
    return (f'{d} روز و ' if d else '') + f'{h} ساعت'

def board_text(uid):
    w = wallet(uid)
    bal = Decimal(w['balance'])
    on = bool(w['self_started'])
    rate = Decimal('2.0') if on else Decimal('0.2')
    left = _span(bal / rate)
    gift = (f'✅ دریافت شد ({TRIAL_BONUS} الماس)' if w['trial_used']
            else f'⏳ هنوز دریافت نشده ({TRIAL_BONUS} الماس بعد از عضویت کانال‌ها)')
    return ('📊 داشبورد\n\n'
            f'🆔 آیدی عددی: <code>{uid}</code>\n'
            f'💎 الماس فعلی: {fmt_bal(bal)}\n'
            f'📉 تا الان مصرف شده: {fmt_bal(w["spent"])} الماس\n'
            f'🎁 هدیهٔ شروع: {gift}\n'
            f'🔌 وضعیت سلف: {"روشن ✅" if on else "خاموش ⏹"}\n'
            f'💸 کسر از موجودی: ساعتی {"۲" if on else "۰٫۲"} الماس ({"سلف روشنه" if on else "سلف خاموشه"})\n'
            f'⏳ باقی‌مانده: {fmt_bal(bal)} الماس ≈ {left}\n\n'
            '👆 آیدی رو لمس کن تا کپی بشه.\n'
            'ℹ️ «مصرف‌شده» از نسخهٔ جدید به بعد حساب می‌شه.')

class _Shim:
    """وقتی کاربر دکمهٔ کیبورد پایین چت رو می‌زنه، مثل یه callback رفتار می‌کنه (جواب‌ها پیام جدید می‌شن)."""
    is_shim = True
    def __init__(self, user, message):
        self.from_user = user; self.message = message
    async def answer(self, *a, **k): pass
    async def edit_message_text(self, *a, **k): raise RuntimeError('shim')
    async def edit_message_caption(self, *a, **k): raise RuntimeError('shim')
    async def edit_message_reply_markup(self, *a, **k): pass

_IMG = {'buy': 'banner_buy.jpg', 'activate': 'banner_activate.jpg', 'account': 'banner_account.jpg',
       'board': 'banner_account.jpg', 'free': 'banner_account.jpg', 'help': 'banner_account.jpg'}
_IMG_ID = {}
_MSG_IMG = {}    # (chat_id, message_id) -> کلید عکسی که الان روی اون پیامه؛ برای اینکه بین منوها عکس درست عوض بشه

async def show_panel(q, text, **kwargs):
    m = q.message
    if getattr(m, 'photo', None):        # از یه صفحهٔ عکس‌دار می‌ریم به صفحهٔ متنی
        try: await m.delete()
        except Exception: pass
        await m.chat.send_message(text, **kwargs)
        return
    try:
        await q.edit_message_text(text, **kwargs)
    except Exception:
        await m.reply_text(text, **kwargs)

async def show_photo(q, context, key, caption, reply_markup=None, parse_mode=None):
    """صفحهٔ عکس‌دار؛ اگه همین الان عکس‌داره فقط کپشن/دکمه‌ها عوض می‌شن، وگرنه پیام قبلی پاک و عکس جدید می‌ره."""
    m = q.message
    same_img = _MSG_IMG.get((m.chat_id, m.message_id)) == key
    if getattr(m, 'photo', None) and not getattr(q, 'is_shim', False) and same_img:
        try:
            await q.edit_message_caption(caption=caption[:1024], reply_markup=reply_markup, parse_mode=parse_mode); return
        except Exception as e:  # noqa: BLE001
            if 'not modified' in str(e).lower(): return
    if not getattr(q, 'is_shim', False):
        try: await m.delete()
        except Exception: pass
    await _send_photo(context.bot, m.chat_id, key, caption, reply_markup, parse_mode)

async def _send_photo(bot, chat_id, key, caption, reply_markup=None, parse_mode=None):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _IMG[key])
    src = _IMG_ID.get(key)
    try:
        if not src:
            with open(path, 'rb') as f: src = f.read()
        sm = await bot.send_photo(chat_id, src, caption=caption[:1024], reply_markup=reply_markup, parse_mode=parse_mode)
        if key not in _IMG_ID and sm.photo: _IMG_ID[key] = sm.photo[-1].file_id
        if sm.photo:
            _MSG_IMG[(chat_id, sm.message_id)] = key
            while len(_MSG_IMG) > 3000: _MSG_IMG.pop(next(iter(_MSG_IMG)))
        return sm
    except Exception:
        import logging; logging.getLogger(__name__).exception('banner send failed')
        return await bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=parse_mode)

async def _buy_screen(bot, chat_id, context, n):
    return await _send_photo(bot, chat_id, 'buy', buy_text(_pct(context), n), buy_kb(n))

async def callback(update,context,_q=None,_data=None):
    q=_q or update.callback_query; uid=q.from_user.id; data=_data or q.data or ''; await q.answer()
    if data=='shop:check':
        if not await is_member(context.bot,uid): await show_panel(q, 'هنوز عضویت هر دو کانال تأیید نشده؛ بعد از عضویت دوباره بزن.',reply_markup=join_kb()); return
        until, trial_new = start_trial(uid) if TRIAL_ENABLED else (None, False)
        awarded = award_referral(uid)
        if awarded:
            wref = wallet(uid)['referrer']
            try:
                await context.bot.send_message(uid, 'با لینک دعوت وارد شدی؛ ۵۰ الماس پاداش گرفتی.')
                if wref:
                    await context.bot.send_message(wref, f'{q.from_user.first_name} با لینک دعوت تو وارد شد؛ هر دو ۵۰ الماس گرفتید.')
            except Exception:
                pass
        note = f'\n🎁 {TRIAL_BONUS} الماس هدیهٔ شروع گرفتی (معادل ۵ روز سلف)!' if trial_new else ''
        await show_panel(q, '✅ عضویتت تأیید شد!\n\n' + welcome_text(q.from_user.first_name or '', wallet(uid)['balance']) + note)
        await context.bot.send_message(uid, '👇 منوی اصلی از پایین در دسترسته.', reply_markup=main_reply_kb()); return
    if not await is_member(context.bot,uid):
        await show_panel(q, 'برای ادامه باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
    w=wallet(uid)
    if data=='shop:home': await show_panel(q, 'منوی اصلی',reply_markup=home_kb())
    elif data=='shop:create':
        trial_active = bool(w.get('trial_until') and int(w['trial_until']) > int(time.time()))
        bal = Decimal(w['balance']); started = w['self_started']
        rate = Decimal('2.0') if started else Decimal('0.2')
        left = _span(bal / rate) if bal > 0 else '۰ ساعت'
        low = bal < ACTIVATE_COST and not trial_active
        cap = ('⚡️ <b>فعال‌سازی سلف روباهیو</b>\n\n'
               f'<blockquote>💎 موجودی: <b>{fmt_bal(bal)}</b> الماس\n'
               f'🔌 وضعیت: <b>{"روشن ✅" if started else "خاموش ⏹"}</b>\n'
               f'⏳ شارژ کافی برای: <b>{left}</b></blockquote>\n'
               '💰 <b>هزینه‌ها</b>\n'
               '▫️ فعال‌سازی: <b>۵ الماس</b>\n'
               '▫️ حالت روشن: ساعتی <b>۲ الماس</b>\n'
               '▫️ حالت خاموش: ساعتی <b>۰٫۲ الماس</b>\n\n'
               + ('⚠️ <b>الماس کافی نداری.</b> از «خرید الماس» یا «الماس رایگان» شارژ کن.' if low
                  else 'برای شروع، وارد اکانت خودت شو.'))
        await show_photo(q, context, 'activate', cap, KB([[B('ورود به اکانت',callback_data='self:conn',style='success')],
                         [B('روشن کردن',callback_data='shop:on'),B('خاموش کردن',callback_data='shop:off')],
                         [B('خرید الماس',callback_data='shop:buy'),B('حساب کاربری',callback_data='shop:dash')],
                         [B('منوی اصلی',callback_data='shop:home')]]), parse_mode='HTML')
    elif data=='shop:panel':
        try:
            if not selfbot._allowed(uid):
                await show_panel(q, 'پنل سلف بعد از فعال‌سازی در دسترسته. اول «فعالسازی سلف» رو بزن.', reply_markup=KB([[B('فعالسازی سلف',callback_data='shop:create',style='success')],[B('منوی اصلی',callback_data='shop:home')]]))
            else:
                await selfbot.send_panel(context.bot, uid, q.from_user.first_name or '')
        except Exception:
            pass
    elif data=='shop:helpmenu':
        await show_photo(q, context, 'help', '🆘 راهنما و پشتیبانی\n\nآموزش استفاده، توضیح سلف یا ارتباط با پشتیبانی رو از دکمه‌های زیر انتخاب کن.',
                         reply_markup=KB([[B('آموزش استفاده',callback_data='shop:help'),B('درباره سلف',callback_data='shop:about')],
                                          [B('تیکت پشتیبانی',callback_data='shop:ticket',style='success')],
                                          [B('پشتیبان',url='https://t.me/'+SUPPORT.lstrip('@'))],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:free':
        await show_photo(q, context, 'free', '🎁 الماس رایگان\n\n'
                         '👥 دعوت دوستان: با هر دعوت، ۵۰ الماس برای تو و ۵۰ الماس برای دوستت\n'
                         '🎟 کد هدیه: اگه کد هدیه داری واردش کن\n'
                         f'🎉 هدیهٔ شروع: {TRIAL_BONUS} الماس برای هر کاربر (بعد از عضویت کانال‌ها)',
                         reply_markup=KB([[B('دعوت دوستان',callback_data='shop:invite',style='success')],[B('کد هدیه',callback_data='shop:code')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:on':
        if w['self_started']:
            await show_panel(q, 'سلفت از قبل روشنه. برای دیدن تنظیمات، دکمه‌ی اتصال اکانت یا دستور «پنل» رو استفاده کن.',reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('⏹ خاموش کردن',callback_data='shop:off')],[B('منوی اصلی',callback_data='shop:home')]])); return
        trial_active = bool(w.get('trial_until') and int(w['trial_until']) > int(time.time()))
        if w['balance'] < ACTIVATE_COST and not trial_active:
            await show_panel(q, f'❌ الماس کافی نداری.\n\n💎 موجودی تو: {fmt_bal(w["balance"])} الماس\n🔒 برای فعال‌سازی سلف حداقل {ACTIVATE_COST} الماس لازمه.\nاز «خرید الماس» شارژ کن یا با دعوت دوستان الماس بگیر.',reply_markup=KB([[B('خرید الماس',callback_data='shop:buy')],[B('دعوت دوستان',callback_data='shop:invite')],[B('منوی اصلی',callback_data='shop:home')]])); return
        if not trial_active:
            change(uid,-5); add_spent(uid,5)
        s=get_session()
        try:
            x=s.get(Wallet,uid); x.self_started=True; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        # Enable self access and present existing safe account connection flow
        selfbot.save_row(uid,enabled=True)
        await show_panel(q, ('دورهٔ رایگان فعاله؛ الماسی کسر نشد. حالا شمارهٔ اکانتی که می‌خوای وصل کنی رو با دکمهٔ اتصال اکانت وارد کن. هرگز کد ورودت رو برای هیچ‌کس نفرست.' if trial_active else '۵ الماس کسر شد. حالا شمارهٔ اکانتی که می‌خوای وصل کنی رو با دکمهٔ اتصال اکانت وارد کن. هرگز کد ورودت رو برای هیچ‌کس نفرست.'),reply_markup=KB([[B('🔗 اتصال اکانت',callback_data='self:conn')],[B('👤 حساب کاربری',callback_data='shop:dash')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:off':
        s=get_session()
        try: x=s.get(Wallet,uid); x.self_started=False; x.last_charge=int(time.time()); s.commit()
        finally:s.close()
        selfbot.save_row(uid, enabled=False)
        if uid in selfbot._clients: await selfbot.stop_client(uid,restore=True)
        await show_panel(q, '⏹ سلف خاموش شد. هزینه‌ی حالت خاموش ۰٫۲ الماس در ساعت است.',reply_markup=home_kb())
    elif data=='shop:board':
        await show_photo(q, context, 'board', board_text(uid), parse_mode='HTML', reply_markup=KB([[B('خرید الماس',callback_data='shop:buy',style='success')],[B('حساب کاربری',callback_data='shop:dash')]]+([app_row()] if app_row() else [])+[[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:dash':
        sr = selfbot.get_row(uid)
        phone = (getattr(sr, 'phone', None) or 'ثبت نشده') if sr else 'ثبت نشده'
        bal = Decimal(w['balance'])
        toman = int(bal * Decimal(unit_price()))
        cap = (f'👤 <b>حساب کاربری</b>\n\n🆔 آیدی عددی: <code>{uid}</code>\n'
               f'📱 شماره تلفن: <code>{phone}</code>\n💎 موجودی الماس: <b>{fmt_bal(bal)}</b>\n'
               f'💰 معادل تومانی موجودی الماس: <b>{toman:,}</b> تومان\n'
               f'🔌 وضعیت سلف: <b>{"روشن ✅" if w["self_started"] else "خاموش ⏹"}</b>')
        await show_photo(q, context, 'account', cap, KB([[B('داشبورد',callback_data='shop:board',style='success')],
                         [B('انتقال الماس',callback_data='shop:transfer'),B('خرید الماس',callback_data='shop:buy')],
                         [B('منوی اصلی',callback_data='shop:home')]]), parse_mode='HTML')
    elif data=='shop:about': await show_panel(q, 'سلف ابزاریه که از اکانت تلگرام خودت، طبق تنظیماتی که انجام می‌دی، کارهای خودکار انجام می‌ده. استفاده از اکانت شخصی ریسک محدودیت تلگرام رو داره. اگه نفهمیدی یا کمکی خواستی با پشتیبانی تماس بگیر: @escotch',reply_markup=KB([[B('پشتیبانی',url='https://t.me/escotch')],[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:help': await show_panel(q, 'برای شروع «ساخت سلف» و سپس «روشن کردن» رو بزن. بعد اتصال اکانت رو انجام بده و در چت‌های تلگرام خودت دستور «پنل» رو بنویس. پشتیبانی: @escotch',reply_markup=home_kb())
    elif data=='shop:buy':
        n=_qty(context); await show_photo(q, context, 'buy', buy_text(_pct(context), n), buy_kb(n))
    elif data.startswith('shop:q:'):
        act=data.split(':')[2]; n=_qty(context); old_n=n
        if act=='noop': return
        if act=='manual':
            context.user_data['gift_step']='qty'
            await show_panel(q, f'✏️ تعداد الماس دلخواهت رو بفرست (بین {MIN_QTY} تا {MAX_QTY}). برای لغو بنویس «لغو».'); return
        if act=='ok':
            await _show_pay(q, context, f'c{n}x{unit_price()}'); return
        n = min(MAX_QTY, n + QTY_STEP) if act=='add' else max(MIN_QTY, n - QTY_STEP)
        if n==old_n: return
        context.user_data['buy_n']=n
        await show_photo(q, context, 'buy', buy_text(_pct(context), n), buy_kb(n))
    elif data=='shop:disc':
        context.user_data['gift_step']='disc'; await show_panel(q, '🏷 کد تخفیفت رو بفرست (مثل OFF-XXXXXX). برای لغو بنویس «لغو».')
    elif data.startswith('shop:pay:'):
        await _show_pay(q, context, data.rsplit(':',1)[1])
    elif data=='shop:invite':
        me=await context.bot.get_me(); link=f'https://t.me/{me.username}?start=ref{uid}'
        await show_panel(q, f'🔗 لینک اختصاصی دعوتت:\n{link}\nهر کاربر جدیدی که با لینک تو وارد بشه و عضویت کانال‌هاش تأیید بشه، ۵۰ الماس می‌گیره و برای تو هم ۵۰ الماس ثبت می‌شه.',reply_markup=KB([[B('منوی اصلی',callback_data='shop:home')]]))
    elif data=='shop:ticket':
        context.user_data['tk_step']='new'; context.user_data.pop('tk_id',None)
        await show_panel(q, '🎫 تیکت پشتیبانی\n\nمشکل یا سؤالت رو بفرست؛ متن، عکس، ویس، ویدیو یا فایل، هرچی که لازم داری. برای لغو بنویس «لغو».')
    elif data=='shop:code':
        context.user_data['gift_step']='redeem'; await show_panel(q, '🎁 کد هدیه‌ات رو بفرست (مثل RUBI-XXXXXXXX). برای لغو بنویس «لغو».')
    elif data=='shop:transfer':
        context.user_data['transfer_step']='recipient'; await show_panel(q, 'آیدی عددی گیرنده یا @شناسه کاربری رو بفرست:')
_pay_msgs = {}   # pid -> [(chat_id, message_id)] پیام‌های رسید پیش پشتیبان‌ها

def pay_caption(pid, uid, username, plan, pct, code, claimed_name=None):
    dia, base = plan_info(plan)
    final = base * (100 - int(pct or 0)) // 100
    t = (f'رسید خرید الماس\nکاربر: {uid} (@{username or "ندارد"})\n'
         f'تعداد: {dia} الماس\n'
         f'💳 مبلغ باید پرداخت‌شده باشد: {money(final)} تومان' + (f' (با کد {code} — {pct}٪ تخفیف)' if pct else '') +
         f'\nشناسه پیگیری: {pid}')
    if claimed_name:
        t += f'\n🙋 در حال بررسی: {claimed_name}'
    return t[:1000]

def pay_kb(pid, viewer, claimer, claimer_name):
    ok_row = [B('تأیید و واریز الماس', callback_data=f'pay:approve:{pid}', style='success'), B('رد درخواست', callback_data=f'pay:reject:{pid}', style='danger')]
    is_admin = viewer in selfbot.ADMIN_IDS
    if claimer is None:
        rows = [[B('🙋 من بررسی می‌کنم', callback_data=f'pay:claim:{pid}', style='primary')]]
        if is_admin:
            rows.append(ok_row)
    elif claimer == viewer:
        rows = [ok_row]
    else:
        rows = [[B(f'🔒 {claimer_name or "یک پشتیبان"} در حال بررسی', callback_data=f'pay:info:{pid}', style='primary')]]
        if is_admin:
            rows.append(ok_row)
    return KB(rows)

async def send_receipt(bot, chat, text, file_id, kind, kb):
    try:
        if file_id and kind == 'photo':
            return await bot.send_photo(chat, file_id, caption=text, reply_markup=kb)
        if file_id and kind == 'document':
            return await bot.send_document(chat, file_id, caption=text, reply_markup=kb)
    except Exception:
        pass
    return await bot.send_message(chat, text, reply_markup=kb)

async def _pay_refresh(bot, pid, claimer, claimer_name, done=False):
    for chat, mid in list(_pay_msgs.get(pid, [])):
        try:
            await bot.edit_message_reply_markup(chat_id=chat, message_id=mid, reply_markup=None if done else pay_kb(pid, chat, claimer, claimer_name))
        except Exception:
            pass
    if done:
        _pay_msgs.pop(pid, None)

async def payment_review(update, context):
    q = update.callback_query
    uid = q.from_user.id
    if not has_perm(uid, 'receipts'):
        await q.answer("دسترسی ندارید", show_alert=True)
        return
    parts = (q.data or "").split(":")
    if len(parts) != 3:
        await q.answer(); return
    action, pid = parts[1], parts[2]
    try:
        pid = int(pid)
    except ValueError:
        await q.answer(); return
    s = get_session()
    try:
        p = s.query(Payment).filter(Payment.id == pid).with_for_update().one_or_none()
        if not p:
            await q.answer()
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text("درخواست پرداخت پیدا نشد.")
            return
        if p.status != "pending":
            await q.answer("این درخواست قبلاً بررسی شده است.", show_alert=True)
            try: await q.edit_message_reply_markup(reply_markup=None)
            except Exception: pass
            return
        who = q.from_user.first_name or ('@' + (q.from_user.username or str(uid)))
        if action == "info":
            await q.answer(f"{p.claimed_name or 'یک پشتیبان'} این رسید رو برداشته؛ تا اون تموم نکنه نمی‌تونی تأییدش کنی.", show_alert=True)
            return
        if action == "claim":
            if p.claimed_by and p.claimed_by != uid:
                await q.answer(f"قبلاً {p.claimed_name} این رسید رو برداشته.", show_alert=True)
                return
            p.claimed_by = uid; p.claimed_name = who[:60]
            s.commit()
            await q.answer("✅ رسید به نام تو ثبت شد.")
            _pay_msgs.setdefault(pid, [])
            if (q.message.chat_id, q.message.message_id) not in _pay_msgs[pid]:
                _pay_msgs[pid].append((q.message.chat_id, q.message.message_id))
            await _pay_refresh(context.bot, pid, uid, who[:60])
            return
        # تأیید/رد: فقط کسی که برداشته (یا ادمین)
        if uid not in selfbot.ADMIN_IDS and p.claimed_by != uid:
            await q.answer("اول دکمهٔ «من بررسی می‌کنم» رو بزن (و اگه کس دیگه‌ای برداشته، اون باید تموم کنه).", show_alert=True)
            return
        await q.answer()
        if action == "approve":
            amount = Decimal(plan_info(p.plan)[0])
            if amount <= 0:
                await q.message.reply_text("پلن این رسید شناخته‌شده نیست؛ دستی بررسی کن."); return
            w = s.get(Wallet, p.user_id)
            if not w:
                w = Wallet(user_id=p.user_id, balance=Decimal("0"), last_charge=int(time.time()))
                s.add(w)
            w.balance = Decimal(w.balance or 0) + amount
            p.status = "approved"
            if not p.claimed_by:
                p.claimed_by = uid; p.claimed_name = who[:60]
            s.commit()
            await context.bot.send_message(p.user_id, f"پرداخت تأیید شد؛ {amount} الماس به حسابت اضافه شد.")
            await _pay_refresh(context.bot, pid, uid, who, done=True)
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text(f"تأیید شد. {amount} الماس به کاربر {p.user_id} اضافه شد.")
        elif action == "reject":
            p.status = "rejected"
            if not p.claimed_by:
                p.claimed_by = uid; p.claimed_name = who[:60]
            s.commit()
            try: release_discount(p.user_id, p.code)
            except Exception: pass
            await context.bot.send_message(p.user_id, "درخواست خرید الماس رد شد. برای بررسی بیشتر از «تیکت پشتیبانی» استفاده کن.")
            await _pay_refresh(context.bot, pid, uid, who, done=True)
            await q.edit_message_reply_markup(reply_markup=None)
            await q.message.reply_text(f"درخواست #{pid} رد شد.")
    finally:
        s.close()

def _tk_kb(tid):
    return KB([[B('💬 پاسخ', callback_data=f'sup:treply:{tid}', style='primary'), B('✅ بستن تیکت', callback_data=f'sup:tclose:{tid}', style='danger')]])

async def ticket_submit(m, context, u):
    """پیام کاربر → تیکت (جدید یا ادامهٔ تیکت باز) → کپی برای همهٔ پشتیبان‌ها."""
    ud = context.user_data
    tid = ud.get('tk_id')
    new = False
    s = get_session()
    try:
        t = s.get(Ticket, tid) if tid else None
        if not t or t.status != 'open' or t.user_id != u.id:
            t = s.query(Ticket).filter(Ticket.user_id == u.id, Ticket.status == 'open').order_by(Ticket.id.desc()).first()
        if not t:
            t = Ticket(user_id=u.id, status='open', subject=((m.text or m.caption or '')[:200] or None)); s.add(t); new = True
        t.last = int(time.time()); s.commit(); tid = t.id
    finally:
        s.close()
    ud.pop('tk_step', None); ud.pop('tk_id', None)
    head = (f'🎫 تیکت #{tid} | ' + ('جدید' if new else 'پیام جدید') + f'\nکاربر: {u.id} (@{u.username or "ندارد"}) {u.first_name or ""}')
    for staff in staff_ids('tickets'):
        try:
            hm = await context.bot.send_message(staff, head, reply_markup=_tk_kb(tid))
            await context.bot.copy_message(staff, m.chat_id, m.message_id, reply_to_message_id=hm.message_id)
        except Exception:
            pass
    await m.reply_text(f'✅ پیامت برای پشتیبانی رفت (تیکت #{tid}). جواب همین‌جا برات می‌آد.', reply_markup=home_kb())

async def ticket_reply(m, context, tid):
    s = get_session()
    try:
        t = s.get(Ticket, tid)
        if not t:
            await m.reply_text('تیکت پیدا نشد.'); return
        if t.status != 'open':
            await m.reply_text('این تیکت بسته شده.'); return
        user_id = t.user_id; t.last = int(time.time()); s.commit()
    finally:
        s.close()
    try:
        await context.bot.send_message(user_id, f'💬 پاسخ پشتیبانی (تیکت #{tid}):')
        await context.bot.copy_message(user_id, m.chat_id, m.message_id, reply_markup=KB([[B('✍️ ادامه گفتگو', callback_data=f'tk:more:{tid}', style='primary')]]))
    except Exception:
        await m.reply_text('❌ ارسال نشد؛ احتمالاً کاربر ربات رو بلاک کرده.'); return
    await m.reply_text(f'✅ پاسخ برای تیکت #{tid} ارسال شد.', reply_markup=_tk_kb(tid))

async def tk_cb(update, context):
    q = update.callback_query
    parts = (q.data or '').split(':')
    await q.answer()
    if len(parts) < 3 or parts[1] != 'more':
        return
    try:
        tid = int(parts[2])
    except ValueError:
        return
    s = get_session()
    try:
        t = s.get(Ticket, tid)
        ok = bool(t and t.user_id == q.from_user.id and t.status == 'open')
    finally:
        s.close()
    if not ok:
        await q.message.reply_text('این تیکت بسته شده؛ از «تیکت پشتیبانی» توی منو یه تیکت جدید بساز.'); return
    context.user_data['tk_step'] = 'more'; context.user_data['tk_id'] = tid
    await q.message.reply_text(f'✍️ پیامت برای تیکت #{tid} رو بفرست (متن، عکس، ویس، ویدیو یا فایل). برای لغو بنویس «لغو».')

def is_staff(uid):
    return bool(perms_of(uid))

def sup_kb(uid=None):
    p = perms_of(uid) if uid is not None else set(PERM_KEYS)
    rows = []
    if 'receipts' in p: rows.append([B('رسیدهای در انتظار', callback_data='sup:pending')])
    if 'tickets' in p: rows.append([B('تیکت‌های باز', callback_data='sup:tickets')])
    if 'lookup' in p: rows.append([B('بررسی کاربر', callback_data='sup:lookup')])
    if 'balance' in p: rows.append([B('تغییر الماس کاربر', callback_data='sup:balask')])
    if 'gift' in p: rows.append([B('ساخت کد هدیه', callback_data='sup:gnew'), B('کدهای هدیه', callback_data='sup:glist')])
    if 'discount' in p: rows.append([B('ساخت کد تخفیف', callback_data='sup:dnew'), B('کدهای تخفیف', callback_data='sup:dlist')])
    if 'price' in p: rows.append([B('قیمت الماس', callback_data='sup:price')])
    if 'staff' in p: rows.append([B('مدیریت پشتیبان‌ها', callback_data='sup:staff')])
    if 'memehelp' in p: rows.append([B('دستورات میم', callback_data='sup:memehelp')])
    if 'broadcast' in p: rows.append([B('پیام همگانی', callback_data='adm:bc')])
    rows.append([B('بستن', callback_data='sup:close')])
    return KB(rows)

def lookup_kb(target, viewer=None):
    rows = []
    if viewer is None or has_perm(viewer, 'balance'):
        rows.append([B('افزودن الماس', callback_data=f'sup:bal:add:{target}', style='success'),
                     B('کم کردن الماس', callback_data=f'sup:bal:sub:{target}', style='danger')])
    if viewer is None or has_perm(viewer, 'lookup'):
        for kind, f in selfbot.FEATS.items():
            on = selfbot.feat_get(target, kind)['allowed']
            lb = f['label']
            rows.append([B(f'غیرفعال‌کردن «{lb}» برای کاربر' if on else f'فعال‌کردن «{lb}» برای کاربر',
                           callback_data=f"sup:feat:{kind}:{'off' if on else 'on'}:{target}")])
    return KB(rows + [list(r) for r in sup_kb(viewer).inline_keyboard])

PANEL_TEXT = ('🛟 پنل پشتیبانی\n\nدکمه‌های زیر بر اساس دسترسی‌هایی که داری نشون داده می‌شن:\n'
              '📥 رسیدها • 🎫 تیکت‌ها • 🔎 بررسی کاربر • 💎 تغییر الماس کاربر\n'
              '🎁 کد هدیه • 🏷 کد تخفیف • 💰 قیمت الماس • 👥 مدیریت پشتیبان‌ها\n'
              '😂 دستورات میم • 📢 پیام همگانی')

async def support_panel(update, context):
    u = update.effective_user
    if not u or not is_staff(u.id) or update.effective_chat.type != 'private':
        return
    context.user_data.pop('sup_step', None)
    await update.effective_message.reply_text(PANEL_TEXT, reply_markup=sup_kb(u.id))

SUP_NEED = {'pending': 'receipts', 'tickets': 'tickets', 'treply': 'tickets', 'tclose': 'tickets', 'feat': 'lookup', 'lookup': 'lookup',
            'memehelp': 'memehelp', 'bal': 'balance', 'balask': 'balance', 'gnew': 'gift', 'glist': 'gift', 'dnew': 'discount',
            'dlist': 'discount', 'price': 'price', 'staff': 'staff', 'sadd': 'staff', 'sedit': 'staff', 'sp': 'staff', 'sdel': 'staff'}

def _perm_kb(target, viewer):
    cur = perms_of(target); mine = perms_of(viewer)
    rows = []
    for k, lb in PERMS:
        rows.append([B(f'{lb}: ' + ('روشن' if k in cur else 'خاموش'), callback_data=f'sup:sp:{target}:{k}',
                       style='success' if k in cur else 'danger')])
    rows.append([B('حذف پشتیبان', callback_data=f'sup:sdel:{target}', style='danger')])
    rows.append([B('بازگشت', callback_data='sup:staff', style='primary')])
    return KB(rows)

def _staff_text():
    st = all_staff()
    lines = [f'• {u}: ' + (('، '.join(lb for k, lb in PERMS if k in p)) or 'بدون دسترسی') for u, p in st.items()]
    return '👥 مدیریت پشتیبان‌ها\n\n' + ('\n'.join(lines) if lines else 'هنوز پشتیبانی اضافه نشده.') + \
        '\n\nبرای تغییر دسترسی روی هر نفر بزن. فقط دسترسی‌هایی رو می‌تونی بدی که خودت داری.'

def _staff_kb():
    rows = [[B(str(u), callback_data=f'sup:sedit:{u}')] for u in all_staff()]
    rows.append([B('افزودن پشتیبان', callback_data='sup:sadd', style='success')])
    rows.append([B('بازگشت', callback_data='sup:home', style='primary')])
    return KB(rows)

def _gift_list_text():
    s = get_session()
    try:
        rows = s.query(GiftCode).order_by(GiftCode.created.desc()).limit(15).all()
        lines = [f'{g.code} | 💎 {fmt_bal(g.amount)} | {g.used}/{g.max_uses}' + (' | ⌛' if g.expires and g.expires < int(time.time()) else '') for g in rows]
    finally:
        s.close()
    return '🎟 آخرین کدهای هدیه (کد | الماس | استفاده):\n\n' + ('\n'.join(lines) if lines else 'هنوز کدی ساخته نشده.')

def _disc_list_text():
    s = get_session()
    try:
        rows = s.query(DiscountCode).order_by(DiscountCode.created.desc()).limit(15).all()
        lines = [f'{d.code} | {d.percent}٪ | {d.used}/{d.max_uses}' + (' | ⌛' if d.expires and d.expires < int(time.time()) else '') for d in rows]
    finally:
        s.close()
    return '🏷 آخرین کدهای تخفیف (کد | درصد | استفاده):\n\n' + ('\n'.join(lines) if lines else 'هنوز کدی ساخته نشده.')

async def sup_cb(update, context):
    q = update.callback_query
    if not is_staff(q.from_user.id):
        await q.answer('این پنل فقط برای پشتیبانیه.', show_alert=True); return
    act = (q.data or '').split(':')[1]
    need = SUP_NEED.get(act)
    if need and not has_perm(q.from_user.id, need):
        await q.answer('این بخش برای تو فعال نیست.', show_alert=True); return
    await q.answer()
    uid_ = q.from_user.id
    if act == 'pending':
        s = get_session()
        try:
            rows = s.query(Payment).filter(Payment.status == 'pending').order_by(Payment.id.asc()).limit(10).all()
            total = s.query(Payment).filter(Payment.status == 'pending').count()
            items = [(p.id, p.user_id, p.plan, int(p.discount or 0), p.code, p.file_id, p.file_kind, p.claimed_by, p.claimed_name) for p in rows]
        finally:
            s.close()
        if not items:
            await q.message.reply_text('✅ رسید در انتظاری نیست.'); return
        chat = q.message.chat_id
        for pid, uid_, plan, pc, code, fid, fkind, cb_, cn in items:
            cap = pay_caption(pid, uid_, None, plan, pc, code, cn)
            sm = await send_receipt(context.bot, chat, cap, fid, fkind, pay_kb(pid, q.from_user.id, cb_, cn))
            lst = _pay_msgs.setdefault(pid, [])
            if (chat, sm.message_id) not in lst:
                lst.append((chat, sm.message_id))
        if total > len(items):
            await q.message.reply_text(f'و {total - len(items)} رسید دیگه هم در انتظاره؛ بعد از بررسی این‌ها دوباره بزن.')
    elif act == 'tickets':
        s = get_session()
        try:
            rows = s.query(Ticket).filter(Ticket.status == 'open').order_by(Ticket.last.asc()).limit(10).all()
            total = s.query(Ticket).filter(Ticket.status == 'open').count()
            items = [(t.id, t.user_id, t.subject) for t in rows]
        finally:
            s.close()
        if not items:
            await q.message.reply_text('✅ تیکت بازی نیست.'); return
        for tid, uid_, subj in items:
            await q.message.reply_text(f'🎫 تیکت #{tid} | کاربر {uid_}\n💬 {subj or "[رسانه]"}', reply_markup=_tk_kb(tid))
        if total > len(items):
            await q.message.reply_text(f'و {total - len(items)} تیکت باز دیگه هم هست.')
    elif act == 'treply':
        context.user_data['sup_step'] = 'treply:' + (q.data.split(':')[2] if len(q.data.split(':')) > 2 else '0')
        await q.message.reply_text('💬 پاسخت رو بفرست؛ متن (با فرمت تلگرام)، عکس، ویس، ویدیو، فایل… هرچی بفرستی همون‌طور برای کاربر کپی می‌شه. برای لغو بنویس «لغو».')
    elif act == 'tclose':
        try:
            tid = int(q.data.split(':')[2])
        except Exception:
            return
        s = get_session()
        try:
            t = s.get(Ticket, tid)
            if not t or t.status != 'open':
                await q.message.reply_text('این تیکت قبلاً بسته شده.'); return
            t.status = 'closed'; s.commit(); user_id = t.user_id
        finally:
            s.close()
        try:
            await context.bot.send_message(user_id, f'✅ تیکت #{tid} بسته شد. اگه مشکلت ادامه داشت از «تیکت پشتیبانی» یه تیکت جدید بساز.')
        except Exception:
            pass
        try: await q.edit_message_reply_markup(reply_markup=None)
        except Exception: pass
        await q.message.reply_text(f'✅ تیکت #{tid} بسته شد.')
    elif act == 'feat':
        parts = (q.data or '').split(':')
        try:
            kind, mode, target = parts[2], parts[3], int(parts[4])
            lb = selfbot.FEATS[kind]['label']
        except Exception:
            return
        selfbot.feat_set_allowed(target, kind, mode == 'on')
        await q.message.reply_text(f"{'✅' if mode == 'on' else '⏹'} «{lb}» برای {target} {'فعال شد.' if mode == 'on' else 'غیرفعال شد.'}",
                                   reply_markup=lookup_kb(target, uid_))
        if mode == 'on':
            try:
                await context.bot.send_message(target, f'✅ قابلیت «{lb}» برات فعال شد؛ از «تنظیم بازی» ← «{lb}» می‌تونی ببینیش.')
            except Exception:
                pass
    elif act == 'lookup':
        context.user_data['sup_step'] = 'lookup'
        await q.message.reply_text('🔎 آیدی عددی کاربر رو بفرست. برای لغو بنویس «لغو».')
    elif act == 'memehelp':
        import selfmodes
        context.user_data['sup_step'] = 'memehelp'
        await q.message.reply_text('😂 متنی که الان کاربر توی دکمهٔ «میم» پنل سلف می‌بینه:\n\n' + selfmodes.meme_help()
                                   + f'\n\n— — —\nمتن جدید رو بفرست (حداکثر {selfmodes.MEME_HELP_MAX} حرف؛ دستورها رو همین‌جا بنویس).\n'
                                   'برگشت به متن پیش‌فرض: «پیش‌فرض» | لغو: «لغو»')
    elif act == 'home':
        try:
            await q.edit_message_text(PANEL_TEXT, reply_markup=sup_kb(uid_))
        except Exception:
            await q.message.reply_text(PANEL_TEXT, reply_markup=sup_kb(uid_))
    elif act == 'balask':
        context.user_data['sup_step'] = 'lookup'
        await q.message.reply_text('💎 آیدی عددی کاربر رو بفرست؛ بعدش می‌تونی الماسش رو زیاد یا کم کنی. برای لغو بنویس «لغو».')
    elif act == 'bal':
        parts = (q.data or '').split(':')
        try:
            op, target = parts[2], int(parts[3])
            assert op in ('add', 'sub')
        except Exception:
            return
        context.user_data['sup_step'] = f'balamt:{op}:{target}'
        await q.message.reply_text(f'{"➕ افزودن" if op == "add" else "➖ کم کردن"} الماس برای کاربر {target}\n\nمقدار الماس رو بفرست (مثلاً 100 یا 12.5). برای لغو بنویس «لغو».')
    elif act == 'gnew':
        context.user_data['gift_step'] = 'admin_new'
        await q.message.reply_text('🎁 ساخت کد هدیه\n\nمقدار الماس و تعداد دفعات استفاده رو بفرست:\n100 5  ← ۱۰۰ الماس برای ۵ نفر\n100 5 48  ← با اعتبار ۴۸ ساعته\nبرای لغو بنویس «لغو».')
    elif act == 'glist':
        await q.message.reply_text(_gift_list_text())
    elif act == 'dnew':
        context.user_data['gift_step'] = 'disc_new'
        await q.message.reply_text('🏷 ساخت کد تخفیف\n\nدرصد و تعداد دفعات استفاده رو بفرست:\n20 10  ← ۲۰٪ تخفیف برای ۱۰ نفر\n20 10 72  ← با اعتبار ۷۲ ساعته\nبرای لغو بنویس «لغو».')
    elif act == 'dlist':
        await q.message.reply_text(_disc_list_text())
    elif act == 'price':
        context.user_data['sup_step'] = 'price'
        await q.message.reply_text(f'💰 قیمت فعلی هر الماس: {money(unit_price())} تومان\n(مثلاً ۱۰۰۰ الماس = {money(unit_price() * 1000)} تومان)\n\nقیمت جدید هر الماس رو به تومان بفرست (فقط عدد). برای لغو بنویس «لغو».')
    elif act == 'staff':
        context.user_data.pop('sup_step', None)
        try:
            await q.edit_message_text(_staff_text(), reply_markup=_staff_kb())
        except Exception:
            await q.message.reply_text(_staff_text(), reply_markup=_staff_kb())
    elif act == 'sadd':
        context.user_data['sup_step'] = 'staffid'
        await q.message.reply_text('👥 آیدی عددی کسی که می‌خوای پشتیبان بشه رو بفرست (باید ربات رو استارت کرده باشه). برای لغو بنویس «لغو».')
    elif act == 'sedit':
        try:
            target = int((q.data or '').split(':')[2])
        except Exception:
            return
        if target in selfbot.ADMIN_IDS:
            await q.answer('ادمین همهٔ دسترسی‌ها رو داره.', show_alert=True); return
        try:
            await q.edit_message_text(f'👤 دسترسی‌های {target}', reply_markup=_perm_kb(target, uid_))
        except Exception:
            await q.message.reply_text(f'👤 دسترسی‌های {target}', reply_markup=_perm_kb(target, uid_))
    elif act == 'sp':
        parts = (q.data or '').split(':')
        try:
            target, key = int(parts[2]), parts[3]
            assert key in PERM_KEYS
        except Exception:
            return
        if target in selfbot.ADMIN_IDS or target == uid_ and uid_ not in selfbot.ADMIN_IDS:
            await q.answer('این کاربر رو نمی‌تونی تغییر بدی.', show_alert=True); return
        if key not in perms_of(uid_):
            await q.answer('این دسترسی رو خودت نداری، پس نمی‌تونی بدی.', show_alert=True); return
        cur = perms_of(target)
        cur ^= {key}
        save_perms(target, cur, uid_)
        try:
            await q.edit_message_reply_markup(reply_markup=_perm_kb(target, uid_))
        except Exception:
            pass
        lb = dict(PERMS)[key]
        await _notify_admins(context, f'👥 {uid_} دسترسی «{lb}» رو برای {target} {"روشن" if key in cur else "خاموش"} کرد.', exclude=uid_)
    elif act == 'sdel':
        try:
            target = int((q.data or '').split(':')[2])
        except Exception:
            return
        if target in selfbot.ADMIN_IDS or target == uid_:
            await q.answer('این کاربر رو نمی‌تونی حذف کنی.', show_alert=True); return
        if target in selfbot.SUPPORT_IDS:
            save_perms(target, set(), uid_)     # از لیست env حذف نمی‌شه؛ همهٔ دسترسی‌هاش برداشته می‌شه
        else:
            s_ = get_session()
            try:
                r_ = s_.get(StaffMember, target)
                if r_:
                    s_.delete(r_); s_.commit()
            finally:
                s_.close()
        await _notify_admins(context, f'👥 {uid_} پشتیبان {target} رو حذف کرد.', exclude=uid_)
        try:
            await q.edit_message_text(_staff_text(), reply_markup=_staff_kb())
        except Exception:
            pass
    elif act == 'close':
        try:
            await q.message.delete()
        except Exception:
            pass

def user_info_text(uid):
    s = get_session()
    try:
        w = s.get(Wallet, uid)
        if not w:
            return f'کاربر {uid} توی ربات حساب ندارد.'
        row = selfbot.get_row(uid)
        pays = s.query(Payment).filter(Payment.user_id == uid).order_by(Payment.id.desc()).limit(3).all()
        stt = {'pending': '⏳ در انتظار', 'approved': '✅ تأیید', 'rejected': '❌ رد'}
        ptxt = '\n'.join(f'#{p.id} | پلن {p.plan} | {stt.get(p.status, p.status)}' for p in pays) or 'پرداختی ندارد.'
        return (f'👤 کاربر {uid}\n💎 موجودی: {fmt_bal(w.balance)} الماس\n🔌 سلف: {"روشن" if w.self_started else "خاموش"}'
                f'\n🔑 دسترسی: {"فعال" if row and row.enabled else "غیرفعال"} | اتصال: {"زنده" if uid in selfbot._clients else "—"}'
                f'\n🛠 کار: {"مجاز" if selfbot.feat_get(uid, "work")["allowed"] else "غیرمجاز"} | 📚 درس: {"مجاز" if selfbot.feat_get(uid, "lesson")["allowed"] else "غیرمجاز"}'
                f'\n\n🧾 آخرین پرداخت‌ها:\n{ptxt}')
    finally:
        s.close()

async def incoming(update,context):
    m=update.effective_message; u=update.effective_user
    if not m or not u or update.effective_chat.type!='private': return
    if m.text and m.text.strip() in MENU_TEXT:
        for k_ in ('gift_step','tk_step','tk_id','transfer_step','transfer_target','payment_plan','sup_step','self_st'):
            context.user_data.pop(k_, None)
        if not await is_member(context.bot,u.id):
            await m.reply_text('برای استفاده از ربات، اول عضو هر دو کانال شو و بعد «عضو شدم» رو بزن.',reply_markup=join_kb())
        else:
            await callback(update, context, _q=_Shim(u, m), _data=MENU_DATA[MENU_TEXT[m.text.strip()]])
        raise ApplicationHandlerStop
    sst=context.user_data.get('sup_step') or ''
    if sst.startswith('treply:') and is_staff(u.id):
        if m.text and m.text.strip()=='لغو':
            context.user_data.pop('sup_step',None); await m.reply_text('لغو شد.'); return
        context.user_data.pop('sup_step',None)
        try: tid=int(sst.split(':')[1])
        except Exception: return
        await ticket_reply(m,context,tid); return
    if sst == 'memehelp' and is_staff(u.id) and m.text:
        import selfmodes
        t = m.text.strip()
        if t == 'لغو':
            context.user_data.pop('sup_step', None); await m.reply_text('لغو شد.'); return
        if len(t) > selfmodes.MEME_HELP_MAX:
            await m.reply_text(f'❗️ متن {len(t)} حرفه؛ حداکثر {selfmodes.MEME_HELP_MAX} حرف. کوتاه‌ترش کن و دوباره بفرست (یا «لغو»).'); return
        context.user_data.pop('sup_step', None)
        if t == 'پیش‌فرض' or t == 'پیشفرض':
            selfmodes.set_meme_help(None); await m.reply_text('✅ متن میم به پیش‌فرض برگشت.'); return
        selfmodes.set_meme_help(t)
        await m.reply_text('✅ ذخیره شد. کاربرها از این به بعد این متن رو توی دکمهٔ «میم» می‌بینن:\n\n' + t); return
    if context.user_data.get('gift_step')=='bc_link' and has_perm(u.id,'broadcast') and m.text:
        t=m.text.strip(); context.user_data.pop('gift_step',None)
        btns=[]
        if t!='لغو':
            for ln in t.split('\n')[:3]:
                if '|' in ln:
                    a_,b_=ln.split('|',1); a_=a_.strip()[:40]; b_=b_.strip()
                    if a_ and b_.startswith(('http://','https://','tg://','https://t.me/')): btns.append((a_,b_))
        if btns:
            context.user_data['bc_btns']=btns
            await m.reply_text('✅ '+str(len(btns))+' دکمهٔ لینک اضافه شد.',reply_markup=_bc_kb())
        else:
            await m.reply_text('دکمه‌ای اضافه نشد (فرمت: متن دکمه | لینک).',reply_markup=_bc_kb())
        return
    if context.user_data.get('sup_step')=='lookup' and is_staff(u.id) and m.text:
        t=selfbot.fa2en(m.text).strip()
        context.user_data.pop('sup_step',None)
        if t=='لغو': await m.reply_text('لغو شد.'); return
        if not t.isdigit(): await m.reply_text('❗️ فقط آیدی عددی بفرست.'); context.user_data['sup_step']='lookup'; return
        await m.reply_text(user_info_text(int(t)),reply_markup=lookup_kb(int(t),u.id)); return
    if sst.startswith('balamt:') and has_perm(u.id,'balance') and m.text:
        t=selfbot.fa2en(m.text).strip().replace(',','.')
        if t=='لغو':
            context.user_data.pop('sup_step',None); await m.reply_text('لغو شد.'); return
        try:
            _,op,tid=sst.split(':'); tid=int(tid); amt=Decimal(t)
            if amt<=0 or amt>1000000 or amt*10!=(amt*10).to_integral_value(): raise ValueError
        except Exception:
            await m.reply_text('❗️ مقدار نامعتبره. یه عدد مثبت بفرست (حداکثر یک رقم اعشار) یا «لغو».'); return
        context.user_data.pop('sup_step',None)
        old,new=adjust_balance(tid, amt if op=='add' else -amt)
        real=abs(new-old)
        word='اضافه' if op=='add' else 'کم'
        await m.reply_text(f'✅ {fmt_bal(real)} الماس {word} شد.\n💎 موجودی کاربر {tid}: {fmt_bal(old)} ← {fmt_bal(new)}',reply_markup=lookup_kb(tid,u.id))
        try: await context.bot.send_message(tid, f'💎 {fmt_bal(real)} الماس توسط پشتیبانی {"به حسابت اضافه" if op=="add" else "از حسابت کم"} شد.\n💰 موجودی فعلی: {fmt_bal(new)} الماس')
        except Exception: pass
        await _notify_admins(context, f'💎 {u.id} موجودی کاربر {tid} رو {fmt_bal(old)} ← {fmt_bal(new)} کرد ({"+" if op=="add" else "-"}{fmt_bal(real)}).', exclude=u.id); return
    if sst=='price' and has_perm(u.id,'price') and m.text:
        t=selfbot.fa2en(m.text).strip().replace(',','').replace('٬','')
        if t=='لغو':
            context.user_data.pop('sup_step',None); await m.reply_text('لغو شد.'); return
        if not t.isdigit() or not 1<=int(t)<=1000000:
            await m.reply_text('❗️ فقط یه عدد صحیح به تومان بفرست (یا «لغو»).'); return
        old=unit_price(); set_setting('unit_price',int(t)); context.user_data.pop('sup_step',None)
        await m.reply_text(f'✅ قیمت هر الماس شد {money(int(t))} تومان (قبلی: {money(old)}).\n۱۰۰۰ الماس = {money(int(t)*1000)} تومان\nسفارش‌هایی که قبلاً با قیمت قبلی ثبت شدن عوض نمی‌شن.',reply_markup=sup_kb(u.id))
        await _notify_admins(context, f'💰 {u.id} قیمت هر الماس رو از {money(old)} به {money(int(t))} تومان تغییر داد.', exclude=u.id); return
    if sst=='staffid' and has_perm(u.id,'staff') and m.text:
        t=selfbot.fa2en(m.text).strip()
        if t=='لغو':
            context.user_data.pop('sup_step',None); await m.reply_text('لغو شد.'); return
        if not t.isdigit():
            await m.reply_text('❗️ فقط آیدی عددی بفرست (یا «لغو»).'); return
        tid=int(t); context.user_data.pop('sup_step',None)
        if tid in selfbot.ADMIN_IDS:
            await m.reply_text('این کاربر ادمینه و همهٔ دسترسی‌ها رو داره.'); return
        if _staff_row(tid) is None and tid not in selfbot.SUPPORT_IDS:
            save_perms(tid, NEW_STAFF_PERMS & perms_of(u.id), u.id)
            try: await context.bot.send_message(tid,'👥 به پنل پشتیبانی اضافه شدی. برای باز کردنش بنویس /support')
            except Exception: pass
            await _notify_admins(context, f'👥 {u.id} کاربر {tid} رو به پشتیبان‌ها اضافه کرد.', exclude=u.id)
        await m.reply_text(f'👤 دسترسی‌های {tid} رو انتخاب کن:',reply_markup=_perm_kb(tid,u.id)); return
    if context.user_data.get('gift_step')=='admin_new' and has_perm(u.id,'gift') and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.'); return
        await _gift_create(m, context, t); return
    gs=context.user_data.get('gift_step')
    if gs=='disc_new' and has_perm(u.id,'discount') and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.'); return
        parts=t.replace(',',' ').split()
        try:
            pc=int(parts[0]); uses=int(parts[1]) if len(parts)>1 else 1; hrs=int(parts[2]) if len(parts)>2 else None
            if not 1<=pc<=90 or not 1<=uses<=10000: raise ValueError
        except Exception:
            await m.reply_text('فرمت درست نیست. مثال: 20 10  (۲۰٪ تخفیف، ۱۰ نفر) یا 20 10 72 (با اعتبار ۷۲ ساعته). برای لغو بنویس «لغو».'); return
        code=make_discount(pc,uses,hrs); context.user_data.pop('gift_step',None)
        await m.reply_text(f'🏷 کد تخفیف ساخته شد:\n\n{code}\n\n{pc}٪ تخفیف | 👥 {uses} بار استفاده'+(f' | ⏳ {hrs} ساعت' if hrs else '')+'\nکاربر از «خرید الماس» ← «کد تخفیف» واردش می‌کنه.'); return
    if gs=='bc_wait' and has_perm(u.id,'broadcast'):
        if m.text and m.text.strip()=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.'); return
        context.user_data.pop('gift_step',None)
        context.user_data['bc_msg']=(m.chat_id,m.message_id); context.user_data.pop('bc_btns',None)
        await m.reply_text('👆 این پیام برای ارسال همگانی آماده‌ست؛ همون‌طور که هست (با فرمت و مدیا) کپی می‌شه. به کی بفرستم؟',reply_to_message_id=m.message_id,reply_markup=_bc_kb()); return
    if context.user_data.get('tk_step') in ('new','more'):
        if m.text and m.text.strip()=='لغو':
            context.user_data.pop('tk_step',None); context.user_data.pop('tk_id',None); await m.reply_text('لغو شد.',reply_markup=home_kb()); return
        await ticket_submit(m,context,u); return
    if not (m.text or m.photo or m.document): return
    if not await is_member(context.bot,u.id):
        await m.reply_text('برای استفاده باید عضو هر دو کانال باشی.',reply_markup=join_kb()); return
    if gs=='qty' and m.text:
        t=selfbot.fa2en(m.text).strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); n=_qty(context)
            await _buy_screen(context.bot, m.chat_id, context, n); return
        if not t.isdigit() or not MIN_QTY<=int(t)<=MAX_QTY:
            await m.reply_text(f'❗️ یه عدد بین {MIN_QTY} تا {MAX_QTY} بفرست (یا «لغو»).'); return
        n=int(t); context.user_data['buy_n']=n; context.user_data.pop('gift_step',None)
        await _buy_screen(context.bot, m.chat_id, context, n); return
    if gs=='disc' and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.',reply_markup=home_kb()); return
        res,pc=check_discount(u.id,t)
        if res=='ok':
            context.user_data.pop('gift_step',None); context.user_data['disc']={'code':t.upper(),'pct':pc}
            await m.reply_text(f'✅ کد تخفیف {pc}٪ اعمال شد!'); await _buy_screen(context.bot, m.chat_id, context, _qty(context)); return
        msgs={'invalid':'❌ کد معتبر نیست؛ دوباره بفرست یا «لغو» بنویس.','expired':'⌛ این کد تخفیف منقضی شده.','finished':'😕 ظرفیت این کد تموم شده.','already':'ℹ️ این کد رو قبلاً استفاده کردی.'}
        if res!='invalid': context.user_data.pop('gift_step',None)
        await m.reply_text(msgs.get(res,'❌ کد معتبر نیست.')); return
    if context.user_data.get('gift_step')=='redeem' and m.text:
        t=m.text.strip()
        if t=='لغو':
            context.user_data.pop('gift_step',None); await m.reply_text('لغو شد.',reply_markup=home_kb()); return
        now=time.time(); fails=[x for x in context.user_data.get('gift_fail',[]) if now-x<600]
        if len(fails)>=5:
            await m.reply_text('⛔ تلاش‌های اشتباه زیاد بود؛ ۱۰ دقیقه دیگه دوباره امتحان کن.'); return
        res,amt,bal=redeem_gift(u.id,t)
        if res=='ok':
            context.user_data.pop('gift_step',None); context.user_data.pop('gift_fail',None)
            await m.reply_text(f'🎉 کد هدیه ثبت شد!\n💎 {fmt_bal(amt)} الماس به حسابت اضافه شد.\n💰 موجودی جدید: {fmt_bal(bal)} الماس',reply_markup=home_kb()); return
        if res=='invalid':
            fails.append(now); context.user_data['gift_fail']=fails
        msgs={'invalid':'❌ کد معتبر نیست؛ دوباره بفرست یا «لغو» بنویس.','expired':'⌛ این کد منقضی شده.','finished':'😕 ظرفیت این کد تموم شده.','already':'ℹ️ این کد رو قبلاً استفاده کردی.'}
        if res!='invalid': context.user_data.pop('gift_step',None)
        await m.reply_text(msgs.get(res,'❌ کد معتبر نیست.')); return
    plan=context.user_data.get('payment_plan')
    if plan and (m.photo or m.document):
        fid=m.photo[-1].file_id if m.photo else m.document.file_id; fkind='photo' if m.photo else 'document'
        s=get_session()
        try:
            dd=context.user_data.get('disc'); pct=0; dcode=None
            if dd:
                pct=consume_discount(u.id,dd['code'])
                dcode=dd['code'] if pct else None
                context.user_data.pop('disc',None)
            p=Payment(user_id=u.id,plan=plan,status='pending',discount=pct,code=dcode,file_id=fid,file_kind=fkind); s.add(p); s.commit(); pid=p.id
        finally:s.close()
        context.user_data.pop('payment_plan',None)
        recipients=staff_ids('receipts')
        cap=pay_caption(pid,u.id,u.username,plan,pct,dcode)
        for staff in recipients:
            try:
                sm=await send_receipt(context.bot,staff,cap,fid,fkind,pay_kb(pid,staff,None,None))
                _pay_msgs.setdefault(pid,[]).append((staff,sm.message_id))
            except Exception: pass
        await m.reply_text('✅ رسید برای پشتیبانی ارسال شد. بعد از بررسی، الماس‌ها به حسابت اضافه می‌شن.',reply_markup=home_kb()); return
    if context.user_data.get('transfer_step')=='recipient':
        val=m.text.strip() if m.text else ''
        target=None
        if val.startswith('@'):
            try: target=(await context.bot.get_chat(val)).id
            except Exception: pass
        else:
            try: target=int(val)
            except Exception: pass
        if not target or target==u.id: await m.reply_text('آیدی معتبر بفرست؛ نمی‌تونی به خودت انتقال بدی.'); return
        context.user_data['transfer_target']=target; context.user_data['transfer_step']='amount'; await m.reply_text('مقدار الماس رو بفرست (مثلاً 25 یا 10.5):'); return
    if context.user_data.get('transfer_step')=='amount':
        try: amount=Decimal(m.text.strip().replace(',','.'))
        except Exception: amount=Decimal('0')
        target=context.user_data.get('transfer_target'); bal=wallet(u.id)['balance']
        if amount<=0 or amount*10 != (amount*10).to_integral_value() or bal<amount:
            await m.reply_text(f'مقدار نامعتبره یا موجودی کافی نیست. موجودی: {bal}'); return
        # Debit and credit in a single DB transaction
        s=get_session()
        try:
            src=s.get(Wallet,u.id); dst=s.get(Wallet,target)
            if not dst: dst=Wallet(user_id=target,balance=Decimal('0.0'),last_charge=int(time.time())); s.add(dst)
            if Decimal(src.balance)<amount: await m.reply_text('موجودی کافی نیست.'); return
            src.balance=Decimal(src.balance)-amount; dst.balance=Decimal(dst.balance)+amount; s.commit()
        finally:s.close()
        context.user_data.pop('transfer_step',None); context.user_data.pop('transfer_target',None)
        await m.reply_text(f'✅ {amount} الماس به {target} منتقل شد.',reply_markup=home_kb())
        try: await context.bot.send_message(target,f'💎 شما {amount} الماس از کاربر {u.id} دریافت کردید.')
        except Exception: pass
        return
    if m.text and m.text.strip()=='لغو':
        context.user_data.pop('payment_plan',None); context.user_data.pop('transfer_step',None); await m.reply_text('لغو شد.',reply_markup=home_kb())
async def charge_job(context):
    try: sync_self_state()
    except Exception: pass
    now=int(time.time()); s=get_session()
    try:
        for w in s.query(Wallet).all():
            trial_active = bool(getattr(w, 'trial_until', None) and now < w.trial_until)
            if getattr(w, 'trial_until', None) and now >= w.trial_until:
                w.trial_until = None
                w.self_started = False
                w.last_charge = now
                try:
                    selfbot.save_row(w.user_id, enabled=False)
                    context.application.create_task(selfbot.stop_client(w.user_id, restore=True))
                    context.application.create_task(context.bot.send_message(w.user_id, 'دورهٔ رایگان ۷ روزه تموم شد و سلف به‌صورت خودکار قطع شد. برای ادامه، الماس تهیه کن.'))
                except Exception:
                    pass
            # عضویت اجباری: اگر کاربر بعد از دریافت پاداش معرفی از کانال‌ها خارج شود، پاداش ۵۰ الماسی برگردانده می‌شود.
            try:
                member = await is_member(context.bot, w.user_id)
            except Exception:
                member = True
            if not member and w.referred_awarded:
                w.balance=max(Decimal('0.0'), Decimal(w.balance or 0)-Decimal('50.0'))
                if w.referrer:
                    rw=s.get(Wallet,w.referrer)
                    if rw: rw.balance=max(Decimal('0.0'),Decimal(rw.balance or 0)-Decimal('50.0'))
                w.referred_awarded=False
                try: context.application.create_task(context.bot.send_message(w.user_id,'⚠️ از یکی از کانال‌های عضویت اجباری خارج شدی؛ ۵۰ الماس پاداش دعوتت کسر شد. دوباره عضو شو.',reply_markup=join_kb()))
                except Exception: pass
            if trial_active:
                # No diamond deductions during the 7-day free period (ساعت شارژ هم جلو می‌رود تا بعد از پایان دوره بابت این مدت کسر نشود).
                w.last_charge = now
                continue
            last=w.last_charge or now; hours=(now-last)//3600
            if hours<=0: continue
            rate=Decimal('2.0') if w.self_started else Decimal('0.2')
            cost=rate*hours; w.spent=Decimal(w.spent or 0)+min(cost,Decimal(w.balance or 0)); w.balance=max(Decimal('0.0'),Decimal(w.balance or 0)-cost); w.last_charge=last+hours*3600
            if w.self_started and Decimal(w.balance)<=0:
                w.self_started=False
                try: selfbot.save_row(w.user_id, enabled=False)
                except Exception: pass
                try: context.application.create_task(selfbot.stop_client(w.user_id,restore=True))
                except Exception: pass
                try: context.application.create_task(context.bot.send_message(w.user_id,'⚠️ الماس‌هات تموم شد و سلف خاموش شد. برای ادامه الماس بخر.'))
                except Exception: pass
            elif cost>0:
                try: context.application.create_task(context.bot.send_message(w.user_id,f'💎 هزینه‌ی {hours} ساعت: {cost} الماس. موجودی فعلی: {w.balance}'))
                except Exception: pass
            if w.self_started and Decimal(w.balance or 0)>0:
                left=Decimal(w.balance)/Decimal('2.0')
                lvl=2 if left<3 else 1 if left<24 else 0
                if lvl==0: _warn_level.pop(w.user_id,None)
                elif lvl>_warn_level.get(w.user_id,0):
                    _warn_level[w.user_id]=lvl
                    try: context.application.create_task(context.bot.send_message(w.user_id,f'⚠️ الماست داره تموم می‌شه!\n💎 موجودی: {fmt_bal(w.balance)} الماس — سلفت حدود {int(left)} ساعت دیگه کار می‌کنه و بعدش خودکار خاموش می‌شه.\nبرای جلوگیری از قطع شدن، شارژ کن 👇',reply_markup=KB([[B('خرید الماس',callback_data='shop:buy')],[B('دعوت دوستان (۵۰ الماس)',callback_data='shop:invite')]])))
                    except Exception: pass
        s.commit()
    finally:s.close()

async def _gift_create(m, context, t):
    parts=t.replace(',',' ').split()
    try:
        amount=Decimal(parts[0]); uses=int(parts[1]) if len(parts)>1 else 1; hours=int(parts[2]) if len(parts)>2 else None
        if amount<=0 or amount>100000 or uses<1 or uses>10000 or (amount*10)!=(amount*10).to_integral_value(): raise ValueError
    except Exception:
        await m.reply_text('فرمت درست نیست. مثال: 100 5  (۱۰۰ الماس، ۵ نفر) یا 100 5 48 (با اعتبار ۴۸ ساعته). برای لغو بنویس «لغو».'); return
    code=make_gift(m.from_user.id, amount, uses, hours)
    context.user_data.pop('gift_step',None)
    await m.reply_text(f'🎁 کد هدیه ساخته شد:\n\n{code}\n\n💎 {fmt_bal(amount)} الماس | 👥 {uses} بار استفاده'+(f' | ⏳ {hours} ساعت' if hours else '')+'\nکاربر باید از «کد هدیه» توی منوی ربات واردش کنه.')

async def gift_cb(update, context):
    q=update.callback_query
    if q.from_user.id not in selfbot.ADMIN_IDS:
        await q.answer('دسترسی نداری.', show_alert=True); return
    await q.answer()
    act=(q.data or '').split(':')[1]
    if act=='new':
        context.user_data['gift_step']='admin_new'
        await q.message.reply_text('🎁 ساخت کد هدیه\n\nمقدار الماس و تعداد دفعات استفاده رو بفرست:\n100 5  ← ۱۰۰ الماس برای ۵ نفر\n100 5 48  ← با اعتبار ۴۸ ساعته\nبرای لغو بنویس «لغو».')
    elif act=='list':
        s=get_session()
        try:
            rows=s.query(GiftCode).order_by(GiftCode.created.desc()).limit(15).all()
            lines=[f'{g.code} | 💎 {fmt_bal(g.amount)} | {g.used}/{g.max_uses}'+(' | ⌛' if g.expires and g.expires<int(time.time()) else '') for g in rows]
        finally:
            s.close()
        await q.message.reply_text('🎟 آخرین کدهای هدیه (کد | الماس | استفاده):\n\n'+('\n'.join(lines) if lines else 'هنوز کدی ساخته نشده.'))

async def adm_cb(update, context):
    q=update.callback_query
    act=(q.data or '').split(':')[1]
    if act in ('bc','bcall','bcself','bccancel','bclink'):
        allowed=has_perm(q.from_user.id,'broadcast')
    else:
        allowed=q.from_user.id in selfbot.ADMIN_IDS
    if not allowed:
        await q.answer('دسترسی نداری.', show_alert=True); return
    await q.answer()
    if act=='stats':
        await q.message.reply_text(stats_text())
    elif act=='bc':
        context.user_data['gift_step']='bc_wait'
        await q.message.reply_text('📢 پیام همگانی\n\nپیامت رو بفرست؛ هر نوعی که باشه همون‌طور برای کاربران کپی می‌شه:\n• متن با فرمت تلگرام: بولد، ایتالیک، زیرخط، خط‌خورده، کد، اسپویلر، نقل‌قول و لینک\n• عکس، ویدیو، گیف، ویس، آهنگ، ویدیو مسیج، فایل یا استیکر (با کپشن)\nبعدش می‌تونی دکمهٔ لینک هم اضافه کنی. برای لغو بنویس «لغو».')
    elif act in ('bcall','bcself'):
        ref=context.user_data.pop('bc_msg',None)
        if not ref:
            await q.message.reply_text('پیامی برای ارسال نیست؛ دوباره از منوی ادمین شروع کن.'); return
        ids=broadcast_ids('all' if act=='bcall' else 'self')
        await q.message.reply_text(f'⏳ ارسال برای {len(ids)} نفر شروع شد؛ بعد از پایان گزارش می‌فرستم.')
        context.application.create_task(_broadcast(context.bot,q.from_user.id,ref[0],ref[1],ids,context.user_data.pop('bc_btns',None)))
    elif act=='bclink':
        if not context.user_data.get('bc_msg'):
            await q.message.reply_text('اول پیام همگانی رو بفرست.'); return
        context.user_data['gift_step']='bc_link'
        await q.message.reply_text('➕ دکمهٔ لینک رو این‌طوری بفرست (تا ۳ خط):\nمتن دکمه | https://example.com')
    elif act=='bccancel':
        context.user_data.pop('bc_msg',None); context.user_data.pop('bc_btns',None); await q.message.reply_text('ارسال همگانی لغو شد.')
    elif act=='dnew':
        context.user_data['gift_step']='disc_new'
        await q.message.reply_text('🏷 ساخت کد تخفیف\n\nدرصد و تعداد دفعات استفاده رو بفرست:\n20 10  ← ۲۰٪ تخفیف برای ۱۰ نفر\n20 10 72  ← با اعتبار ۷۲ ساعته\nبرای لغو بنویس «لغو».')
    elif act=='dlist':
        s=get_session()
        try:
            rows=s.query(DiscountCode).order_by(DiscountCode.created.desc()).limit(15).all()
            lines=[f'{d.code} | {d.percent}٪ | {d.used}/{d.max_uses}'+(' | ⌛' if d.expires and d.expires<int(time.time()) else '') for d in rows]
        finally:
            s.close()
        await q.message.reply_text('🏷 آخرین کدهای تخفیف (کد | درصد | استفاده):\n\n'+('\n'.join(lines) if lines else 'هنوز کدی ساخته نشده.'))

_BAL_RE = r'^\s*(موجودی|موجودی من|موجودی\s*من\s*چقدره|موجودیم)\s*[؟?!.]*\s*$'

def _bal_kb(uid, balance):
    return KB([[B('💎 ' + fmt_bal(balance) + ' الماس', callback_data=f'bal:{uid}')]])

async def balance_cmd(update, context):
    m = update.effective_message; u = update.effective_user
    if not m or not u or not m.text:
        return
    bal = wallet(u.id)['balance']
    try:
        await m.reply_text('💰 موجودی شما', reply_markup=_bal_kb(u.id, bal), reply_to_message_id=m.message_id)
    except Exception:
        pass
    if update.effective_chat.type == 'private':
        raise ApplicationHandlerStop

async def balance_cb(update, context):
    q = update.callback_query
    try:
        owner = int((q.data or '').split(':')[1])
    except Exception:
        await q.answer(); return
    if q.from_user.id != owner:
        await q.answer('این دکمه برای تو نیست؛ خودت بنویس «موجودی».', show_alert=True); return
    w = wallet(owner)
    state = 'روشن ✅' if w['self_started'] else 'خاموش ⏹'
    await q.answer(f'💎 موجودی شما: {fmt_bal(w["balance"])} الماس\n🔌 سلف: {state}', show_alert=True)
    try:
        await q.edit_message_reply_markup(reply_markup=_bal_kb(owner, w['balance']))
    except Exception:
        pass

def setup(app):
    Base.metadata.create_all(__import__('database').engine)
    _migrate_wallet()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(_BAL_RE), balance_cmd), group=-11)
    app.add_handler(CallbackQueryHandler(balance_cb, pattern=r'^bal:'), group=-11)
    app.add_handler(CallbackQueryHandler(gift_cb, pattern=r'^gift:'), group=-20)
    app.add_handler(CallbackQueryHandler(adm_cb, pattern=r'^adm:'), group=-20)
    app.add_handler(CallbackQueryHandler(sup_cb, pattern=r'^sup:'), group=-20)
    app.add_handler(CallbackQueryHandler(tk_cb, pattern=r'^tk:'), group=-20)
    app.add_handler(CommandHandler('start',start),group=-10)
    app.add_handler(CallbackQueryHandler(payment_review, pattern=r'^pay:(approve|reject|claim|info):'), group=-20)
    app.add_handler(CommandHandler('support', support_panel), group=-10)
    app.add_handler(CallbackQueryHandler(callback,pattern=r'^shop:'),group=-10)
    app.add_handler(MessageHandler((~filters.COMMAND)&filters.ChatType.PRIVATE, incoming),group=-10)
    app.job_queue.run_repeating(charge_job, interval=300, first=60, name='diamond-hourly-charges')
    try:   # مینی‌اپ + مار و پله؛ نیازی به تغییر main.py نیست و خطاش هیچ‌وقت ربات رو از کار نمی‌ندازه
        import miniapp
        miniapp.setup(app)
    except Exception:
        import logging; logging.getLogger(__name__).exception('miniapp setup failed')
