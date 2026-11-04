# -*- coding: utf-8 -*-
"""
مینی‌اپ تلگرام: داشبورد کاربر (الماس، وضعیت سلف، روشن/خاموش) + تب بازی‌ها (مار و پله) + تب سلف (پنل کامل سلف با کلید روشن/خاموش برای همهٔ امکانات؛ منطقش توی selfpanel.py).

- بدون وابستگی جدید (فقط stdlib)؛ یک سرور HTTP سبک توی همین پروسهٔ ربات بالا میاد و روی PORT گوش می‌ده.
- احراز هویت: initData تلگرام با HMAC و BOT_TOKEN چک می‌شه؛ آیدی کاربر فقط از همون خونده می‌شه (نه از ورودی کاربر).
- متغیرها: MINIAPP_URL (آدرس عمومی https، اختیاری؛ اگه نباشه از RAILWAY_PUBLIC_DOMAIN ساخته می‌شه)،
  PORT (Railway خودش می‌ده)، MINIAPP_DISABLED=1 برای خاموش‌کردن کامل.
- نصب: خودکار؛ commerce.setup(app) این ماژول رو صدا می‌زنه (مار و پله، snakes.py، هم از همین‌جا نصب می‌شه). تغییر main.py لازم نیست.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

AUTH_MAX_AGE = 24 * 3600      # initData قدیمی‌تر از این پذیرفته نمی‌شه
MAX_BODY = 32 * 1024
_app_ref = [None]
_server = [None]


# ---------- آدرس ----------
def base_url():
    if os.environ.get("MINIAPP_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return ""
    u = os.environ.get("MINIAPP_URL", "").strip().rstrip("/")
    if not u:
        d = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        u = ("https://" + d) if d else ""
    if u and not u.startswith("http"):
        u = "https://" + u
    return u


def app_url():
    b = base_url()
    return (b + "/app") if b else ""


# ---------- احراز هویت ----------
def validate_init_data(init_data, token, now=None, max_age=AUTH_MAX_AGE):
    """initData رو بررسی می‌کنه؛ خروجی: dict کاربر یا None."""
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    except Exception:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        age = (now or time.time()) - int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if age > max_age or age < -300:
        return None
    try:
        user = json.loads(pairs.get("user", ""))
        if not isinstance(user, dict) or not isinstance(user.get("id"), int):
            return None
        return user
    except Exception:
        return None


# ---------- منطق (همون حساب‌های commerce.py) ----------
def _cm():
    import commerce
    return commerce


def _referral_count(uid):
    """تعداد دعوت‌های موفق (کسی که با لینک این کاربر اومده و عضویتش تأیید شده)."""
    cm = _cm()
    s = cm.get_session()
    try:
        return int(s.query(cm.Wallet).filter(cm.Wallet.referrer == uid,
                                             cm.Wallet.referred_awarded.is_(True)).count())
    except Exception:
        logger.exception("miniapp: referral count failed")
        return 0
    finally:
        s.close()


def build_state(uid, member=True, user=None):
    cm = _cm()
    import selfbot
    w = cm.wallet(uid)
    bal = Decimal(w["balance"])
    on = bool(w["self_started"])
    rate = Decimal("2.0") if on else Decimal("0.2")
    hours = int(bal / rate) if bal > 0 else 0
    row = selfbot.get_row(uid)
    now = int(time.time())
    user = user or {}
    uname = str(user.get("username") or "").strip()
    fname = " ".join(x for x in (str(user.get("first_name") or "").strip(),
                                 str(user.get("last_name") or "").strip()) if x)[:60]
    trial_left = max(0, int(w.get("trial_until") or 0) - now)
    return {
        "id": uid,
        "username": uname,
        "name": fname,
        "referrals": _referral_count(uid),
        "days_left": hours // 24,
        "hours_left": hours % 24,
        "trial_days_left": (trial_left + 86399) // 86400 if trial_left else 0,
        "balance": cm.fmt_bal(bal),
        "spent": cm.fmt_bal(w["spent"]),
        "toman": int(bal * Decimal(cm.unit_price())),
        "on": on,
        "rate": "2" if on else "0.2",
        "hours": hours,
        "gift_received": bool(w["trial_used"]),
        "gift_amount": cm.TRIAL_BONUS,
        "connected": bool(row and row.session_enc),
        "trial_active": bool(w.get("trial_until") and int(w["trial_until"]) > now),
        "activate_cost": cm.ACTIVATE_COST,
        "member": bool(member),
        "support": cm.SUPPORT,
        "game": _game_of(uid),
        "draw": _draw_of(uid),
    }


def _game_of(uid):
    try:
        import snakes
        return snakes.active_summary(uid)
    except Exception:
        return None


def _draw_of(uid):
    try:
        import drawgame
        return drawgame.active_summary(uid)
    except Exception:
        return None


async def _member(uid):
    try:
        return bool(await _cm().is_member(_app_ref[0].bot, uid))
    except Exception:
        logger.exception("miniapp: membership check failed")
        return True       # خطای موقت تلگرام نباید داشبورد رو ببنده؛ تغییر وضعیت جدا چک می‌شه


async def toggle(uid, want_on):
    """روشن/خاموش‌کردن سلف؛ همون منطق دکمه‌های shop:on و shop:off. خروجی: (ok, کد خطا یا None)"""
    cm = _cm()
    import selfbot
    if not await _member(uid):
        return False, "not_member"
    w = cm.wallet(uid)
    if want_on:
        if w["self_started"]:
            return True, None
        now = int(time.time())
        trial_active = bool(w.get("trial_until") and int(w["trial_until"]) > now)
        if w["balance"] < cm.ACTIVATE_COST and not trial_active:
            return False, "low_balance"
        if not trial_active:
            cm.change(uid, -cm.ACTIVATE_COST)
            cm.add_spent(uid, cm.ACTIVATE_COST)
        s = cm.get_session()
        try:
            x = s.get(cm.Wallet, uid)
            x.self_started = True
            x.last_charge = now
            s.commit()
        finally:
            s.close()
        selfbot.save_row(uid, enabled=True)
        row = selfbot.get_row(uid)
        if row and row.session_enc and uid not in selfbot._clients:
            _app_ref[0].create_task(selfbot.start_client(uid))
        return True, None
    s = cm.get_session()
    try:
        x = s.get(cm.Wallet, uid)
        if x:
            x.self_started = False
            x.last_charge = int(time.time())
            s.commit()
    finally:
        s.close()
    selfbot.save_row(uid, enabled=False)
    if uid in selfbot._clients:
        await selfbot.stop_client(uid, restore=True)
    return True, None


# ---------- سرور HTTP سبک ----------
_STATUS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 413: "Payload Too Large", 500: "Internal Server Error"}
_rate = {}   # (uid, bucket) -> [timestamps]


def _limited(uid, limit=30, window=60, bucket="api"):
    now = time.time()
    k = (uid, bucket)
    q = [t for t in _rate.get(k, []) if now - t < window]
    q.append(now)
    _rate[k] = q
    if len(_rate) > 5000:
        for kk in [kk for kk, v in _rate.items() if not v or now - v[-1] > window]:
            _rate.pop(kk, None)
    return len(q) > limit


def _resp(code, body, ctype="application/json; charset=utf-8", cache="no-store"):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    b = body.encode() if isinstance(body, str) else body
    head = (f"HTTP/1.1 {code} {_STATUS.get(code, 'OK')}\r\nContent-Type: {ctype}\r\nContent-Length: {len(b)}\r\n"
            f"Cache-Control: {cache}\r\nX-Content-Type-Options: nosniff\r\n"
            "Content-Security-Policy: default-src 'self' https://telegram.org; script-src 'self' 'unsafe-inline' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org\r\n"
            "Connection: close\r\n\r\n")
    return head.encode() + b


BOARD_FILE = "snakes_board.webp"     # عکس تخته؛ کنار miniapp.py توی ریشهٔ ریپو
_board_cache = [None]


def _board_bytes():
    if _board_cache[0] is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), BOARD_FILE)
        try:
            with open(p, "rb") as f:
                _board_cache[0] = f.read()
        except Exception:
            logger.warning("miniapp: %s پیدا نشد؛ تخته بدون عکس نشون داده می‌شه.", BOARD_FILE)
            _board_cache[0] = b""
    return _board_cache[0]


def _page():
    """صفحهٔ مینی‌اپ؛ جدول مار و پله از snakes.py داخلش گذاشته می‌شه (یک منبع واحد)."""
    try:
        import snakes
        j = json.dumps({"snakes": snakes.SNAKES, "ladders": snakes.LADDERS})
    except Exception:
        logger.exception("miniapp: snakes tables unavailable")
        j = '{"snakes":{},"ladders":{}}'
    return HTML.replace("__SNK__", j)


async def _route(method, path, headers, body):
    if path in ("/health", "/healthz"):
        return _resp(200, {"ok": True})
    if path in ("/", "/app"):
        return _resp(200, _page(), "text/html; charset=utf-8")
    if path == "/board.webp":
        b = _board_bytes()
        if not b:
            return _resp(404, {"error": "not_found"})
        return _resp(200, b, "image/webp", cache="public, max-age=86400")
    if path in ("/api/me", "/api/toggle", "/api/snk/state", "/api/snk/roll",
                "/api/snk/packs", "/api/snk/pack_add", "/api/snk/chat",
                "/api/self/state", "/api/self/act", "/api/self/dialogs",
                "/api/drw/state", "/api/drw/ops", "/api/drw/guess", "/api/drw/report", "/api/drw/leave", "/api/drw/chat",
                "/api/snk/leave", "/api/snk/gpacks", "/api/snk/gpack_add", "/api/snk/gpack_del"):
        if method != "POST":
            return _resp(405, {"error": "method"})
        auth = headers.get("authorization", "")
        init = auth[4:] if auth.lower().startswith("tma ") else ""
        user = validate_init_data(init, os.environ.get("BOT_TOKEN", "").strip())
        if not user:
            return _resp(401, {"error": "auth"})
        uid = user["id"]
        if path.startswith("/api/self/"):
            # پنل کامل سلف (selfpanel.py)؛ uid فقط از initData امضاشده میاد
            if _limited(uid, 150, 60, "self") or (path.endswith("/dialogs") and _limited(uid, 25, 60, "selfdl")):
                return _resp(403, {"error": "rate"})
            try:
                data = json.loads(body.decode() or "{}")
                if not isinstance(data, dict):
                    raise ValueError
            except Exception:
                return _resp(400, {"error": "bad_request"})
            import selfpanel
            if path == "/api/self/state":
                return _resp(200, selfpanel.state(uid))
            if path == "/api/self/dialogs":
                return _resp(200, await selfpanel.dialogs(uid, str(data.get("kind") or "slot"), str(data.get("q") or "")[:40]))
            return _resp(200, await selfpanel.act(uid, data))
        if path.startswith("/api/drw/"):
            if _limited(uid, 600, 60, "drw"):
                return _resp(403, {"error": "rate"})
            try:
                data = json.loads(body.decode() or "{}")
                if not isinstance(data, dict):
                    raise ValueError
            except Exception:
                return _resp(400, {"error": "bad_request"})
            import drawgame
            data["_name"] = str(user.get("first_name") or "کاربر")[:20]
            try:
                return _resp(200, await drawgame.api(path, uid, data))
            except Exception:
                logger.exception("miniapp: drawgame api failed (%s)", path)
                return _resp(200, {"error": "internal"})
        if path.startswith("/api/snk/"):
            if _limited(uid, 200, 60, "snk"):
                return _resp(403, {"error": "rate"})
            try:
                data = json.loads(body.decode() or "{}")
                if not isinstance(data, dict):
                    raise ValueError
            except Exception:
                return _resp(400, {"error": "bad_request"})
            import snakes
            data["_name"] = str(user.get("first_name") or "کاربر")[:20]
            try:
                return _resp(200, await snakes.api(path, uid, data))
            except Exception:
                logger.exception("miniapp: snakes api failed (%s)", path)
                return _resp(200, {"error": "internal"})
        if _limited(uid):
            return _resp(403, {"error": "rate"})
        if path == "/api/me":
            return _resp(200, build_state(uid, await _member(uid), user))
        try:
            data = json.loads(body.decode() or "{}")
            want = data.get("on")
            if not isinstance(want, bool):
                raise ValueError
        except Exception:
            return _resp(400, {"error": "bad_request"})
        ok, err = await toggle(uid, want)
        st = build_state(uid, err != "not_member", user)
        st["ok"] = ok
        if err:
            st["error"] = err
        return _resp(200, st)
    return _resp(404, {"error": "not_found"})


async def _handle(reader, writer):
    try:
        async def _serve():
            line = await reader.readline()
            parts = line.decode("latin-1").split()
            if len(parts) < 2:
                return _resp(400, {"error": "bad_request"})
            method, path = parts[0].upper(), parts[1].split("?", 1)[0]
            headers = {}
            for _ in range(100):
                h = await reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
                k, _, v = h.decode("latin-1").partition(":")
                headers[k.strip().lower()] = v.strip()
            n = int(headers.get("content-length", "0") or 0)
            if n > MAX_BODY:
                return _resp(413, {"error": "too_large"})
            body = await reader.readexactly(n) if n else b""
            return await _route(method, path, headers, body)
        out = await asyncio.wait_for(_serve(), timeout=20)
    except Exception:
        logger.exception("miniapp request failed")
        out = _resp(500, {"error": "server"})
    try:
        writer.write(out)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _start(context=None):
    if _server[0]:
        return
    port = int(os.environ.get("PORT", "8080") or 8080)
    try:
        _server[0] = await asyncio.start_server(_handle, "0.0.0.0", port)
        logger.info("miniapp: listening on :%s (url=%s)", port, app_url() or "—")
    except Exception:
        logger.exception("miniapp: cannot start server on port %s", port)
        return
    if app_url():
        try:
            from telegram import MenuButtonWebApp, WebAppInfo
            await _app_ref[0].bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="پروفایل", web_app=WebAppInfo(url=app_url())))
        except Exception:
            logger.exception("miniapp: set menu button failed")


async def cmd_app(update, context):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    if update.effective_chat.type != "private":
        return
    if not app_url():
        await update.effective_message.reply_text("مینی‌اپ هنوز تنظیم نشده (MINIAPP_URL یا دامنهٔ عمومی Railway لازمه).")
        return
    await update.effective_message.reply_text(
        "👤 پروفایل مینی‌اپ", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("باز کردن پروفایل", web_app=WebAppInfo(url=app_url()))]]))


def setup(app):
    from telegram.ext import CommandHandler
    if _app_ref[0] is app:      # اگه دستی هم صدا زده بشه، دوبار نصب نمی‌شه
        return
    _app_ref[0] = app
    # آدرس درخواست‌های httpx شامل توکن رباته؛ از لاگ پنهانش می‌کنیم
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if os.environ.get("MINIAPP_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return
    if not base_url():
        logger.warning("miniapp: MINIAPP_URL / RAILWAY_PUBLIC_DOMAIN تنظیم نشده؛ سرور بالا میاد ولی دکمهٔ مینی‌اپ نشون داده نمی‌شه.")
    app.add_handler(CommandHandler("app", cmd_app), group=-10)
    app.job_queue.run_once(_start, 2, name="miniapp-start")
    try:
        import snakes
        snakes.setup(app)
    except Exception:
        logger.exception("miniapp: snakes setup failed")
    try:
        import drawgame
        drawgame.setup(app)
    except Exception:
        logger.exception("miniapp: drawgame setup failed")


# ---------- رابط کاربری ----------
HTML = r"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>پروفایل</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#fff);--tx:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#707579);
--card:var(--tg-theme-secondary-bg-color,#f1f3f5);--btn:var(--tg-theme-button-color,#2481cc);--btx:var(--tg-theme-button-text-color,#fff);
--ok:#2e9e5b;--bad:#d64545}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Vazirmatn",Tahoma,sans-serif;
padding:16px 14px calc(84px + env(safe-area-inset-bottom));min-height:100vh}
body.ing{padding-bottom:calc(150px + env(safe-area-inset-bottom))}
h1{font-size:18px;margin:0 0 14px}
.hero{background:var(--card);border-radius:18px;padding:18px;text-align:center}
.hero .n{font-size:40px;font-weight:700;line-height:1.1}
.hero .l{color:var(--hint);font-size:13px;margin-top:4px}
.pill{display:inline-block;margin-top:12px;padding:5px 14px;border-radius:99px;font-size:13px;font-weight:600;color:#fff}
.pill.on{background:var(--ok)}.pill.off{background:var(--hint)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
.c{background:var(--card);border-radius:14px;padding:12px}
.c b{display:block;font-size:17px;margin-top:2px}.c span{color:var(--hint);font-size:12px}
.row{background:var(--card);border-radius:14px;padding:12px;margin-top:10px;display:flex;justify-content:space-between;gap:8px;font-size:14px}
.row span:first-child{color:var(--hint)}
button{width:100%;margin-top:14px;padding:14px;border:0;border-radius:14px;font-size:16px;font-weight:600;background:var(--btn);color:var(--btx);font-family:inherit}
button.red{background:var(--bad);color:#fff}button.alt{background:var(--card);color:var(--tx)}
button:disabled{opacity:.55}
.msg{margin-top:12px;padding:10px 12px;border-radius:12px;background:var(--card);font-size:13px;text-align:center}
.id{direction:ltr;unicode-bidi:embed;cursor:pointer}
.skel{opacity:.5}
.sm{width:auto;padding:8px 14px;font-size:13px;margin:0 0 10px}
.pl{display:flex;align-items:center;gap:8px;background:var(--card);border-radius:12px;padding:9px 12px;margin-top:6px;font-size:14px}
.pl u{display:inline-block;width:13px;height:13px;border-radius:50%;border:1.5px solid #fff;text-decoration:none;flex:none}
.pl span{flex:1}.pl.cur{outline:2px solid var(--btn)}.pl.out{opacity:.45;text-decoration:line-through}
.top{background:var(--card);border-radius:14px;padding:12px;text-align:center;font-size:15px}
.lg{color:var(--hint);font-size:12px;margin-top:8px;line-height:1.7}

.dice3{width:66px;height:66px;perspective:420px;flex:none;margin:6px 10px 6px 4px}
.dice3.roll{animation:dhop 1s ease-out}
@keyframes dhop{0%{transform:translateY(0) scale(1)}25%{transform:translateY(-34px) scale(1.12)}50%{transform:translateY(0) scale(.95)}68%{transform:translateY(-12px)}84%{transform:translateY(0) scale(1.03)}100%{transform:translateY(0)}}
.tilt{position:relative;width:100%;height:100%;transform-style:preserve-3d;transform:rotateX(-24deg) rotateY(-30deg)}
.cube{position:relative;width:100%;height:100%;transform-style:preserve-3d}
.df{position:absolute;inset:0;border-radius:13px;display:grid;grid-template:repeat(3,1fr)/repeat(3,1fr);padding:9px;
 background:radial-gradient(circle at 30% 22%,#fff 0%,#f4f1ff 45%,#d6d2f0 100%);
 box-shadow:inset 0 0 0 1.5px rgba(255,255,255,.9),inset 0 -5px 9px rgba(90,80,160,.35),inset 0 4px 7px rgba(255,255,255,.9),0 0 14px rgba(124,92,255,.55)}
.df i{place-self:center;width:82%;height:82%;border-radius:50%}
.df i.p{background:radial-gradient(circle at 34% 28%,#ff8aa0 0%,#e0143f 45%,#6d0019 100%);box-shadow:inset 0 -1.5px 2.5px rgba(0,0,0,.45),0 0 3px rgba(255,60,90,.5)}
.f1 i.p{width:112%;height:112%}
.f1{transform:translateZ(33px)}.f6{transform:rotateY(180deg) translateZ(33px)}
.f2{transform:rotateY(90deg) translateZ(33px)}.f5{transform:rotateY(-90deg) translateZ(33px)}
.f3{transform:rotateX(90deg) translateZ(33px)}.f4{transform:rotateX(-90deg) translateZ(33px)}
.dsh{position:absolute;left:8%;right:8%;bottom:-10px;height:10px;border-radius:50%;background:radial-gradient(closest-side,rgba(0,0,0,.55),transparent);pointer-events:none}
.quake{animation:qk .55s}
@keyframes qk{0%,100%{transform:translate(0,0)}15%{transform:translate(-4px,2px)}30%{transform:translate(4px,-2px)}45%{transform:translate(-3px,-2px)}60%{transform:translate(3px,2px)}80%{transform:translate(-1px,1px)}}
.gbar{display:flex;justify-content:space-between;align-items:center;gap:8px}
.gbar .sm{margin-bottom:6px}
.dkw{position:fixed;left:0;right:0;bottom:0;z-index:6;padding:26px 12px calc(10px + env(safe-area-inset-bottom));background:linear-gradient(180deg,rgba(9,13,27,0),rgba(9,13,27,.94) 38%);display:none}
.dk{max-width:520px;margin:0 auto;display:flex;align-items:center;gap:16px;background:rgba(18,25,51,.97);border:1px solid rgba(124,92,255,.35);border-radius:24px;padding:12px 16px;box-shadow:0 -8px 30px rgba(124,92,255,.28)}
.dkr{flex:1;min-width:0;text-align:center}
.dkt{font-size:13px;color:var(--hint);min-height:36px;line-height:1.55;margin-bottom:6px}
.dk button{margin:0;padding:15px 10px;font-size:17px;border-radius:16px}
.dk button.go{animation:gp 1.4s ease-in-out infinite}
.dk button:disabled{animation:none;opacity:.55}
@keyframes gp{0%,100%{box-shadow:0 6px 20px rgba(124,92,255,.35)}50%{box-shadow:0 0 0 5px rgba(25,211,255,.25),0 6px 28px rgba(25,211,255,.6)}}
body.ing .toast{bottom:calc(150px + env(safe-area-inset-bottom))}

/* ---- تخته: خود عکس (شماره‌ها و مارها توش‌ان) + فقط مهره‌ها روش ---- */
.bw{position:relative;width:100%;max-width:520px;margin:12px auto 0;aspect-ratio:2/3;container-type:inline-size;border-radius:14px;overflow:hidden;background:#1d2733;direction:ltr;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.bw img{display:block;width:100%;height:100%}
.tkl{position:absolute;inset:0;pointer-events:none;z-index:5}
.tok{position:absolute;width:20px;height:20px;width:5.2cqw;height:5.2cqw;transform:translate(-50%,-50%);border-radius:50%;border:2.5px solid #fff;
 background-image:radial-gradient(circle at 32% 26%,rgba(255,255,255,.6),transparent 55%);
 box-shadow:0 3px 8px rgba(0,0,0,.7);transition:left .2s ease-out,top .2s ease-out,opacity .3s;z-index:3}
.tok.step{animation:thop .17s ease-out}
@keyframes thop{0%{transform:translate(-50%,-50%) scale(1)}50%{transform:translate(-50%,-78%) scale(1.18)}100%{transform:translate(-50%,-50%) scale(1)}}
.tok.jump{transition:left .9s cubic-bezier(.55,0,.25,1),top .9s cubic-bezier(.55,0,.25,1)}
.tok.me{box-shadow:0 0 0 3px rgba(255,235,59,.95),0 3px 8px rgba(0,0,0,.7)}
.toast{position:fixed;left:50%;bottom:calc(24px + env(safe-area-inset-bottom));transform:translateX(-50%);max-width:92%;background:rgba(20,20,20,.88);color:#fff;padding:10px 16px;border-radius:14px;font-size:14px;z-index:9;display:none;text-align:center}

/* ---- تب‌ها و بازی‌ها ---- */
.tabs{position:fixed;left:0;right:0;bottom:0;display:flex;background:var(--card);border-top:1px solid rgba(128,128,128,.25);padding-bottom:env(safe-area-inset-bottom);z-index:5}
.tabs a{flex:1;text-align:center;padding:9px 0 8px;font-size:12px;color:var(--hint);cursor:pointer}
.tabs a i{display:block;font-style:normal;font-size:20px;margin-bottom:1px}
.tabs a.on{color:var(--btn);font-weight:700}
body.ing .tabs{display:none}
.gc{background:var(--card);border-radius:18px;overflow:hidden;margin-top:4px}
.gimg{height:150px;background:#1d2733 url(/board.webp) top center/100% auto no-repeat}
.gb{padding:14px}
.gb h3{margin:0 0 6px;font-size:17px}
.gb p{margin:0;color:var(--hint);font-size:13px;line-height:1.8}
ul.rl{margin:10px 0 0;padding:0 18px 0 0;font-size:13px;line-height:1.9;color:var(--tx)}
.soon{opacity:.6}

/* ================= تم نئونی + پنل سلف ================= */
:root{--bg:#090d1b;--tx:#eef1ff;--hint:#8d96b8;--card:#121933;--card2:#182142;--btn:#7c5cff;--btx:#fff;--ok:#27d98a;--bad:#ff4d6d;
--ac1:#7c5cff;--ac2:#19d3ff;--line:rgba(255,255,255,.07)}
body{background:radial-gradient(900px 420px at 85% -80px,rgba(124,92,255,.28),transparent 70%),radial-gradient(700px 380px at -10% 12%,rgba(25,211,255,.14),transparent 70%),var(--bg);background-attachment:fixed}
.hero,.c,.row,.gc,.top,.pl,.msg{border:1px solid var(--line)}
button{background:linear-gradient(135deg,var(--ac1),var(--ac2));box-shadow:0 6px 20px rgba(124,92,255,.28);transition:transform .12s,filter .2s}
button:active{transform:scale(.97)}
button.red{background:linear-gradient(135deg,#ff4d6d,#ff8a5c);box-shadow:0 6px 20px rgba(255,77,109,.28)}
button.alt{background:var(--card2);box-shadow:none;border:1px solid var(--line)}
.tabs{background:rgba(14,19,40,.92);backdrop-filter:blur(14px);border-top:1px solid var(--line)}
.tabs a.on{color:var(--ac2);text-shadow:0 0 12px rgba(25,211,255,.55)}

.shero{position:relative;overflow:hidden;border-radius:22px;padding:18px 16px 16px;text-align:center;border:1px solid var(--line);
background:linear-gradient(160deg,rgba(124,92,255,.22),rgba(25,211,255,.08) 60%,rgba(18,25,51,.9))}
.ring{position:relative;width:96px;height:96px;margin:2px auto 10px;border-radius:50%;display:grid;place-items:center;font-size:40px}
.ring::before{content:"";position:absolute;inset:0;border-radius:50%;padding:3px;
background:conic-gradient(from 0deg,var(--ac1),var(--ac2),var(--ac1));-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);
-webkit-mask-composite:xor;mask-composite:exclude;opacity:.25}
.ring.on::before{opacity:1;animation:spin 3.2s linear infinite}
.ring.on{box-shadow:0 0 34px rgba(124,92,255,.55),inset 0 0 22px rgba(25,211,255,.18);animation:pulse 2.6s ease-in-out infinite}
.ring i{font-style:normal;filter:drop-shadow(0 0 10px rgba(25,211,255,.6))}
.ring:not(.on) i{filter:grayscale(1);opacity:.55}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{50%{box-shadow:0 0 52px rgba(25,211,255,.55),inset 0 0 26px rgba(124,92,255,.25)}}
.shero h2{margin:0;font-size:20px}
.shero p{margin:4px 0 0;color:var(--hint);font-size:12.5px}
.chips{display:flex;flex-wrap:wrap;gap:6px;justify-content:center;margin-top:12px}
.chip{padding:5px 11px;border-radius:99px;font-size:12px;background:rgba(255,255,255,.07);border:1px solid var(--line)}
.chip.g{color:var(--ok);background:rgba(39,217,138,.12)}.chip.r{color:var(--bad);background:rgba(255,77,109,.12)}.chip.y{color:#ffd24d;background:rgba(255,210,77,.1)}
.sbtns{display:flex;gap:8px;margin-top:12px}.sbtns button{margin:0;padding:11px;font-size:14px}

.ac{background:var(--card);border:1px solid var(--line);border-radius:18px;margin-top:10px;overflow:hidden}
.ach{display:flex;align-items:center;gap:10px;padding:13px 14px;cursor:pointer;user-select:none}
.ach .ic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;font-size:19px;background:linear-gradient(135deg,rgba(124,92,255,.35),rgba(25,211,255,.2));flex:none}
.ach b{flex:1;font-size:15px}.ach small{color:var(--hint);font-size:11.5px;display:block;font-weight:400;margin-top:2px}
.ach .bd{font-size:11px;padding:3px 9px;border-radius:99px;background:rgba(255,255,255,.08);color:var(--hint)}
.ach .bd.on{background:rgba(39,217,138,.16);color:var(--ok)}
.ach .cv{color:var(--hint);transition:transform .25s;font-size:14px}.ac.open .cv{transform:rotate(-90deg)}
.acb{display:none;padding:2px 12px 14px}.ac.open .acb{display:block;animation:fd .25s}
@keyframes fd{from{opacity:0;transform:translateY(-4px)}}
.sr{display:flex;align-items:center;gap:10px;padding:11px 4px;border-top:1px solid var(--line);cursor:pointer}
.acb>.sr:first-child{border-top:0}
.sr .t{flex:1;font-size:14px}.sr .t small{display:block;color:var(--hint);font-size:11.5px;margin-top:2px;line-height:1.6}
.sw{position:relative;width:48px;height:28px;border-radius:99px;background:#2a3355;flex:none;transition:background .25s,box-shadow .25s}
.sw::after{content:"";position:absolute;top:3px;right:3px;width:22px;height:22px;border-radius:50%;background:#fff;transition:right .25s;box-shadow:0 2px 6px rgba(0,0,0,.4)}
.sw.on{background:linear-gradient(135deg,var(--ac1),var(--ac2));box-shadow:0 0 16px rgba(124,92,255,.65)}.sw.on::after{right:23px}
.sw.dg.on{background:linear-gradient(135deg,#ff4d6d,#ff8a5c);box-shadow:0 0 16px rgba(255,77,109,.6)}
.mini{display:inline-block;padding:8px 13px;border-radius:12px;font-size:13px;background:var(--card2);border:1px solid var(--line);color:var(--tx);cursor:pointer;margin:4px 0 0 6px}
.mini.p{background:linear-gradient(135deg,var(--ac1),var(--ac2));border:0;color:#fff}.mini.d{color:var(--bad)}
.fg{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
.fc{background:var(--card2);border:1px solid var(--line);border-radius:13px;padding:9px 8px;text-align:center;cursor:pointer;transition:.2s}
.fc small{display:block;color:var(--hint);font-size:11px;margin-top:3px}.fc .sm1{font-size:16px;direction:ltr;word-break:break-all}
.fc.on{border-color:var(--ac2);box-shadow:0 0 14px rgba(25,211,255,.3);background:linear-gradient(160deg,rgba(124,92,255,.22),var(--card2))}
.f-u{text-decoration:underline}.f-s{text-decoration:line-through}.f-code{font-family:monospace}.f-b,.f-bi{font-weight:700}.f-i,.f-bi{font-style:italic}
.f-spoiler{filter:blur(4px)}.f-quote{border-right:3px solid var(--ac2);padding-right:6px}
.seg{display:flex;background:var(--card2);border-radius:12px;padding:3px;margin-top:8px;border:1px solid var(--line)}
.seg a{flex:1;text-align:center;padding:8px 0;border-radius:9px;font-size:13px;color:var(--hint);cursor:pointer}
.seg a.on{background:linear-gradient(135deg,var(--ac1),var(--ac2));color:#fff;font-weight:700}
.slc{background:var(--card2);border:1px solid var(--line);border-radius:16px;padding:12px;margin-top:9px}
.slc.rdy{border-color:rgba(39,217,138,.45);box-shadow:0 0 18px rgba(39,217,138,.12)}
.slh{display:flex;align-items:center;gap:8px;font-size:15px}.slh b{flex:1}
.slh .n{width:26px;height:26px;border-radius:9px;display:grid;place-items:center;background:linear-gradient(135deg,var(--ac1),var(--ac2));font-size:13px;font-weight:700}
.slg{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px}
.slg div{background:rgba(255,255,255,.05);border-radius:11px;padding:8px 10px;font-size:12.5px;cursor:pointer;line-height:1.6}
.slg div small{display:block;color:var(--hint);font-size:11px}.slg div.no{outline:1px dashed rgba(255,210,77,.55)}
.hint{color:var(--hint);font-size:12px;line-height:1.8;margin:8px 2px 0}
.tag{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:99px;background:var(--card2);border:1px solid var(--line);font-size:12.5px;margin:6px 0 0 6px;max-width:100%}
.tag u{text-decoration:none;color:var(--bad);cursor:pointer;font-size:15px;line-height:1}
.guide{padding:11px 4px;border-top:1px solid var(--line);cursor:pointer;font-size:14px}
.acb>.guide:first-child{border-top:0}
.gbody{display:none;margin-top:8px;color:var(--hint);font-size:12.5px;line-height:1.9;background:rgba(255,255,255,.04);border-radius:12px;padding:10px}
.guide.open .gbody{display:block}.gbody code{direction:ltr;unicode-bidi:embed;background:rgba(124,92,255,.22);color:#cfd6ff;padding:1px 6px;border-radius:6px;font-size:12px}
.gbody b{color:var(--tx)}

.shw{position:fixed;inset:0;background:rgba(3,5,14,.65);backdrop-filter:blur(4px);z-index:20;display:none;align-items:flex-end}
.shw.on{display:flex}
.shb{width:100%;max-height:82vh;overflow:auto;background:var(--card);border-radius:24px 24px 0 0;padding:16px 16px calc(18px + env(safe-area-inset-bottom));border-top:1px solid var(--line);animation:up .28s ease-out}
@keyframes up{from{transform:translateY(40px);opacity:0}}
.shb h3{margin:0 0 10px;font-size:16px}
.shb input,.shb textarea{width:100%;background:var(--card2);color:var(--tx);border:1px solid var(--line);border-radius:12px;padding:12px;font:inherit;font-size:15px;outline:0}
.shb input:focus,.shb textarea:focus{border-color:var(--ac2)}.shb textarea{min-height:90px;resize:vertical}
.opt{display:flex;align-items:center;gap:10px;padding:12px;border-radius:13px;background:var(--card2);border:1px solid var(--line);margin-top:7px;cursor:pointer;font-size:14px}
.opt small{color:var(--hint);font-size:11.5px;margin-right:auto}.opt.on{border-color:var(--ac2)}
.chipsel{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px}.chipsel a{padding:9px 14px;border-radius:12px;background:var(--card2);border:1px solid var(--line);font-size:13px;cursor:pointer}
.chipsel a.on{background:linear-gradient(135deg,var(--ac1),var(--ac2));border:0;color:#fff}
.lk{opacity:.5;pointer-events:none}
.shine{position:relative;overflow:hidden}
.shine::after{content:"";position:absolute;top:0;left:-60%;width:40%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.28),transparent);animation:sh2 4.5s infinite}
@keyframes sh2{0%,60%{left:-60%}100%{left:130%}}

/* ================= پروفایل + دکمه‌های خفن + آیکن‌های ویژه ================= */
.ico{width:1.2em;height:1.2em;vertical-align:-.22em;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 0 4px rgba(25,211,255,.5))}
.ei{display:inline-flex;align-items:center;justify-content:center;line-height:1}
button .ico,.mini.p .ico,.seg a.on .ico,.chipsel a.on .ico,.slh .n .ico,.sw .ico{stroke:#fff;filter:drop-shadow(0 0 3px rgba(255,255,255,.5))}
.mini{display:inline-flex;align-items:center;gap:6px}
button{position:relative;overflow:hidden;display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid rgba(255,255,255,.2);
 background-size:200% 200%;animation:bgm 7s ease-in-out infinite;letter-spacing:.2px;text-shadow:0 1px 8px rgba(0,0,0,.35)}
button::after{content:"";position:absolute;top:0;left:-70%;width:38%;height:100%;background:linear-gradient(100deg,transparent,rgba(255,255,255,.32),transparent);transform:skewX(-20deg);animation:sh2 5.5s infinite;pointer-events:none}
button.alt{animation:none;text-shadow:none}button.alt::after,button:disabled::after,.dk button::after{display:none}
button.alt .ico{stroke:url(#gr)}
@keyframes bgm{0%,100%{background-position:0 50%}50%{background-position:100% 50%}}
.tabs{left:10px;right:10px;bottom:calc(8px + env(safe-area-inset-bottom));padding:6px;gap:4px;border-radius:24px;border:1px solid var(--line);
 box-shadow:0 12px 36px rgba(0,0,0,.6),0 0 26px rgba(124,92,255,.22)}
.tabs a{border-radius:18px;padding:8px 0 6px;transition:.25s;font-weight:600}
.tabs a i .ico,.tabs a .ico{width:25px;height:25px;opacity:.75}
.tabs a.on{background:linear-gradient(160deg,rgba(124,92,255,.38),rgba(25,211,255,.18));box-shadow:inset 0 0 0 1px rgba(124,92,255,.55),0 0 20px rgba(124,92,255,.4)}
.tabs a.on .ico{opacity:1;stroke:#fff;filter:drop-shadow(0 0 6px rgba(25,211,255,.9))}
body{padding-bottom:calc(104px + env(safe-area-inset-bottom))}
h1{display:flex;align-items:center;gap:8px;font-size:19px}h1 .ico{width:24px;height:24px}

.phero{position:relative;overflow:hidden;text-align:center;padding:22px 14px 16px;border-radius:26px;border:1px solid var(--line);
 background:linear-gradient(160deg,rgba(124,92,255,.3),rgba(25,211,255,.1) 55%,rgba(18,25,51,.92))}
.phero::before{content:"";position:absolute;width:220px;height:220px;right:-70px;top:-90px;border-radius:50%;background:radial-gradient(closest-side,rgba(25,211,255,.28),transparent)}
.pav{position:relative;width:92px;height:92px;margin:0 auto 10px;border-radius:50%;display:grid;place-items:center;font-size:40px;font-weight:800;color:#fff;
 background:linear-gradient(135deg,var(--ac1),var(--ac2));box-shadow:0 0 0 4px rgba(255,255,255,.07),0 0 30px rgba(124,92,255,.6)}
.pav.on{animation:pulse 2.6s ease-in-out infinite}
.phero h2{position:relative;margin:0;font-size:20px}
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.pc{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px;min-width:0}
.pc.hl{background:linear-gradient(150deg,rgba(124,92,255,.3),var(--card));border-color:rgba(124,92,255,.5);box-shadow:0 0 24px rgba(124,92,255,.2)}
.pc.cp{cursor:pointer}.pc.cp:active{transform:scale(.98)}
.pi{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;flex:none;background:linear-gradient(135deg,rgba(124,92,255,.38),rgba(25,211,255,.2));box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}
.pi .ico{width:23px;height:23px}
.pt{min-width:0;flex:1}.pt span{display:block;color:var(--hint);font-size:11.5px}
.pt b{display:block;font-size:15px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ptime{margin-top:10px;background:linear-gradient(150deg,rgba(25,211,255,.14),var(--card));border:1px solid rgba(25,211,255,.3);border-radius:20px;padding:14px}
.ptime .top2{display:flex;align-items:center;gap:10px}
.ptime .top2 b{display:block;font-size:17px;margin-top:2px}
.bar{height:8px;border-radius:99px;background:rgba(255,255,255,.08);margin-top:12px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--ac1),var(--ac2));box-shadow:0 0 12px rgba(25,211,255,.7)}
.ptime small{display:block;color:var(--hint);font-size:11.5px;margin-top:8px;line-height:1.7}

/* ================= بازی نقاشی ================= */
.dimg{height:112px;position:relative;background:radial-gradient(circle at 18% 30%,#ff4081 0,transparent 38%),radial-gradient(circle at 78% 22%,#ffd600 0,transparent 36%),radial-gradient(circle at 52% 92%,#00e5ff 0,transparent 44%),linear-gradient(135deg,#651fff,#2979ff)}
.dimg::after{content:"🎨";position:absolute;inset:0;display:grid;place-items:center;font-size:56px;filter:drop-shadow(0 6px 14px rgba(0,0,0,.45))}
.dhd{display:flex;align-items:center;gap:10px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:10px 12px}
.dch{padding:5px 11px;border-radius:99px;font-size:12px;font-weight:800;background:linear-gradient(135deg,var(--ac1),var(--ac2));color:#fff;white-space:nowrap}
.dtt{flex:1;min-width:0}.dtt b{display:block;font-size:14.5px}.dtt small{display:block;color:var(--hint);font-size:11.5px;margin-top:2px}
.dring{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;position:relative;flex:none;background:conic-gradient(#19d3ff 360deg,rgba(255,255,255,.12) 0)}
.dring::after{content:"";position:absolute;inset:5px;border-radius:50%;background:var(--card)}
.dring b{position:relative;z-index:1;font-size:18px}
.dpls{display:flex;gap:6px;margin-top:8px}
.dpl{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;gap:2px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:7px 3px;font-size:11.5px}
.dpl u{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;border:2px solid #fff;color:#fff;font-weight:800;font-size:13px;text-decoration:none}
.dpl span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dpl b{font-size:15px}
.dpl.dr{outline:2px solid #ffd24d;box-shadow:0 0 14px rgba(255,210,77,.35)}.dpl.me{background:var(--card2)}
.dpl.up{animation:dpup .7s ease-out}
@keyframes dpup{0%{transform:scale(1)}35%{transform:scale(1.14);box-shadow:0 0 18px rgba(39,217,138,.7)}100%{transform:scale(1)}}
.dcw{position:relative;width:100%;max-width:520px;margin:10px auto 0;aspect-ratio:1/1;border-radius:20px;overflow:hidden;background:#fffef9;box-shadow:0 0 0 3px rgba(124,92,255,.55),0 14px 40px rgba(124,92,255,.35)}
#dcv{width:100%;height:100%;display:block;touch-action:none;cursor:crosshair}
.dov{position:absolute;inset:0;display:none;align-items:center;justify-content:center;padding:10px;overflow:auto;background:rgba(9,13,27,.86);backdrop-filter:blur(6px)}
.dov.on{display:flex;animation:dpop .35s ease-out}
@keyframes dpop{0%{opacity:0;transform:scale(.92)}100%{opacity:1;transform:scale(1)}}
.dcard{width:100%;text-align:center;margin:auto}.dcard small{color:var(--hint);font-size:12px;display:block;margin-top:3px}.dcard>b{display:block;font-size:17px;margin-top:4px}
.dbig{font-size:46px;line-height:1.1}
.dwd{font-size:32px;font-weight:800;margin:2px 0 8px;background:linear-gradient(135deg,#ffd24d,#ff7a59);-webkit-background-clip:text;background-clip:text;color:transparent}
.drow{display:flex;align-items:center;gap:8px;background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:12px;padding:7px 10px;margin-top:5px;font-size:13px;text-align:right}
.drow.w{background:rgba(39,217,138,.14);border-color:rgba(39,217,138,.4)}.drow.l{background:rgba(255,77,109,.1)}.drow.dr{background:rgba(255,210,77,.12)}
.drow .nm2{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.drow .st{color:var(--hint);font-size:11.5px}
.dnext{margin-top:10px;font-size:12.5px;color:var(--hint)}.dnext b{color:#19d3ff;font-size:17px}
.dword{margin-top:10px;text-align:center;padding:11px 10px;border-radius:18px;background:linear-gradient(135deg,rgba(124,92,255,.28),rgba(25,211,255,.2));border:1px solid rgba(124,92,255,.55)}
.dword small{display:block;color:var(--hint);font-size:11.5px}.dword b{display:block;font-size:26px;margin:2px 0}
.dtools{margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px 8px}
.dsw{display:flex;flex-wrap:wrap;gap:9px;justify-content:center}
.dsw a{width:31px;height:31px;border-radius:50%;border:2.5px solid rgba(255,255,255,.85);box-shadow:0 2px 6px rgba(0,0,0,.5);cursor:pointer;transition:transform .12s}
.dsw a.on{transform:scale(1.3);box-shadow:0 0 0 3px #fff,0 0 16px currentColor}
.dsz{display:flex;gap:8px;justify-content:center;margin-top:12px}
.dsz a{min-width:46px;height:40px;padding:0 8px;border-radius:12px;background:var(--card2);border:1px solid var(--line);display:grid;place-items:center;cursor:pointer;font-size:18px}
.dsz a i{display:block;border-radius:50%;background:#fff}
.dsz a.on{background:linear-gradient(135deg,var(--ac1),var(--ac2));border-color:transparent}
.dopts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));grid-auto-flow:row;gap:5px;margin-top:12px;direction:rtl}
.dop{margin:0;padding:6px 1px;min-height:50px;width:100%;font-size:11px;line-height:1.3;font-weight:700;border-radius:10px;background:var(--card2);box-shadow:none;border:1.5px solid rgba(124,92,255,.5);display:flex;align-items:center;justify-content:center;text-align:center;overflow-wrap:anywhere;word-break:break-word;white-space:normal}
.dpl.out{opacity:.4}
.dchat{margin-top:12px;background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:8px}
.dcm{max-height:112px;overflow:auto;font-size:12.5px;line-height:1.7}
.dqe{display:flex;gap:6px;justify-content:center;flex-wrap:wrap;margin:6px 0}
.dqe a{font-size:20px;cursor:pointer;padding:2px 6px;border-radius:9px;background:rgba(255,255,255,.07)}
.dci{display:flex;gap:6px}.dci input{flex:1;min-width:0}
.xbtn{border-color:rgba(255,77,109,.7)!important;color:#ff8a9d!important}
.dop:disabled{opacity:.55}
.dop.ok{background:linear-gradient(135deg,#00c853,#27d98a);border-color:transparent;opacity:1}
.dop.no{background:linear-gradient(135deg,#ff1744,#ff6d6d);border-color:transparent;opacity:1}
.dnote{margin-top:10px;text-align:center;font-size:13px;color:var(--hint);line-height:1.8}
.cf{position:absolute;top:-16px;width:9px;height:15px;border-radius:2px;animation:dfall 2.8s linear forwards;pointer-events:none}
@keyframes dfall{to{transform:translateY(560px) rotate(600deg)}}
</style></head><body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><linearGradient id="gr" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#b69cff"/><stop offset="1" stop-color="#19d3ff"/></linearGradient></defs></svg>
<h1 id="ttl">👤 پروفایل</h1>
<div id="v" class="skel"><div class="hero"><div class="n">…</div><div class="l">در حال بارگذاری</div></div></div>
<div id="gl" style="display:none"></div>
<div id="sf" style="display:none"></div>
<div id="g" style="display:none"></div>
<div class="shw" id="shw"><div class="shb" id="sh"></div></div>
<div class="toast" id="toast"></div>
<div class="tabs" id="tabs"><a data-t="dash" class="on"><i>👤</i>پروفایل</a><a data-t="games"><i>🎮</i>بازی‌ها</a><a data-t="self"><i>⚡</i>سلف</a></div>
<script>
const SNK=__SNK__;
const tg=window.Telegram&&Telegram.WebApp;if(tg){tg.ready();tg.expand();try{tg.setHeaderColor("#090d1b");tg.setBackgroundColor("#090d1b");tg.setBottomBarColor&&tg.setBottomBarColor("#0e1328")}catch(e){}}
const init=tg?tg.initData:"";
const $=id=>document.getElementById(id);
const fa=s=>String(s).replace(/\d/g,d=>"۰۱۲۳۴۵۶۷۸۹"[d]).replace(".","٫");
const num=n=>fa(Number(n).toLocaleString("en"));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function span(h){const d=Math.floor(h/24),x=h%24;return (d?fa(d)+" روز و ":"")+fa(x)+" ساعت"}
const E={low_balance:"الماس کافی نداری؛ اول شارژ کن.",not_member:"اول باید عضو کانال‌ها بشی (داخل ربات).",rate:"کمی صبر کن و دوباره امتحان کن.",auth:"نشست منقضی شده؛ مینی‌اپ رو دوباره باز کن."};
async function api(p,b){const r=await fetch(p,{method:"POST",headers:{"Authorization":"tma "+init,"Content-Type":"application/json"},body:JSON.stringify(b||{})});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(E[j.error]||("خطا در ارتباط با سرور ("+r.status+")"));return j}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function haptic(k){try{tg&&tg.HapticFeedback&&(k==="light"?tg.HapticFeedback.impactOccurred("light"):tg.HapticFeedback.notificationOccurred(k))}catch(e){}}
let _tt=null;
function toast(t,ms){const e=$("toast");e.textContent=t;e.style.display="block";clearTimeout(_tt);_tt=setTimeout(()=>e.style.display="none",ms||2600)}


/* ---------- آیکن‌های ویژه به‌جای ایموجی معمولی ---------- */
const IC={
chart:'<path d="M5 20v-7M12 20V5M19 20v-11"/>',
user:'<circle cx="12" cy="8" r="4"/><path d="M4 21c.6-4 4-6 8-6s7.4 2 8 6"/>',
users:'<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.5-3.5 3.2-5.5 6.5-5.5s6 2 6.5 5.5"/><path d="M16 4.7a3.5 3.5 0 010 6.6M18 14.8c2 .6 3.3 2.4 3.5 5.2"/>',
gamepad:'<rect x="2.5" y="7" width="19" height="11" rx="5"/><path d="M8 10.5v4M6 12.5h4"/><circle cx="15.5" cy="11.5" r=".9"/><circle cx="18" cy="14" r=".9"/>',
bolt:'<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
gem:'<path d="M6 3h12l4 6-10 12L2 9z"/><path d="M2 9h20M9 3l3 6 3-6M12 21L9 9M12 21l3-12"/>',
gift:'<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M5 12v8a1 1 0 001 1h12a1 1 0 001-1v-8M8 8a2.5 2.5 0 110-5c2 0 4 2.5 4 5 0-2.5 2-5 4-5a2.5 2.5 0 110 5"/>',
link:'<path d="M10 14a4.5 4.5 0 006.4 0l3-3a4.5 4.5 0 00-6.4-6.4l-1 1M14 10a4.5 4.5 0 00-6.4 0l-3 3a4.5 4.5 0 006.4 6.4l1-1"/>',
plug:'<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 01-12 0zM12 17v5"/>',
check:'<circle cx="12" cy="12" r="9.5"/><path d="M7.5 12.5l3 3 6-7"/>',
alert:'<path d="M12 3L2 20h20z"/><path d="M12 10v5M12 17.8v.2"/>',
ban:'<circle cx="12" cy="12" r="9.5"/><path d="M5.5 5.5l13 13"/>',
lock:'<rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 018 0v2.5"/>',
clock:'<circle cx="12" cy="12" r="9.5"/><path d="M12 7v5l3.5 2"/>',
hourglass:'<path d="M6 2.5h12M6 21.5h12M7 2.5c0 5 5 6 5 9.5s-5 4.5-5 9.5M17 2.5c0 5-5 6-5 9.5s5 4.5 5 9.5"/>',
id:'<rect x="2.5" y="5" width="19" height="14" rx="3"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16c.5-1.7 1.7-2.5 3-2.5s2.5.8 3 2.5M14 10h5M14 14h4"/>',
at:'<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 006 0v-1a10 10 0 10-4 8"/>',
plus:'<path d="M12 5v14M5 12h14"/>',
trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
edit:'<path d="M4 20l1-4L16.5 4.5a2.1 2.1 0 013 3L8 19z"/>',
speaker:'<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="M16.5 9a4 4 0 010 6M19 6.5a7.5 7.5 0 010 11"/>',
mute:'<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="M17 9.5l5 5M22 9.5l-5 5"/>',
chat:'<path d="M4 5h16a1.5 1.5 0 011.5 1.5v9A1.5 1.5 0 0120 17h-8l-5 4v-4H4a1.5 1.5 0 01-1.5-1.5v-9A1.5 1.5 0 014 5z"/>',
bot:'<rect x="4" y="8" width="16" height="12" rx="4"/><path d="M12 8V4M9 14v1M15 14v1M2 13v3M22 13v3"/>',
dice:'<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><circle cx="8.5" cy="8.5" r="1"/><circle cx="15.5" cy="8.5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="8.5" cy="15.5" r="1"/><circle cx="15.5" cy="15.5" r="1"/>',
palette:'<path d="M12 3a9 9 0 100 18c1.5 0 2-1 1.5-2.2-.6-1.3.2-2.3 1.6-2.3H17a4 4 0 004-4c0-5-4-9.5-9-9.5z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
snake:'<path d="M5 20c-2-3 0-6 4-6s5-3 3-6 1-5 5-4M17 4l2.5 1.5L17 7"/>',
ladder:'<path d="M7 3v18M17 3v18M7 7h10M7 12h10M7 17h10"/>',
trophy:'<path d="M8 4h8v6a4 4 0 01-8 0zM8 6H4v1a4 4 0 004 4M16 6h4v1a4 4 0 01-4 4M12 14v4M8 21h8M10 18h4"/>',
flag:'<path d="M5 21V4M5 4h12l-2 4 2 4H5"/>',
target:'<circle cx="12" cy="12" r="9.5"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>',
shield:'<path d="M12 2.5l8 3v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10v-6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
search:'<circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/>',
note:'<path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7"/>',
mega:'<path d="M3 10v4h3l9 5V5L6 10zM18.5 9a4 4 0 010 6"/>',
font:'<path d="M5 20l7-16 7 16M8 14h8"/>',
eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
gear:'<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1"/>',
brief:'<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M9 7V4.5h6V7M3 13h18"/>',
book:'<path d="M4 21.5V4.5A2.5 2.5 0 016.5 2H20v17H6.5A2.5 2.5 0 004 21.5z"/>',
pin:'<path d="M12 21s7-6.2 7-11.5A7 7 0 005 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
x:'<circle cx="12" cy="12" r="9.5"/><path d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>',
dot:'<circle cx="12" cy="12" r="5"/>',
heart:'<path d="M12 21s-8.5-5.2-8.5-11A4.8 4.8 0 0112 7a4.8 4.8 0 018.5 3c0 5.8-8.5 11-8.5 11z"/>',
wallet:'<path d="M3 7.5A2.5 2.5 0 015.5 5H19v14H5.5A2.5 2.5 0 013 16.5z"/><path d="M19 9h2v6h-2a3 3 0 010-6z"/>',
power:'<path d="M12 3v9M6.5 6.5a8 8 0 1011 0"/>',
flame:'<path d="M12 22c4 0 7-2.8 7-7 0-3-2-5-3.5-7-.4 2-1.5 3-3 3 0-3-1.5-5.5-4-7 0 3-3.5 5-3.5 10 0 4.2 3 8 7 8z"/>'
};
const EMAP={"📊":"chart","👤":"user","🎮":"gamepad","⚡":"bolt","💎":"gem","🎁":"gift","🔗":"link","🔌":"plug","✅":"check","⚠️":"alert","⚠":"alert",
"⛔":"ban","🔒":"lock","🕒":"clock","⏳":"hourglass","⏹":"power","🆔":"id","👥":"users","➕":"plus","🗑":"trash","✏️":"edit","✏":"edit","🔊":"speaker",
"🔇":"mute","💬":"chat","🤖":"bot","🎲":"dice","🎨":"palette","🐍":"snake","🪜":"ladder","🏆":"trophy","🏁":"flag","🎯":"target","🛡":"shield",
"🔎":"search","📝":"note","📢":"mega","📣":"mega","🔤":"font","👀":"eye","🛠":"gear","🧰":"gear","⚙️":"gear","⚙":"gear","💼":"brief","📚":"book",
"📍":"pin","❌":"x","🟢":"dot","💕":"heart","🔥":"flame","💰":"wallet"};
function icon(n){return IC[n]?`<svg class="ico" viewBox="0 0 24 24" stroke="url(#gr)" aria-hidden="true">${IC[n]}</svg>`:""}
const EKEYS=Object.keys(EMAP).sort((a,b)=>b.length-a.length);
const ERT=new RegExp(EKEYS.join("|")),ERE=new RegExp(EKEYS.join("|"),"g");
const ESKIP="textarea,input,script,style,svg,.sm1,.noem";
function swapNode(tn){
 const v=tn.nodeValue;if(!v||!ERT.test(v))return;
 const p=tn.parentNode;if(!p||(p.closest&&p.closest(ESKIP)))return;
 const f=document.createDocumentFragment();let last=0,m;ERE.lastIndex=0;
 while((m=ERE.exec(v))){if(m.index>last)f.appendChild(document.createTextNode(v.slice(last,m.index)));
  const s=document.createElement("span");s.className="ei";s.innerHTML=icon(EMAP[m[0]]);f.appendChild(s);last=m.index+m[0].length}
 if(last<v.length)f.appendChild(document.createTextNode(v.slice(last)));
 p.replaceChild(f,tn)}
function swapScan(root){const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),a=[];let n;while((n=w.nextNode()))a.push(n);a.forEach(swapNode)}
let _swq=0;const _mo=new MutationObserver(()=>{if(_swq)return;_swq=requestAnimationFrame(()=>{_swq=0;_mo.disconnect();try{swapScan(document.body)}catch(e){}_mo.observe(document.body,{childList:true,subtree:true,characterData:true})})});
try{swapScan(document.body)}catch(e){}_mo.observe(document.body,{childList:true,subtree:true,characterData:true});
function cp(v){try{navigator.clipboard&&navigator.clipboard.writeText(String(v))}catch(e){}haptic("success");toast("کپی شد ✅")}

/* ---------- تب‌ها ---------- */
let ME=null,VIEW="dash",busy=false;
const TT={dash:"👤 پروفایل",games:"🎮 بازی‌ها",self:"⚡ سلف"};
const PANES={dash:"v",games:"gl",self:"sf"};
function showTab(t){VIEW=t;document.body.classList.remove("ing");$("g").style.display="none";
 for(const k in PANES)$(PANES[k]).style.display=(k===t)?"block":"none";
 document.querySelectorAll(".tabs a").forEach(a=>a.classList.toggle("on",a.dataset.t===t));
 $("ttl").textContent=TT[t];$("ttl").style.display="block"}
document.querySelectorAll(".tabs a").forEach(a=>a.onclick=()=>{if(a.dataset.t!==VIEW||$("g").style.display==="block"){stopGame();showTab(a.dataset.t)}});
function msg(t){document.querySelectorAll(".nm").forEach(m=>{m.textContent=t;m.style.display=t?"block":"none"})}

/* ---------- پروفایل ---------- */
function renderAll(s,note){ME=s;renderDash(s,note);renderGames(s);renderSelf(s,note)}
function noteBox(note){return `<div class="msg nm" style="display:${note?"block":"none"}">${note||""}</div>`}
function toggleBtn(s){return `<button class="tgl ${s.on?"red":""}">${s.on?"⏹ خاموش کردن سلف":"⚡ روشن کردن سلف ("+fa(s.activate_cost)+" الماس)"}</button>`}
function bindTgl(){document.querySelectorAll(".tgl").forEach(b=>b.onclick=()=>flip())}
function pcard(ic,l,v,cls,extra){return `<div class="pc ${cls||""}" ${extra||""}><div class="pi">${icon(ic)}</div><div class="pt"><span>${l}</span><b>${v}</b></div></div>`}
function renderDash(s,note){
 const warn=s.on&&s.hours<24?`<div class="msg">⚠️ شارژت کمتر از ${fa(Math.max(s.hours,1))} ساعت کافیه.</div>`:"";
 const conn=s.on&&!s.connected?`<div class="msg">اکانتت هنوز وصل نشده؛ از داخل ربات «ورود به اکانت» رو بزن.</div>`:"";
 const nm=s.name||(s.username?"@"+s.username:"کاربر");
 const ch=(String(nm).replace(/^@/,"").trim()[0]||"U").toUpperCase();
 const un=s.username?"@"+s.username:"ندارد";
 const left=(s.days_left>0?fa(s.days_left)+" روز و ":"")+fa(s.hours_left)+" ساعت";
 const pct=Math.max(3,Math.min(100,Math.round(s.hours/7.2)));
 const trial=s.trial_days_left>0?`<small>🎁 هدیهٔ رایگان: ${fa(s.trial_days_left)} روز دیگه فعاله</small>`:"";
 $("v").className="";
 $("v").innerHTML=`
 <div class="phero"><div class="pav ${s.on?"on":""}"><span>${esc(ch)}</span></div><h2>${esc(nm)}</h2>
  <div class="chips"><span class="chip ${s.on?"g":"r"}">${s.on?"⚡ سلف روشن":"⏹ سلف خاموش"}</span>
  <span class="chip ${s.connected?"g":"y"}">${s.connected?"🔌 اکانت وصل":"🔌 اکانت وصل نیست"}</span></div></div>
 <div class="pgrid">
  ${pcard("gem","موجودی الماس",fa(s.balance),"hl")}
  ${pcard("flame","الماس مصرف‌شده",fa(s.spent))}
  ${pcard("wallet","موجودی تومانی",num(s.toman)+" تومان")}
  ${pcard("users","تعداد رفرال",fa(s.referrals))}
  ${pcard("at","یوزرنیم",`<span class="id">${esc(un)}</span>`,s.username?"cp":"",s.username?`onclick="cp('${esc(s.username)}')"`:"")}
  ${pcard("id","آیدی عددی",`<span class="id">${s.id}</span>`,"cp",`onclick="cp('${s.id}')"`)}
 </div>
 <div class="ptime"><div class="top2"><div class="pi">${icon("hourglass")}</div><div class="pt"><span>باقی‌مانده</span><b>${left}</b></div></div>
  <div class="bar"><i style="width:${pct}%"></i></div>
  <small>با کسر ${fa(s.rate)} الماس در ساعت (${s.on?"سلف روشنه":"سلف خاموشه"})</small>${trial}</div>
 ${warn}${conn}
 ${toggleBtn(s)}
 <button class="alt" id="b">💎 خرید الماس / بازگشت به ربات</button>
 ${noteBox(note)}`;
 $("b").onclick=()=>tg&&tg.close();bindTgl();
}
async function flip(){
 if(busy||!ME)return;const s=ME;
 const go=async()=>{busy=true;document.querySelectorAll(".tgl").forEach(b=>b.disabled=true);
  try{const r=await api("/api/toggle",{on:!s.on});renderAll(r,r.error?E[r.error]:(r.on?"✅ سلف روشن شد.":"⏹ سلف خاموش شد."));}
  catch(e){msg(e.message)}finally{busy=false;document.querySelectorAll(".tgl").forEach(b=>b.disabled=false)}};
 if(s.on&&tg&&tg.showConfirm){tg.showConfirm("سلف خاموش بشه؟",ok=>ok&&go())}else go();
}

/* ---------- تب سلف (پنل کامل) ---------- */
let SP=null,SPON=null,SBUSY=false;const OPEN=new Set(["slots"]);
const SE={not_allowed:"سلف برای حسابت فعال نیست.",not_connected:"اول اکانتت رو وصل کن.",need_target:"اول گپ تایپینگ رو انتخاب کن.",stale:"لیست قدیمی شده؛ دوباره باز کن.",tg_error:"تلگرام جواب نداد؛ دوباره امتحان کن.",bad_request:"ورودی نامعتبره.",server:"خطای سرور؛ کمی بعد دوباره امتحان کن.",rate:"کمی صبر کن.",banned_word:"⛔ این کلمه مجاز نیست.",feat_denied:"این قابلیت برای حسابت فعال نیست."};
const TGD={
 presence:[["ft","always_online","🟢","همیشه آنلاین","وضعیت آنلاین هر ۲ دقیقه تازه می‌شه"],
           ["ft","typing_mode","⌨️","تایپینگ نمایشی","وقتی کسی تایپ می‌کنه، تو هم توی گپ انتخابی «در حال تایپ» نشون می‌دی"]],
 pv:[["ft","pv_lock","🔒","قفل پیوی","پیام‌های ورودی پیوی خودکار پاک می‌شن","dg"],
     ["ft","pv_join","📢","عضویت اجباری پیوی","طرف مقابل باید اول عضو کانال‌ها بشه"],
     ["mt","ar_on","💬","پاسخ خودکار پیوی","به هر نفر حداکثر هر ۶ ساعت یک‌بار"],
     ["mt","del_on","🗑","خبر حذف/ویرایش پیوی","پیام‌هایی که پاک یا ویرایش شدن بهت خبر داده می‌شه"],
     ["mt","ttl_on","⏳","ذخیرهٔ مدیای تایمردار","عکس و ویدیوی خودتخریب‌شونده ذخیره می‌شه"]],
 watch:[["mt","mn_on","📣","خبر منشن در گروه‌ها","هر بار منشن بشی بهت اطلاع می‌ده"]]
};
const sw=(kind,k,ic,ti,sub,on,cls)=>`<div class="sr" data-sw="${kind}:${k}"><span style="font-size:20px">${ic}</span><div class="t">${ti}<small>${sub||""}</small></div><span class="sw ${cls||""} ${on?"on":""}"></span></div>`;
const swT=t=>sw(t[0],t[1],t[2],t[3],t[4],SP.toggles[t[1]],t[5]);
const ac=(id,ic,ti,sub,bd,body)=>`<div class="ac ${OPEN.has(id)?"open":""}" data-id="${id}"><div class="ach"><span class="ic">${ic}</span><b>${ti}<small>${sub}</small></b>${bd}<span class="cv">‹</span></div><div class="acb">${body}</div></div>`;
const bdg=(t,on)=>`<span class="bd ${on?"on":""}">${t}</span>`;
const onoff=v=>bdg(v?"روشن":"خاموش",v);

async function loadSP(){try{SP=await api("/api/self/state")}catch(e){if(!SP){const x=$("spx");if(x)x.innerHTML=`<div class="msg">${esc(e.message)}</div>`;return}}spRender()}

function renderSelf(s,note){
 $("sf").innerHTML=`<div class="shero shine"><div class="ring ${s.on?"on":""}"><i>⚡</i></div>
 <h2>${s.on?"سلف روشنه":"سلف خاموشه"}</h2>
 <p>${s.on?"ساعتی "+fa(s.rate)+" الماس کسر می‌شه":"با روشن‌کردن، همهٔ تنظیمات پایین باز می‌شن ("+fa(s.activate_cost)+" الماس)"}</p>
 <div class="chips"><span class="chip ${s.connected?"g":"r"}">🔗 ${s.connected?"اکانت وصله":"اکانت وصل نیست"}</span><span class="chip">💎 ${fa(s.balance)}</span><span class="chip">⏳ ${span(s.hours)}</span></div>
 ${toggleBtn(s)}</div><div id="spx"></div>${noteBox(note)}`;
 bindTgl();
 if(SP===null||SPON!==s.on){SPON=s.on;loadSP();if(s.on)setTimeout(loadSP,4000)}else spRender();
}

function spRender(){
 const el=$("spx");if(!el)return;
 if(!SP){el.innerHTML=`<div class="msg">در حال بارگذاری پنل…</div>`;return}
 if(!SP.allowed){el.innerHTML=`<div class="msg">🔒 پنل سلف بعد از روشن‌شدن سلف باز می‌شه. اول با دکمهٔ بالا روشنش کن.</div>`;return}
 const T=SP.toggles,sl=SP.slots,rd=sl.filter(x=>x.ready).length,H=[];
 if(!SP.configured)H.push(`<div class="msg">⚠️ سلف هنوز روی سرور راه‌اندازی نشده (SELF_API_ID / SELF_API_HASH).</div>`);
 else if(!SP.connected)H.push(`<div class="msg" style="border-color:rgba(255,210,77,.4)">🔌 اکانتت الان وصل نیست؛ تنظیمات ذخیره می‌شن ولی کار نمی‌کنن.<br><span class="mini p" data-do="conn" style="margin-top:8px">🔗 اتصال اکانت (داخل ربات)</span></div>`);
 // اسلات‌ها
 H.push(ac("slots","🎮","اسلات‌های بازی","کلمه + گپ + تایمر + دکمه؛ خودش می‌گه و می‌زنه",bdg(fa(rd)+"/"+fa(sl.length)+" آماده",rd>0),
  sl.map(s=>`<div class="slc ${s.ready?"rdy":""}"><div class="slh"><span class="n">${fa(s.i+1)}</span><b>${s.word?esc(s.word):"اسلات خالی"}</b>${s.ready?'<span class="chip g">آماده ✅</span>':(s.word||s.has_chat)?'<span class="chip y">ناقص</span>':""}</div>
  <div class="slg"><div class="${s.word?"":"no"}" data-do="slot:word:${s.i}">📝 کلمه<small>${s.word?esc(s.word):"تنظیم کن"}</small></div>
  <div class="${s.has_chat?"":"no"}" data-do="slot:chat:${s.i}">💬 گپ<small>${s.has_chat?esc(s.title):"انتخاب کن"}</small></div>
  <div class="${s.delay?"":"no"}" data-do="slot:delay:${s.i}">⏱ تایمر<small>${s.delay?"هر "+esc(fa(s.delay_txt)):"خاموش"}</small></div>
  <div data-do="slot:pos:${s.i}">🎯 دکمه<small>${esc(SP.pos[s.pos]||"")}</small></div></div>
  ${(s.word||s.has_chat)?`<span class="mini d" data-do="slot:clr:${s.i}">🗑 پاک‌کردن</span>`:""}</div>`).join("")
  +`<div class="hint">تایمر حداقل ${fa(SP.min_auto)} ثانیه. وقتی هر چهار مورد پر باشه اسلات «آماده» می‌شه و خودکار کار می‌کنه.</div>`));
 // ساعت
 const cf=SP.clock.fonts;
 H.push(ac("clock","🕒","ساعت زنده","ساعت کنار اسم پروفایلت، هر دقیقه تازه می‌شه",onoff(SP.clock.on),
  `<div class="sr" data-do="clk"><span style="font-size:20px">🕒</span><div class="t">نمایش ساعت کنار اسم<small>اسم اصلیت موقع خاموش‌کردن برمی‌گرده</small></div><span class="sw ${SP.clock.on?"on":""}"></span></div>
  <div class="fg">${cf.map(f=>`<div class="fc ${SP.clock.font===f.i&&SP.clock.on?"on":""}" data-do="clkf:${f.i}"><div class="sm1">${esc(f.sample)}</div><small>${esc(f.name)}</small></div>`).join("")}</div>`));
 // متن
 const X=SP.text;
 H.push(ac("text","🔤","حالت متن","فونت پیام‌هایی که می‌فرستی + افکت تایپ",bdg(esc(X.mode)+(X.fx?" + تایپ":""),X.mode!=="ساده"||X.fx),
  `<div class="fg">${X.fonts.map(f=>`<div class="fc ${f.n===X.mode?"on":""}" data-fn="${esc(f.n)}"><div class="sm1 ${f.tag?"f-"+f.tag:""}">${esc(f.s)}</div><small>${esc(f.n)}</small></div>`).join("")}</div>
  <div style="height:8px"></div>${sw("do","tfx","⌨️","تایپ حرف‌به‌حرف","پیام‌های کوتاه با افکت تایپ ادیت می‌شن",X.fx)}
  <div class="seg">${["1","2","3"].map(k=>`<a class="${String(X.speed)===k?"on":""}" data-do="tsp:${k}">${esc(X.speeds[k])}</a>`).join("")}</div>
  <div class="hint">دستورهای نقطه‌دار (مثل .پنل) دست نمی‌خورن.</div>`));
 // حضور
 const ty=SP.typing;
 H.push(ac("presence","🟢","حضور و تایپینگ","آنلاین بودن و تایپ نمایشی",onoff(T.always_online||T.typing_mode),
  TGD.presence.map(swT).join("")
  +`<div class="sr" style="cursor:default"><span style="font-size:20px">🎯</span><div class="t">گپ تایپینگ<small>${ty.has_target?esc(ty.target):"هنوز انتخاب نشده"}</small></div></div>
  <span class="mini p" data-do="typing">${ty.has_target?"تغییر گپ":"انتخاب گپ"}</span>${ty.has_target?`<span class="mini d" data-do="typclr">حذف مقصد</span>`:""}`));
 // پیوی
 H.push(ac("pv","🛡","پیوی و پاسخ خودکار","قفل، عضویت اجباری، پاسخ خودکار و اعلان‌ها",onoff(T.pv_lock||T.pv_join||T.ar_on),
  TGD.pv.map(swT).join("")
  +`<div class="hint" style="white-space:pre-wrap">متن پاسخ خودکار:\n${esc(SP.ar_text)}</div><span class="mini p" data-do="artext">✏️ ویرایش متن</span>`));
 // نظارت
 H.push(ac("watch","👀","نظارت و سکوت","منشن‌گیر، ناظر کلمه/آیدی، سکوت پیوی",onoff(T.mn_on||T.wl_on||T.mu_on),
  TGD.watch.map(swT).join("")
  +sw("mt","wl_on","🔎","ناظر کلمه/آیدی ("+fa(SP.wl.length)+")","هر جا دیده بشن بهت خبر می‌ده",T.wl_on)
  +`<div>${SP.wl.map((x,i)=>`<span class="tag">${esc(x)}<u data-do="wldel:${i}">×</u></span>`).join("")}</div>${SP.wl.length<SP.wl_max?`<span class="mini p" data-do="wladd">➕ افزودن مورد</span>`:""}
  `+sw("mt","mu_on","🔇","سکوت پیوی ("+fa(SP.mu.length)+" نفر)","پیام‌های جدیدشون توی پیوی از سمت تو پاک می‌شه",T.mu_on)
  +`<div>${SP.mu.map(x=>`<span class="tag">${esc(x.name)}<u data-do="mudel:${esc(x.id)}">×</u></span>`).join("")}</div>${SP.mu.length<SP.mu_max?`<span class="mini p" data-do="muadd">➕ افزودن کاربر</span>`:""}`));
 // دوست/دشمن
 const M=SP.modes,mrow=(m,ic,ti)=>sw("mode",m,ic,ti,M[m].n?fa(M[m].n)+" نفر: "+esc(M[m].names.join("، ")):"کسی اضافه نشده",M[m].on,m==="enemy"?"dg":"")+(M[m].n?`<span class="mini d" data-do="mode:clr:${m}">🗑 پاک‌کردن افراد ${ti.replace("حالت ","")}</span>`:"");
 H.push(ac("modes","💕","حالت دوست / دشمن","جواب چرخشی خودکار به افراد انتخابی",onoff(M.friend.on||M.enemy.on),
  mrow("friend","💕","حالت دوست")+mrow("enemy","😈","حالت دشمن")
  +`<div class="hint">برای افزودن آدم: توی چت روی پیامش ریپلای کن و بنویس <b>.افزودن دوست</b> یا <b>.افزودن دشمن</b> (حداکثر ${fa(M.friend.max)} نفر).</div>`));
 // کار / درس
 const fk=Object.keys(SP.feats).filter(k=>SP.feats[k].allowed);
 if(fk.length)H.push(ac("feats","🛠","کار و درس","قابلیت‌های ویژه‌ای که پشتیبانی برات فعال کرده",bdg(fk.some(k=>SP.feats[k].active)?"روشن":"خاموش",fk.some(k=>SP.feats[k].active)),
  fk.map(k=>{const f=SP.feats[k];return sw("feat",k,k==="work"?"💼":"📚",f.label,f.active?"روشن توی «"+esc(f.title)+"» — هر "+esc(f.every):"هر "+esc(f.every)+" "+esc(f.what),f.active)}).join("")
  +`<div class="hint">فقط توی یک گپ می‌شه فعالش کرد. با روشن‌کردن، گپ هدف رو انتخاب می‌کنی.</div>`));
 // ابزارها
 H.push(ac("tools","🧰","ابزارها و دستورها","راهنمای همهٔ دستورهای نقطه‌دار",bdg(fa(SP.tools.length+1)+" مورد"),
  `<div class="guide"><b>😂 میم</b><div class="gbody">${SP.meme.replace(/\n/g,"<br>")}</div></div>`
  +SP.tools.map(t=>`<div class="guide"><b>${esc(t.t)}</b><div class="gbody">${t.b.replace(/\n/g,"<br>")}</div></div>`).join("")));
 // حساب
 H.push(ac("acct","⚙️","حساب و اتصال","توقف موقت، پینگ، قطع اتصال",bdg(SP.connected?"وصل":"قطع",SP.connected),
  sw("do","pause","▶️","سلف فعال","با خاموش‌کردن، فقط کلمه‌های خودکار متوقف می‌شن",!SP.paused)
  +`<span class="mini p" data-do="ping">🏓 پینگ</span>`+(SP.connected?`<span class="mini d" data-do="disc">🔌 قطع اتصال</span>`:`<span class="mini p" data-do="conn">🔗 اتصال اکانت</span>`)));
 el.innerHTML=H.join("");
}

/* ---------- عملیات ---------- */
async function sact(body){
 if(SBUSY)return;SBUSY=true;
 try{const r=await api("/api/self/act",body);if(r.state)SP=r.state;spRender();
  if(r.ok){haptic("success");if(r.msg)toast(r.msg)}
  else{haptic("error");toast(r.msg||SE[r.error]||"خطا");if(r.error==="need_target")pickTyping()}}
 catch(e){toast(e.message);loadSP()}
 finally{SBUSY=false}}
function swFlip(v){
 const [kind,k]=v.split(":");
 if(kind==="do"){if(k==="tfx"){SP.text.fx=!SP.text.fx;spRender();sact({a:"type_fx",on:SP.text.fx})}
  else if(k==="pause"){sact({a:"pause",paused:!SP.paused})}return}
 if(kind==="feat"){const f=SP.feats[k];if(f.active){sact({a:"feat",k,on:false})}else pickFeat(k);return}
 if(kind==="mode"){const on=!SP.modes[k].on;SP.modes[k].on=on;spRender();sact({a:"mode",m:k,on});return}
 const on=!SP.toggles[k];
 if(k==="typing_mode"&&on&&!SP.typing.has_target){pickTyping();return}
 SP.toggles[k]=on;spRender();sact({a:kind,k,on})}
function sheetOpen(h){$("sh").innerHTML=h;$("shw").classList.add("on")}
function sheetClose(){$("shw").classList.remove("on");$("sh").innerHTML=""}
function askText(o){return new Promise(res=>{
 sheetOpen(`<h3>${o.title}</h3>${o.hint?`<div class="hint" style="margin:0 0 8px">${o.hint}</div>`:""}${o.multi?`<textarea id="ti" maxlength="${o.max||500}">${esc(o.value||"")}</textarea>`:`<input id="ti" maxlength="${o.max||40}" value="${esc(o.value||"")}">`}<button id="tok" style="margin-top:12px">ذخیره</button><button class="alt" id="tno">انصراف</button>`);
 $("tok").onclick=()=>{const v=$("ti").value.trim();sheetClose();res(v||null)};$("tno").onclick=()=>{sheetClose();res(null)};setTimeout(()=>{const t=$("ti");t&&t.focus()},260)})}
function pickChat(kind,title){return new Promise(res=>{
 if(!SP.connected){toast(SE.not_connected);res(null);return}
 sheetOpen(`<h3>${title}</h3><input id="cq" placeholder="جستجوی اسم گپ…"><div id="cl" style="margin-top:6px"><div class="hint">در حال دریافت از تلگرام…</div></div>`);
 let t=null;
 const load=async()=>{try{const r=await api("/api/self/dialogs",{kind,q:$("cq").value});
  if(!r.ok){$("cl").innerHTML=`<div class="msg">${esc(SE[r.error]||"خطا")}</div>`;return}
  $("cl").innerHTML=r.items.length?r.items.map(x=>`<div class="opt" data-id="${x.id}"><span>${x.k==="group"?"👥":x.k==="bot"?"🤖":"👤"}</span>${esc(x.t)}</div>`).join("")+(r.total>r.items.length?`<div class="hint">برای دیدن بقیه جستجو کن (${fa(r.total)} گپ)</div>`:""):`<div class="msg">گپی پیدا نشد.</div>`;
  $("cl").querySelectorAll(".opt").forEach(o=>o.onclick=()=>{sheetClose();res(o.dataset.id)})}
  catch(e){const c=$("cl");if(c)c.innerHTML=`<div class="msg">${esc(e.message)}</div>`}};
 $("cq").oninput=()=>{clearTimeout(t);t=setTimeout(load,400)};load()})}
async function pickTyping(){const id=await pickChat("typing","⌨️ تایپینگ توی کدوم گپ نشون داده بشه؟");if(id)sact({a:"typing_set",id})}
async function pickFeat(k){const id=await pickChat("feat","📍 «"+SP.feats[k].label+"» توی کدوم گپ فعال بشه؟");if(id)sact({a:"feat",k,on:true,id})}
function delaySheet(i){
 const cur=SP.slots[i].delay,pre=[[0,"خاموش"],[5,"۵ ثانیه"],[10,"۱۰ ثانیه"],[30,"۳۰ ثانیه"],[60,"۱ دقیقه"],[300,"۵ دقیقه"],[600,"۱۰ دقیقه"],[1800,"۳۰ دقیقه"]];
 sheetOpen(`<h3>⏱ هر چند ثانیه خودکار بگه؟</h3><div class="chipsel">${pre.map(p=>`<a class="${cur===p[0]?"on":""}" data-v="${p[0]}">${p[1]}</a>`).join("")}</div>
 <div class="hint">یا عدد دلخواه (ثانیه، حداقل ${fa(SP.min_auto)}؛ مثل «۴۵» یا «۲ دقیقه»):</div><input id="dv" inputmode="numeric" placeholder="مثلاً 45"><button id="dok" style="margin-top:10px">ثبت</button>`);
 $("sh").querySelectorAll(".chipsel a").forEach(a=>a.onclick=()=>{sheetClose();sact({a:"slot",i,f:"delay",v:parseInt(a.dataset.v,10)})});
 $("dok").onclick=()=>{const v=$("dv").value.trim();if(!v)return;sheetClose();sact({a:"slot",i,f:"delay",v})}}
function posSheet(i){
 const cur=SP.slots[i].pos;
 sheetOpen(`<h3>🎯 کدوم دکمهٔ پیام ربات زده بشه؟</h3>${["l","m","r","n"].map(k=>`<div class="opt ${cur===k?"on":""}" data-v="${k}">${esc(SP.pos[k])}</div>`).join("")}`);
 $("sh").querySelectorAll(".opt").forEach(o=>o.onclick=()=>{sheetClose();sact({a:"slot",i,f:"pos",v:o.dataset.v})})}
async function spDo(c){
 const p=c.split(":");
 switch(p[0]){
  case"ping":sact({a:"ping"});break;
  case"conn":{if(!SP.bot){toast("آدرس ربات پیدا نشد؛ توی ربات /self رو بزن.");break}
   try{tg.openTelegramLink("https://t.me/"+SP.bot+"?start=self");tg.close()}catch(e){toast("توی ربات /self رو بزن.")}break}
  case"disc":{const go=()=>sact({a:"disconnect"});if(tg&&tg.showConfirm)tg.showConfirm("اتصال قطع بشه و سشن اکانتت کامل بسته بشه؟",ok=>ok&&go());else go();break}
  case"clk":sact({a:"clock",on:!SP.clock.on});break;
  case"clkf":sact({a:"clock_font",font:parseInt(p[1],10)});break;
  case"tsp":sact({a:"type_speed",v:parseInt(p[1],10)});break;
  case"typing":pickTyping();break;
  case"typclr":sact({a:"typing_clear"});break;
  case"artext":{const v=await askText({title:"✏️ متن پاسخ خودکار",hint:"به کسی که توی پیوی پیام می‌ده فرستاده می‌شه (حداکثر ۵۰۰ حرف).",value:SP.ar_text,multi:true,max:500});if(v)sact({a:"ar_text",v});break}
  case"wladd":{const v=await askText({title:"➕ ناظر: کلمه، @یوزرنیم یا آیدی",hint:"چندتا رو با کاما یا خط جدید جدا کن.",multi:true,max:300});if(v)sact({a:"wl_add",v});break}
  case"wldel":sact({a:"wl_del",i:parseInt(p[1],10)});break;
  case"muadd":{const v=await askText({title:"🔇 سکوت: آیدی عددی یا @یوزرنیم",hint:"چندتا رو با کاما یا خط جدید جدا کن. برای @یوزرنیم اکانت باید وصل باشه.",multi:true,max:300});if(v)sact({a:"mu_add",v});break}
  case"mudel":sact({a:"mu_del",id:p[1]});break;
  case"mode":if(p[1]==="clr")sact({a:"mode",m:p[2],clear:true});break;
  case"slot":{const i=parseInt(p[2],10);
   if(p[1]==="word"){const v=await askText({title:"📝 کلمهٔ اسلات "+fa(i+1),hint:"همین کلمه توی گپ انتخابی گفته می‌شه.",value:SP.slots[i].word,max:40});if(v)sact({a:"slot",i,f:"word",v})}
   else if(p[1]==="chat"){const id=await pickChat("slot","💬 کلمه توی کدوم گپ گفته بشه؟");if(id)sact({a:"slot_chat",i,id})}
   else if(p[1]==="delay")delaySheet(i);
   else if(p[1]==="pos")posSheet(i);
   else if(p[1]==="clr")sact({a:"slot_clear",i});
   break}
 }}
document.addEventListener("click",e=>{
 if(e.target===$("shw")){sheetClose();return}
 if(!e.target.closest("#spx")||!SP)return;
 const h=e.target.closest(".ach");if(h){const a=h.parentNode,id=a.dataset.id;OPEN.has(id)?OPEN.delete(id):OPEN.add(id);a.classList.toggle("open");return}
 const g=e.target.closest(".guide");if(g){g.classList.toggle("open");return}
 const f=e.target.closest("[data-fn]");if(f){sact({a:"font_mode",n:f.dataset.fn});return}
 const s=e.target.closest("[data-sw]");if(s){swFlip(s.dataset.sw);return}
 const d=e.target.closest("[data-do]");if(d)spDo(d.dataset.do)});

/* ---------- تب بازی‌ها ---------- */
function renderGames(s){
 const dcont=s.draw?`<button id="dcont">▶️ ادامه نقاشی (${s.draw.status==="lobby"?"در حال جمع‌شدن بازیکن":"در جریان"})</button>`:"";
 const cont=s.game?`<button id="gcont">▶️ ادامه بازی (${s.game.status==="lobby"?"در حال جمع‌شدن بازیکن":"در جریان"})</button>`:"";
 $("gl").innerHTML=`<style>
 .game-picks{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:4px 0 14px}
 .game-pick{min-height:104px;border:1px solid rgba(124,92,255,.42);border-radius:18px;background:linear-gradient(145deg,#202b55,#171d3b);color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;padding:12px 8px;box-shadow:0 8px 22px rgba(0,0,0,.2)}
 .game-pick:active{transform:scale(.98)}.game-pick b{font-size:16px}.game-pick span{font-size:28px}
 .game-detail{display:none}.game-detail.on{display:block}
 </style>
 <div class="game-picks">
  <button class="game-pick" id="pickSnake"><span>🐍🪜</span><b>مار و پله</b><small>بازی گروهی و تمرینی</small></button>
  <button class="game-pick" id="pickDraw"><span>🎨</span><b>نقاشی</b><small>نقاشی کن و حدس بزن</small></button>
 </div>
 <div id="snakeDetail" class="game-detail">
  <div class="gc"><div class="gimg"></div><div class="gb">
  <h3>🎲🐍 مار و پله</h3>
  <p>۲ تا ۴ نفر، شرط‌بندی با الماس. اول به خونهٔ ۱۰۰ برسه، برنده‌ست!</p>
  <ul class="rl"><li>تاس ۶ = یه نوبت دیگه</li><li>🐍 رسیدی به سر مار؟ می‌افتی پایین</li><li>🪜 رسیدی به پایین نردبون؟ می‌ری بالا</li><li>نوبت ۳۰ ثانیه‌ست؛ ۳ بار پشت‌سرهم نندازی حذف می‌شی</li><li>جایزه = کل شرط‌ها منهای ۵٪ مالیات</li></ul>
  ${cont}
  <button class="alt" id="gprac">🤖 تمرین با ربات (رایگان)</button>
  <div class="msg">برای بازی با شرط: توی گروه بنویس «مار و پله 10» و بقیه رو دعوت کن.</div>
  </div></div>
 </div>
 <div id="drawDetail" class="game-detail">
  <div class="gc"><div class="dimg"></div><div class="gb">
  <h3>🖌 نقاشی و حدس</h3>
  <p>۲ تا ۴ نفر، شرط‌بندی با الماس. ۴ دور؛ هر دور یکی نقاشی می‌کشه و بقیه حدس می‌زنن!</p>
  <ul class="rl"><li>هر دور ۳۰ ثانیه؛ نقاش یه کلمهٔ مخفی می‌گیره و فقط با نقاشی توضیحش می‌ده</li><li>بقیه بین ۱۸ گزینه حدس می‌زنن؛ هرچی سریع‌تر، امتیاز بیشتر ⚡</li><li>نقاش به‌ازای هر حدس درست امتیاز می‌گیره</li><li>آخر هر دور ۵ ثانیه نتیجه نشون داده می‌شه؛ آخر بازی برنده و بازنده‌ها مشخص می‌شن</li><li>جایزه = کل شرط‌ها منهای ۵٪ مالیات</li></ul>
  ${dcont}
  <div class="msg">برای ساخت میز: توی گروه بنویس «نقاشی 20» (عدد = شرط به الماس) و بقیه رو دعوت کن.</div>
  </div></div>
 </div>
 <div class="gc" style="margin-top:14px"><div class="gb">
  <h3>🎨 استیکرهای تلگرام</h3><p>لینک پک استیکر را از تلگرام کپی کن و اینجا وارد کن تا به مجموعه‌ات اضافه شود.</p>
  <input id="sturl" dir="ltr" placeholder="https://t.me/addstickers/PackName" style="width:100%;margin:8px 0">
  <button id="stadd">➕ افزودن پک استیکر</button><div id="stpacks" class="hint">در حال دریافت پک‌ها…</div>
 </div></div><div id="gpcard"></div>`;
 const show=(id)=>{document.querySelectorAll(".game-detail").forEach(x=>x.classList.remove("on"));$(id).classList.add("on")};
 $("pickSnake").onclick=()=>show("snakeDetail");
 $("pickDraw").onclick=()=>show("drawDetail");
 const c=$("gcont");if(c)c.onclick=()=>openGame(s.game.id,false);
 const dc=$("dcont");if(dc)dc.onclick=()=>openDraw(s.draw.id);
 $("gprac").onclick=startPractice;
 loadStickerPacks();
 loadGlobalPacks();
}

/* ---------- پک‌های استیکر ---------- */
let STPACKS=[];
async function loadStickerPacks(){
 const el=$("stpacks");if(!el)return;
 try{
  const r=await api("/api/snk/packs");STPACKS=r.packs||[];
  el.innerHTML=STPACKS.length?STPACKS.map(p=>`<div class="row"><span>🎨 ${esc(p.title)} <small>(${fa(p.stickers.length)} استیکر)</small></span><span class="chip g">آماده</span></div>`).join(""):"هنوز پکی اضافه نکردی.";
 }catch(e){el.textContent="دریافت پک‌ها ناموفق بود؛ دوباره تلاش کن."}
 const b=$("stadd");if(b)b.onclick=async()=>{
  const v=$("sturl").value.trim();if(!v){toast("لینک پک استیکر را وارد کن");return}
  b.disabled=true;b.textContent="در حال افزودن…";
  try{const r=await api("/api/snk/pack_add",{url:v});
   if(r.ok){toast("✅ پک اضافه شد: "+r.title);$("sturl").value="";await loadStickerPacks()}
   else toast(({bad_pack_link:"لینک پک معتبر نیست.",pack_not_found:"پک پیدا نشد؛ لینک رو دوباره کپی کن.",empty_pack:"این پک استیکری ندارد.",tg_timeout:"تلگرام دیر جواب داد؛ دوباره امتحان کن.",tg_error:"تلگرام جواب نداد؛ کمی بعد دوباره امتحان کن.",db_error:"ذخیره پک ناموفق بود؛ دوباره امتحان کن.",internal:"مشکل موقتی سرور؛ دوباره امتحان کن."})[r.error]||"افزودن پک ناموفق بود.")
  }catch(e){toast(e.message)}finally{b.disabled=false;b.textContent="➕ افزودن پک استیکر"}
 };
}
const GPERR={forbidden:"فقط پشتیبانی می‌تونه پک همگانی اضافه کنه.",bad_pack_link:"لینک پک معتبر نیست.",pack_not_found:"پک پیدا نشد؛ لینک رو دوباره کپی کن.",empty_pack:"این پک استیکری ندارد.",tg_timeout:"تلگرام دیر جواب داد؛ دوباره امتحان کن.",tg_error:"تلگرام جواب نداد؛ کمی بعد دوباره امتحان کن.",db_error:"ذخیره ناموفق بود؛ دوباره امتحان کن.",internal:"مشکل موقتی سرور؛ دوباره امتحان کن."};
async function loadGlobalPacks(){
 const el=$("gpcard");if(!el)return;
 try{
  const r=await api("/api/snk/gpacks"),ps=r.packs||[],st=!!r.staff;
  if(!ps.length&&!st){el.innerHTML="";return}
  el.innerHTML=`<div class="gc" style="margin-top:14px"><div class="gb"><h3>🌐 استیکرهای همگانی</h3><p>این پک‌ها را پشتیبانی اضافه کرده و همهٔ کاربرها توی چت مار و پله و نقاشی می‌تونن استفاده کنن.</p>${ps.length?ps.map(p=>`<div class="row"><span>🌐 ${esc(p.title)} <small>(${fa(p.stickers.length)} استیکر)</small></span>${st?`<button class="alt sm" data-gdel="${p.id}" style="width:auto">🗑 حذف</button>`:`<span class="chip g">فعال</span>`}</div>`).join(""):'<div class="hint">هنوز پکی اضافه نشده.</div>'}${st?`<input id="gpurl" dir="ltr" placeholder="https://t.me/addstickers/PackName" style="width:100%;margin:8px 0"><button id="gpadd">➕ افزودن پک همگانی</button>`:""}</div></div>`;
  const add=$("gpadd");if(add)add.onclick=async()=>{
   const v=$("gpurl").value.trim();if(!v){toast("لینک پک را وارد کن");return}
   add.disabled=true;add.textContent="در حال افزودن…";
   try{const x=await api("/api/snk/gpack_add",{url:v});
    if(x.ok){toast("✅ پک همگانی اضافه شد: "+x.title);await loadGlobalPacks()}
    else toast(GPERR[x.error]||"افزودن پک ناموفق بود.")}
   catch(e){toast(e.message)}finally{add.disabled=false;add.textContent="➕ افزودن پک همگانی"}};
  el.querySelectorAll("[data-gdel]").forEach(b=>b.onclick=async()=>{
   try{const x=await api("/api/snk/gpack_del",{id:Number(b.dataset.gdel)});
    if(x.ok){toast("پک حذف شد");await loadGlobalPacks()}else toast(GPERR[x.error]||"حذف ناموفق بود.")}
   catch(e){toast(e.message)}})
 }catch(e){el.innerHTML=""}
}
const QEMO=["👏","😂","🔥","😮","😭","👍","🎉","😡"];
const CERR={slow:"یه کم آروم‌تر ✋",empty_message:"پیام خالیه.",pack_not_found:"این پک دیگه وجود نداره.",bad_sticker:"استیکر نامعتبره.",no_game:"این بازی فعال نیست.",rate:"کمی صبر کن."};
async function pickSticker(send){
 try{
  const [a,b]=await Promise.all([api("/api/snk/gpacks"),api("/api/snk/packs")]);
  const all=(a.packs||[]).map(p=>Object.assign({},p,{g:1})).concat((b.packs||[]).map(p=>Object.assign({},p,{g:0})));
  STPACKS=b.packs||[];
  if(!all.length){toast("هنوز استیکری اضافه نشده؛ از پشتیبانی بخواه پک همگانی اضافه کنه.");return}
  sheetOpen(`<h3>🎨 انتخاب استیکر</h3>${all.map((p,k)=>`<div class="opt" data-pack="${k}">${p.g?"🌐":"🎨"} ${esc(p.title)} · ${fa(p.stickers.length)} استیکر</div>`).join("")}<div id="stchoose"></div>`);
  $("sh").querySelectorAll("[data-pack]").forEach(el=>el.onclick=()=>{
   const p=all[Number(el.dataset.pack)];if(!p)return;
   $("stchoose").innerHTML=`<div class="fg">${p.stickers.map((x,i)=>`<div class="fc" data-st="${i}"><div class="sm1" style="font-size:28px">${esc(x.emoji||"🙂")}</div><small>استیکر ${fa(i+1)}</small></div>`).join("")}</div>`;
   $("stchoose").querySelectorAll("[data-st]").forEach(x=>x.onclick=()=>{sheetClose();send({kind:"sticker",pack_id:p.id,index:Number(x.dataset.st),g:p.g})});
  });
 }catch(e){toast(e.message)}
}
async function snkChat(payload){
 try{const r=await api("/api/snk/chat",Object.assign({gid:GID},payload));
  if(r.error){toast(CERR[r.error]||"ارسال پیام ناموفق بود");if(r.game)applyG(r.game);return false}
  if(r.game)applyG(r.game);return true}
 catch(e){toast(e.message);return false}
}
function chooseSticker(){return pickSticker(snkChat)}
async function sendGameChat(){
 const el=$("gchattext");if(!el)return;const text=el.value.trim();if(!text)return;
 if(await snkChat({kind:"text",text}))el.value=""
}

/* ---------- بازی مار و پله ---------- */
/* ---------- افکت‌های صوتی (WebAudio، بدون فایل؛ بلند و با کمپرسور) ---------- */
const SFX=(()=>{
 let ac=null,mg=null,nb=null,on=true;
 try{on=localStorage.getItem("snk_snd")!=="0"}catch(e){}
 function ctx(){
  if(!ac){try{const C=window.AudioContext||window.webkitAudioContext;ac=new C();
   const cp=ac.createDynamicsCompressor();cp.threshold.value=-8;cp.knee.value=10;cp.ratio.value=14;cp.attack.value=.002;cp.release.value=.12;
   mg=ac.createGain();mg.gain.value=1.9;mg.connect(cp);cp.connect(ac.destination);
   nb=ac.createBuffer(1,ac.sampleRate,ac.sampleRate);const d=nb.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1}catch(e){ac=null}}
  if(ac&&ac.state==="suspended")ac.resume().catch(()=>{});
  return ac}
 function tone(f,t,d,type,v,to){const a=ctx();if(!a||!on)return;t=a.currentTime+t;
  const o=a.createOscillator(),g=a.createGain();o.type=type||"square";o.frequency.setValueAtTime(f,t);
  if(to)o.frequency.exponentialRampToValueAtTime(to,t+d);
  g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(v,t+.006);g.gain.exponentialRampToValueAtTime(.0001,t+d);
  o.connect(g);g.connect(mg);o.start(t);o.stop(t+d+.03);return o}
 function noise(t,d,v,ft,f0,f1,q){const a=ctx();if(!a||!on)return;t=a.currentTime+t;
  const s=a.createBufferSource();s.buffer=nb;s.loop=true;
  const f=a.createBiquadFilter();f.type=ft||"bandpass";f.Q.value=q||1;f.frequency.setValueAtTime(f0,t);if(f1)f.frequency.exponentialRampToValueAtTime(f1,t+d);
  const g=a.createGain();g.gain.setValueAtTime(.0001,t);g.gain.exponentialRampToValueAtTime(v,t+Math.min(.02,d/3));g.gain.exponentialRampToValueAtTime(.0001,t+d);
  s.connect(f);f.connect(g);g.connect(mg);s.start(t);s.stop(t+d+.03)}
 return{
  unlock(){ctx()},
  get on(){return on},
  set(v){on=!!v;try{localStorage.setItem("snk_snd",on?"1":"0")}catch(e){}if(on)ctx()},
  tick(){tone(900,0,.05,"square",.5);tone(1350,.03,.06,"square",.4)},
  dice(){ // قل‌خوردن تاس: تق‌تق‌های پشت‌سرهم که کند می‌شن + کوبیدن نهایی
   const n=16;for(let i=0;i<n;i++){const t=.95*Math.pow(i/n,1.55),v=.95-i*.03;
    noise(t,.035,v,"bandpass",1800+Math.random()*2200,null,2.5);
    tone(600+Math.random()*900,t,.045,"square",v*.55,300+Math.random()*200)}
   tone(190,.98,.22,"sine",1.2,45);noise(.98,.07,1,"lowpass",900,200,1);tone(520,.99,.08,"square",.6,260)},
  six(){tone(880,0,.1,"square",.5);tone(1320,.09,.16,"square",.5);tone(1760,.2,.22,"triangle",.6)},
  step(k){const f=380+(k%10)*34;tone(f,0,.1,"triangle",1,f*1.5);tone(f*2,0,.05,"sine",.5);noise(0,.025,.5,"highpass",3000)},
  ladder(){ // بالا رفتن از نردبون: پله‌پله + نت‌های صعودی + وووش
   const N=[523,659,784,1047,1319,1568,2093];
   N.forEach((f,i)=>{tone(f,i*.085,.16,"square",.45);tone(f/2,i*.085,.18,"triangle",.8);noise(i*.085,.03,.6,"bandpass",2500,null,3)});
   noise(0,.75,.6,"bandpass",300,4500,1.2);
   [2093,2637,3136].forEach((f,i)=>tone(f,.62+i*.07,.28,"sine",.55))},
  snake(){ // گاز مار: هیس + سُر خوردن به پایین + تپ + نت‌های غمگین
   noise(0,1,1,"highpass",2500,6500,.8);noise(0,.9,.7,"bandpass",5200,1800,3);
   const o=tone(620,.05,.95,"sawtooth",.65,70);
   const a=ctx();if(a&&o&&on){const l=a.createOscillator(),lg=a.createGain();l.frequency.value=14;lg.gain.value=28;l.connect(lg);lg.connect(o.frequency);l.start(a.currentTime+.05);l.stop(a.currentTime+1.05)}
   tone(130,.95,.3,"sine",1.3,38);noise(.95,.08,1,"lowpass",700,150,1);
   [392,349,311,262].forEach((f,i)=>tone(f,.35+i*.16,.17,"square",.4))},
  win(){[523,659,784,1047].forEach((f,i)=>{tone(f,i*.13,.22,"square",.5);tone(f/2,i*.13,.25,"sawtooth",.5)});
   [1047,1319,1568].forEach(f=>{tone(f,.55,.9,"square",.4);tone(f/2,.55,.9,"sawtooth",.4)});noise(.55,.5,.4,"highpass",5000,9000)},
  lose(){[392,330,262,196].forEach((f,i)=>tone(f,i*.2,.28,"sawtooth",.55,f*.96))}}})();
document.addEventListener("pointerdown",()=>SFX.unlock(),{passive:true});

/* ---------- تاس سه‌بعدی ---------- */
const PIPS={1:[4],2:[0,8],3:[0,4,8],4:[0,2,6,8],5:[0,2,4,6,8],6:[0,2,3,5,6,8]},
 DFACE={1:[0,0],6:[0,180],2:[0,-90],5:[0,90],3:[-90,0],4:[90,0]};
let DSPIN=0;
function diceHTML(){let h='<div class="tilt"><div class="cube" id="cb">';
 for(let k=1;k<=6;k++)h+=`<div class="df f${k}">`+[0,1,2,3,4,5,6,7,8].map(i=>`<i${PIPS[k].includes(i)?' class="p"':""}></i>`).join("")+"</div>";
 return h+'</div></div><div class="dsh"></div>'}
function setDice(r,anim){const cb=$("cb");if(!cb)return;const f=DFACE[r]||DFACE[1];
 if(anim){DSPIN++;cb.style.transition="transform 1s cubic-bezier(.12,.75,.2,1)"}else cb.style.transition="none";
 cb.style.transform=`rotateX(${f[0]+720*DSPIN}deg) rotateY(${f[1]+1080*DSPIN}deg)`}
async function rollDice(r){
 const w=$("dc");SFX.dice();haptic("light");
 if(w){w.classList.remove("roll");void w.offsetWidth;w.classList.add("roll")}
 setDice(r,true);await sleep(1050);if(w)w.classList.remove("roll");
 if(r===6)SFX.six()}

/* ---------- تخته ---------- */
const COLORS=["#e5484d","#2e9e5b","#2481cc","#f5a524"];
const GE={not_player:"تو توی این بازی نیستی.",no_game:"این بازی فعال نیست.",not_your_turn:"نوبت تو نیست.",already_out:"از بازی خارج شدی.",rate:"کمی صبر کن."};
let GID=null,GS=null,SIG="",LSEQ=-1,LEFT=0,LAT=0,PT=null,RB=false,FIRST=true,STOPAT=0,PRAC=false,PG=null,PON=false;
let TKE={},DISP={},Q=Promise.resolve();
/* خونه‌ها روی خود عکس: ۱۰ ستون × ۱۰ ردیف؛ ردیف یازدهم (پایین) خونهٔ «شروع»ه. مختصات به پیکسلِ عکس اصلی ۱۰۲۴×۱۵۳۶ */
const GX=[162,232,302,375,448,518,598,672,754,836,918],GY=[270,356,443,534,631,730,834,939,1045,1154,1265,1370];
function tokPos(n,idx){
 if(n<=0){const c=idx%4;return [(GX[c]+GX[c+1])/20.48,(GY[10]+GY[11])/30.72]}
 const c=(n-1)%10,r=9-Math.floor((n-1)/10),dx=[-1.5,1.5,-1.5,1.5][idx%4],dy=[-1.1,-1.1,1.2,1.2][idx%4];
 return [(GX[c]+GX[c+1])/20.48+dx,(GY[r]+GY[r+1])/30.72+dy]}
function setTok(p,n,jump,step){
 let e=TKE[p.id];
 if(!e){e=document.createElement("div");e.className="tok";e.style.backgroundColor=COLORS[p.idx%4];$("tkl").appendChild(e);TKE[p.id]=e}
 const g=GS;e.classList.toggle("me",!!(g&&p.id===g.me));
 e.classList.toggle("jump",!!jump);
 if(step){e.classList.remove("step");void e.offsetWidth;e.classList.add("step")}
 const xy=tokPos(n,p.idx);e.style.left=xy[0]+"%";e.style.top=xy[1]+"%";e.style.opacity=p.out?".35":"1";DISP[p.id]=n}
function shell(){
 $("g").innerHTML=`<div class="gbar"><button class="alt sm" id="gback">‹ بازگشت</button><button class="alt sm xbtn" id="gexit" style="display:none">🚪 خروج</button><button class="alt sm" id="gsnd">${SFX.on?"🔊 صدا":"🔇 بی‌صدا"}</button></div>
 <div id="gtop"></div>
 <div class="bw" id="bw"><img src="/board.webp" alt="" decoding="async"><div class="tkl" id="tkl"></div></div>
 <div class="lg" id="glog"></div>
 <div class="dkw" id="gdock"><div class="dk"><div class="dice3" id="dc">${diceHTML()}</div><div class="dkr"><div class="dkt" id="dkt"></div><button id="rb">🎲 تاس بنداز</button></div></div></div>`;
 $("gback").onclick=()=>{stopGame();showTab(VIEW==="dash"?"games":VIEW);reloadDash()};
 $("gexit").onclick=()=>askExit(leaveSnk);
 $("gsnd").onclick=()=>{SFX.set(!SFX.on);$("gsnd").textContent=SFX.on?"🔊 صدا":"🔇 بی‌صدا";if(SFX.on)SFX.tick()};
 $("rb").onclick=roll;setDice(1,false);
 TKE={};DISP={};Q=Promise.resolve()}
function openGame(gid,prac){
 stopGame();PRAC=!!prac;GID=gid;GS=null;
 for(const k in PANES)$(PANES[k]).style.display="none";
 $("g").style.display="block";$("ttl").style.display="none";document.body.classList.add("ing");
 shell();FIRST=true;SIG="";LSEQ=-1;STOPAT=0;
 if(PRAC){PON=true;applyG(PG)}
 else{$("gtop").innerHTML='<div class="msg">در حال بارگذاری…</div>';poll();PT=setInterval(poll,1300)}}
function askExit(go){const m="اگه از بازی خارج بشی می‌بازی، شرطت می‌سوزه و حریفت برنده می‌شه. مطمئنی؟";
 if(tg&&tg.showConfirm)tg.showConfirm(m,ok=>{if(ok)go()});else if(confirm(m))go()}
async function leaveSnk(){try{const r=await api("/api/snk/leave",{gid:GID});if(r.game)applyG(r.game);if(r.error)toast(GE[r.error]||"خطا")}catch(e){toast(e.message)}}
function stopGame(){clearInterval(PT);PT=null;PON=false;GS=null;if(typeof drStop==="function")drStop()}
function reloadDash(){api("/api/me").then(s=>renderAll(s)).catch(()=>{})}
async function poll(){try{const r=await api("/api/snk/state",{gid:GID});
 if(!r.game){$("gtop").innerHTML=`<div class="msg">${GE[r.error]||"بازی فعالی نداری."}</div>`;clearInterval(PT);PT=null;return}
 applyG(r.game)}catch(e){}}
function nameOf(g,id){const p=g.players.find(x=>x.id===id);return p?p.name:"—"}
function applyG(g){
 GS=g;LEFT=g.left;LAT=Date.now();GID=g.id;
 const sig=JSON.stringify([g.status,g.turn,g.seq,g.players.map(p=>[p.pos,p.out]),g.winner,g.chat||[],g.spec,g.watchers]);
 if(sig!==SIG){SIG=sig;renderTop(g)}
 g.players.forEach(p=>{if(!TKE[p.id])setTok(p,FIRST?p.pos:0,false)});
 if(g.last&&g.last.seq!==LSEQ){
  if(FIRST){g.players.forEach(p=>setTok(p,p.pos,false));setDice(g.last.r,false)}
  else animMove(g,g.last)
  LSEQ=g.last.seq}
 else if(FIRST)g.players.forEach(p=>setTok(p,p.pos,false));
 Q=Q.then(()=>{if(GS!==g)return;g.players.forEach(p=>{if(DISP[p.id]!==p.pos||TKE[p.id].style.opacity!==(p.out?".35":"1"))setTok(p,p.pos,false)})});
 FIRST=false;
 if(["done","cancelled","refunded"].includes(g.status)){
  if(g.status==="done"&&!g._shown){g._shown=1;Q=Q.then(()=>{if(!g.spec)SFX[g.winner===g.me?"win":"lose"]();toast(g.winner===g.me?"🏆 برنده شدی!":"🏁 "+nameOf(g,g.winner)+" برنده شد",3500);if(!g.spec)haptic(g.winner===g.me?"success":"warning")})}
  if(!PRAC){if(!STOPAT)STOPAT=Date.now()+8000;else if(Date.now()>STOPAT){clearInterval(PT);PT=null}}}}
function animMove(g,L){
 const p=g.players.find(x=>x.id===L.pid);if(!p)return;
 Q=Q.then(async()=>{
  await rollDice(L.r);
  if(!TKE[p.id])return;
  const land=Math.min(L.from+L.r,100);
  if(DISP[p.id]!==L.from){setTok(p,L.from,false);await sleep(60)}
  for(let k=L.from+1;k<=land;k++){setTok(p,k,false,true);SFX.step(k);await sleep(190)}
  if(L.ev){
   await sleep(300);
   const who=p.id===g.me?"تو":p.name;
   if(L.ev==="snake"){SFX.snake();const b=$("bw");if(b){b.classList.remove("quake");void b.offsetWidth;b.classList.add("quake")}
    toast("🐍 "+(p.id===g.me?"مار گازت گرفت!":"مار "+who+" رو گاز گرفت!")+" "+fa(land)+" ← "+fa(L.to),2800);haptic("error")}
   else{SFX.ladder();toast("🪜 "+(p.id===g.me?"از نردبون رفتی بالا!":who+" از نردبون رفت بالا!")+" "+fa(land)+" ← "+fa(L.to),2800);haptic("success")}
   setTok(p,L.to,true);await sleep(1050);
  }
 })}
function renderTop(g){
 const me=g.me,mine=g.turn===me&&g.status==="playing";
 const nm=id=>esc(nameOf(g,id));
 let top;
 if(g.status==="lobby")top=`⏳ منتظر شروع بازی… <b id="tm">${fa(LEFT)}</b> ثانیه<br><span style="color:var(--hint);font-size:12px">شروع با دکمهٔ «شروع بازی» توی گروه یا خودکار</span>`;
 else if(g.status==="playing")top=g.practice?(mine?`🎲 <b>نوبت توئه!</b>`:`نوبت <b>${nm(g.turn)}</b>…`):(mine?`🎲 <b>نوبت توئه!</b> <span id="tm">${fa(LEFT)}</span> ثانیه`:`نوبت <b>${nm(g.turn)}</b> · <span id="tm">${fa(LEFT)}</span> ثانیه`);
 else if(g.status==="done")top=g.winner===me?(g.practice?`🏆 <b>بردی!</b> (تمرین)`:`🏆 <b>برنده شدی!</b><br>${fa(g.payout)} الماس به حسابت اضافه شد`):`🏁 <b>${nm(g.winner)}</b> برنده شد`;
 else top="❌ بازی لغو شد؛ شرط‌ها برگشت";
 const mep=g.players.find(p=>p.id===me);
 if(g.status==="playing"&&mep&&mep.out)top="🚪 از بازی خارج شدی؛ شرطت سوخت";
 if(g.spec)top="👀 <b>تماشاچی</b> · "+top;
 const exb=$("gexit");if(exb)exb.style.display=(g.status==="playing"&&!g.practice&&mep&&!mep.out)?"":"none";
 const pl=g.players.map(p=>`<div class="pl ${p.id===g.turn&&g.status==="playing"?"cur":""} ${p.out?"out":""}"><u style="background:${COLORS[p.idx%4]}"></u><span>${esc(p.name)}${p.id===me&&!g.practice?" (تو)":""}</span><b>${p.pos?fa(p.pos):"شروع"}</b></div>`).join("");
 const L=g.last,ev=L?(L.ev==="ladder"?" 🪜":L.ev==="snake"?" 🐍":""):"";
 const lt=L?`${nm(L.pid)}: ${fa(L.r)} ← ${fa(L.from)} به ${fa(L.to)}${ev}${L.auto?" (خودکار)":""}`:"";
 const money=g.practice?`<div class="row"><span>🤖 حالت تمرین</span><span>بدون الماس</span></div>`:`<div class="row"><span>💎 شرط هر نفر</span><span>${num(g.bet)}</span></div><div class="row"><span>🎁 جایزهٔ کل</span><span>${num(g.pot)}</span></div>`;
 const chat=(g.chat||[]).map(m=>`<div class="row" style="align-items:flex-start"><span><b>${esc(m.name)}:</b> ${m.kind==="sticker"?"🎨 استیکر "+esc(m.emoji||"🙂"):esc(m.text||"")}</span></div>`).join("");
 const chatbox=g.practice?"":`<div class="gc" style="margin:12px 0;padding:12px"><h3>💬 چت بازی${g.watchers?` <small style="color:var(--hint)">· 👀 ${fa(g.watchers)} تماشاچی</small>`:""}</h3><div style="max-height:180px;overflow:auto">${chat||'<div class="hint">هنوز پیامی نیست.</div>'}</div><div class="dqe" id="gqe">${QEMO.map(e=>`<a data-q="${e}">${e}</a>`).join("")}</div><div style="display:flex;gap:6px;margin-top:8px"><input id="gchattext" placeholder="پیام بنویس…" maxlength="300"><button id="gchatsend" style="width:auto">ارسال</button><button id="gchatst" class="alt" style="width:auto">🎨</button></div></div>`;
 $("gtop").innerHTML=`<div class="top">${top}</div>${money}${pl}${chatbox}`;
 const csend=$("gchatsend"),cinput=$("gchattext"),cst=$("gchatst");
 if(csend)csend.onclick=sendGameChat;
 if(cinput)cinput.onkeydown=e=>{if(e.key==="Enter")sendGameChat()};
 if(cst)cst.onclick=chooseSticker;
 document.querySelectorAll("#gqe [data-q]").forEach(a=>a.onclick=()=>snkChat({kind:"text",text:a.dataset.q}));
 $("glog").innerHTML=g.log.map(x=>esc(x)).join("<br>");
 /* دکمهٔ تاس و خود تاس پایین صفحه (ثابت) */
 const dk=$("gdock"),rb=$("rb");
 if(dk){dk.style.display=(g.status==="playing"&&!g.spec)?"block":"none";
  $("dkt").innerHTML=lt||"اول تو تاس بنداز!";
  rb.disabled=!mine||RB;rb.classList.toggle("go",mine);
  rb.textContent=mine?"🎲 تاس بنداز":"⏳ نوبت "+nameOf(g,g.turn)}}
async function roll(){
 if(RB)return;RB=true;const b=$("rb");if(b)b.disabled=true;SFX.unlock();SFX.tick();
 try{
  if(PRAC){pRoll();return}
  const r=await api("/api/snk/roll",{gid:GID});if(r.game)applyG(r.game);if(r.error)toast(GE[r.error]||"خطا")}
 catch(e){toast(e.message)}finally{RB=false}}
setInterval(()=>{const e=$("tm");if(e&&GS&&!GS.practice)e.textContent=fa(Math.max(0,LEFT-Math.floor((Date.now()-LAT)/1000)))},500);

/* ---------- حالت تمرین (سمت کلاینت، بدون الماس) ---------- */
function startPractice(){
 PG={id:0,practice:true,status:"playing",bet:0,pot:0,me:1,creator:1,
  players:[{id:1,idx:0,name:"تو",pos:0,out:false},{id:2,idx:1,name:"ربات 🤖",pos:0,out:false}],
  turn:1,left:0,seq:0,last:null,log:["🎲 تمرین شروع شد"],winner:null,payout:null,snakes:SNK.snakes,ladders:SNK.ladders};
 openGame(0,true)}
function pRoll(){
 const g=PG;if(!PON||!g||g.status!=="playing"){RB=false;return}
 const p=g.players.find(x=>x.id===g.turn),r=1+Math.floor(Math.random()*6),frm=p.pos;
 let pos=frm+r,ev=null;
 if(pos<100){if(SNK.ladders[pos]){pos=SNK.ladders[pos];ev="ladder"}else if(SNK.snakes[pos]){pos=SNK.snakes[pos];ev="snake"}}
 else pos=100;
 p.pos=pos;g.seq++;g.last={seq:g.seq,pid:p.id,r:r,from:frm,to:pos,ev:ev,auto:false};
 g.log.push(p.name+" 🎲"+r+": "+frm+"→"+pos+(ev==="ladder"?" 🪜":ev==="snake"?" 🐍":""));g.log=g.log.slice(-8);
 if(pos>=100){g.status="done";g.winner=p.id}
 else if(r!==6)g.turn=(g.turn===1)?2:1;
 RB=false;applyG(g);
 if(g.status==="playing"&&g.turn===2)setTimeout(()=>{if(PON&&PG===g){RB=true;pRoll()}},(g.last&&g.last.ev?4600:3000))}

/* ================= بازی نقاشی ================= */
const DCOL=["#111111","#ff1744","#ff6d00","#ffd600","#76ff03","#00c853","#00e5ff","#2979ff","#651fff","#d500f9","#ff4081","#8d5524","#9e9e9e","#fffef9"],DW=[6,14,34],DPAPER="#fffef9";
const DE={not_player:"تو توی این بازی نیستی.",no_game:"این بازی فعال نیست.",not_draw:"الان زمان حدس زدن نیست.",drawer:"نقاش نمی‌تونه حدس بزنه 😅",already:"قبلاً جواب دادی.",bad_option:"گزینه نامعتبره.",not_drawer:"الان نوبت نقاشی تو نیست.",wait_drawing:"صبر کن نقاش شروع کنه ✏️",stale:"دور عوض شد.",left:"تو از بازی خارج شدی.",rate:"کمی صبر کن."};
let DR=null;
function drNew(gid){return{gid:gid,rnd:null,n:0,fresh:true,GS:null,sig:"",dsig:"",osig:"",csig:"",LEFT:0,LAT:0,col:0,wid:1,q:[],cur:null,drawing:false,inflight:false,polling:false,pt:null,ft:null,busyG:false,lastSec:-1,myk:"",prevRnd:-1,done:false,stopAt:0,confetti:false,tmv:"",sc:{},cx:null}}
function drStop(){const D=DR;if(!D)return;clearInterval(D.pt);clearInterval(D.ft);DR=null;try{tg&&tg.enableVerticalSwipes&&tg.enableVerticalSwipes()}catch(e){}}
function openDraw(gid){
 stopGame();
 for(const k in PANES)$(PANES[k]).style.display="none";
 $("g").style.display="block";$("ttl").style.display="none";document.body.classList.add("ing");
 try{tg&&tg.disableVerticalSwipes&&tg.disableVerticalSwipes()}catch(e){}
 DR=drNew(gid);drShell();
 drPoll();DR.pt=setInterval(drPoll,800);DR.ft=setInterval(drFlush,300)}
function drShell(){
 $("g").innerHTML=`<div class="gbar"><button class="alt sm" id="dback">‹ بازگشت</button><button class="alt sm xbtn" id="dexit" style="display:none">🚪 خروج</button><button class="alt sm" id="dsnd">${SFX.on?"🔊 صدا":"🔇 بی‌صدا"}</button></div>
 <div id="dtop"><div class="msg">در حال بارگذاری…</div></div>
 <div class="dcw" id="dcw"><canvas id="dcv" width="1000" height="1000"></canvas><div class="dov" id="dov"></div></div>
 <div id="ddock"></div>
 <div class="dchat" id="dchat"><div class="dcm" id="dcm"></div><div class="dqe" id="dqe">${QEMO.map(e=>`<a data-q="${e}">${e}</a>`).join("")}</div><div class="dci"><input id="dct" placeholder="پیام بنویس…" maxlength="300"><button id="dcs" style="width:auto">ارسال</button><button id="dcst" class="alt" style="width:auto">🎨</button></div></div>`;
 $("dback").onclick=()=>{drStop();showTab(VIEW==="dash"?"games":VIEW);reloadDash()};
 $("dexit").onclick=()=>askExit(async()=>{const D=DR;if(!D)return;try{const r=await api("/api/drw/leave",{gid:D.gid,rnd:D.rnd,since:D.n});if(DR===D&&r.game)drApply(r.game);if(r.error)toast(DE[r.error]||"خطا")}catch(e){toast(e.message)}});
 $("dsnd").onclick=()=>{SFX.set(!SFX.on);$("dsnd").textContent=SFX.on?"🔊 صدا":"🔇 بی‌صدا";if(SFX.on)SFX.tick()};
 const cv=$("dcv");DR.cx=cv.getContext("2d");drClear();
 $("dcs").onclick=async()=>{const el=$("dct"),t=el.value.trim();if(t&&await drChat({kind:"text",text:t}))el.value=""};
 $("dct").onkeydown=e=>{if(e.key==="Enter")$("dcs").click()};
 $("dcst").onclick=()=>pickSticker(drChat);
 document.querySelectorAll("#dqe [data-q]").forEach(a=>a.onclick=()=>drChat({kind:"text",text:a.dataset.q}));
 cv.addEventListener("pointerdown",drDown);cv.addEventListener("pointermove",drMove);
 cv.addEventListener("pointerup",drUp);cv.addEventListener("pointercancel",drUp);
 $("ddock").onclick=e=>{
  const D=DR;if(!D)return;
  const c=e.target.closest("[data-c]");if(c){D.col=+c.dataset.c;drSel();haptic("light");return}
  const w=e.target.closest("[data-w]");if(w){D.wid=+w.dataset.w;if(D.col===13)D.col=0;drSel();haptic("light");return}
  if(e.target.closest("[data-e]")){D.col=13;drSel();haptic("light");return}
  if(e.target.closest("[data-clr]")){drCut();D.q.push({clr:1});drClear();haptic("warning");return}
  const o=e.target.closest("[data-o]");if(o&&!o.disabled)drGuess(+o.dataset.o)}}
async function drChat(payload){
 const D=DR;if(!D)return false;
 try{const r=await api("/api/drw/chat",Object.assign({gid:D.gid,rnd:D.rnd,since:D.n},payload));
  if(DR!==D)return false;
  if(r.game)drApply(r.game);
  if(r.error){toast(CERR[r.error]||"ارسال پیام ناموفق بود");return false}
  return true}
 catch(e){toast(e.message);return false}
}
function drRenderChat(g){
 const el=$("dcm");if(!el)return;
 el.innerHTML=(g.chat||[]).slice(-12).map(m=>`<div><b>${esc(m.name)}:</b> ${m.kind==="sticker"?"🎨 "+esc(m.emoji||"🙂"):esc(m.text||"")}</div>`).join("")||'<div class="hint">هنوز پیامی نیست.</div>';
 el.scrollTop=el.scrollHeight}
function drClear(){const D=DR;if(!D||!D.cx)return;D.cx.fillStyle=DPAPER;D.cx.fillRect(0,0,1000,1000)}
function drawOp(o){
 const D=DR;if(!D||!D.cx)return;const cx=D.cx;
 if(o.clr){drClear();return}
 const p=o.p,c=DCOL[o.c]||"#111",w=DW[o.w]||14;
 cx.strokeStyle=c;cx.fillStyle=c;cx.lineWidth=w;cx.lineCap="round";cx.lineJoin="round";
 if(p.length<4){cx.beginPath();cx.arc(p[0],p[1],w/2,0,6.2832);cx.fill();return}
 cx.beginPath();cx.moveTo(p[0],p[1]);for(let i=2;i<p.length;i+=2)cx.lineTo(p[i],p[i+1]);cx.stroke()}
function drSel(){
 document.querySelectorAll("#dsw a").forEach(a=>a.classList.toggle("on",+a.dataset.c===DR.col));
 document.querySelectorAll("#dsz [data-w]").forEach(a=>a.classList.toggle("on",+a.dataset.w===DR.wid&&DR.col!==13));
 const e=document.querySelector("#dsz [data-e]");if(e)e.classList.toggle("on",DR.col===13)}
/* ---- کشیدن (فقط نقاش) ---- */
function drCanDraw(){const D=DR;if(!D||!D.GS)return false;const g=D.GS;
 return g.status==="playing"&&g.phase==="draw"&&g.drawer===g.me&&(D.LEFT-(Date.now()-D.LAT)/1000)>0}
function drPtr(e){const r=$("dcv").getBoundingClientRect();
 return[Math.max(0,Math.min(1000,Math.round((e.clientX-r.left)/r.width*1000))),Math.max(0,Math.min(1000,Math.round((e.clientY-r.top)/r.height*1000)))]}
function drDown(e){
 const D=DR;if(!D||!drCanDraw())return;e.preventDefault();
 try{$("dcv").setPointerCapture(e.pointerId)}catch(x){}
 const q=drPtr(e);D.drawing=true;D.cur={c:D.col,w:D.wid,p:[q[0],q[1]],sent:0};
 drawOp({c:D.col,w:D.wid,p:[q[0],q[1]]})}
function drMove(e){
 const D=DR;if(!D||!D.drawing||!D.cur)return;e.preventDefault();
 if(!drCanDraw()){drUp(e);return}
 const cur=D.cur,cx=D.cx,evs=(e.getCoalescedEvents&&e.getCoalescedEvents().length)?e.getCoalescedEvents():[e];
 cx.strokeStyle=DCOL[cur.c];cx.lineWidth=DW[cur.w];cx.lineCap="round";cx.lineJoin="round";
 for(const ev of evs){
  const q=drPtr(ev),n=cur.p.length,lx=cur.p[n-2],ly=cur.p[n-1];
  if(Math.hypot(q[0]-lx,q[1]-ly)<2.5)continue;
  cx.beginPath();cx.moveTo(lx,ly);cx.lineTo(q[0],q[1]);cx.stroke();
  cur.p.push(q[0],q[1]);
  if(cur.p.length-cur.sent>=160)drCut()}}
function drUp(){const D=DR;if(!D)return;D.drawing=false;if(D.cur){drCut();D.cur=null;drFlush()}}
function drCut(){
 const D=DR,c=D&&D.cur;if(!c||c.p.length<=c.sent)return;
 const from=c.sent>0?Math.max(0,c.sent-2):0;
 D.q.push({c:c.c,w:c.w,p:c.p.slice(from)});c.sent=c.p.length}
async function drFlush(){
 const D=DR;if(!D||D.inflight||!D.GS)return;
 const g=D.GS;
 if(g.drawer!==g.me||g.phase!=="draw"||g.status!=="playing"){D.q.length=0;D.cur=null;return}
 drCut();if(!D.q.length)return;
 const batch=D.q.splice(0,30);D.inflight=true;
 try{const r=await api("/api/drw/ops",{gid:D.gid,ops:batch,rnd:D.rnd,since:D.n});
  if(DR===D&&r.game)drApply(r.game)}
 catch(e){if(DR===D)D.q.unshift(...batch)}
 finally{D.inflight=false}}
/* ---- حدس زدن ---- */
async function drGuess(i){
 const D=DR;if(!D||D.busyG)return;D.busyG=true;SFX.unlock();SFX.tick();
 try{const r=await api("/api/drw/guess",{gid:D.gid,opt:i,rnd:D.rnd,since:D.n});
  if(DR!==D)return;
  if(r.game)drApply(r.game);if(r.error)toast(DE[r.error]||"خطا")}
 catch(e){toast(e.message)}finally{D.busyG=false}}
/* ---- همگام‌سازی با سرور ---- */
async function drPoll(){
 const D=DR;if(!D||D.polling)return;D.polling=true;
 try{const r=await api("/api/drw/state",{gid:D.gid,rnd:D.rnd,since:D.n});
  if(DR!==D)return;
  if(!r.game){$("dtop").innerHTML=`<div class="msg">${DE[r.error]||"بازی فعالی نداری."}</div>`;clearInterval(D.pt);clearInterval(D.ft);return}
  drApply(r.game)}
 catch(e){}finally{D.polling=false}}
function drApply(g){
 const D=DR;if(!D)return;
 D.GS=g;D.LEFT=g.left;D.LAT=Date.now();
 const isD=g.drawer===g.me;
 /* بوم */
 if(g.rnd!==D.rnd||g.reset){drClear();D.n=0;D.rnd=g.rnd;D.fresh=true;D.q=[];D.cur=null;D.drawing=false}
 if(!isD||D.fresh){if(g.from<=D.n){for(let i=D.n-g.from;i<g.ops.length;i++)drawOp(g.ops[i]);D.n=g.from+g.ops.length}}
 else D.n=g.total;
 if(D.n>=g.total)D.fresh=false;
 else if(!isD)setTimeout(drPoll,60);
 /* صدا و حس‌های دور جدید / جواب من / پایان */
 if(g.status==="playing"&&D.prevRnd!==g.rnd){
  if(D.prevRnd!==-1){SFX.six();haptic("light")}
  if(isD)toast("✏️ نوبت نقاشی توئه! کلمه: «"+g.word+"»",3000);
  D.prevRnd=g.rnd}
 const k=g.my?(g.my.ok?"ok":"no"):"";
 if(k!==D.myk){if(k==="ok"){SFX.ladder();haptic("success")}else if(k==="no"){SFX.lose();haptic("error")}D.myk=k}
 if(g.status==="done"&&!D.done){D.done=true;const iw=(g.winners||[]).includes(g.me);if(!g.all_zero&&!g.spec){SFX[iw?"win":"lose"]();haptic(iw?"success":"warning")}}
 /* بالا (امتیازها) */
 const csig=JSON.stringify((g.chat||[]).map(m=>[m.at,m.uid,m.text||m.emoji||""]));
 if(csig!==D.csig){D.csig=csig;drRenderChat(g)}
 const sig=JSON.stringify([g.status,g.phase,g.rnd,g.drawer,g.players.map(p=>[p.score,p.ans,p.out]),g.spec,g.watchers]);
 if(sig!==D.sig){D.sig=sig;drRenderTop(g)}
 const dsig=JSON.stringify([g.status,g.phase,g.rnd,g.drawer,g.my&&g.my.opt,g.my&&g.my.ok,g.total>0,g.word,(g.players.find(p=>p.id===g.me)||{}).out]);
 if(dsig!==D.dsig){D.dsig=dsig;drRenderDock(g)}
 const osig=JSON.stringify([g.status,g.phase,g.rnd,g.last_round&&g.last_round.rnd]);
 if(osig!==D.osig){D.osig=osig;drOverlay(g)}
 if(["done","cancelled","refunded"].includes(g.status)){
  if(!D.stopAt)D.stopAt=Date.now()+6000;else if(Date.now()>D.stopAt){clearInterval(D.pt);clearInterval(D.ft)}}}
/* ---- رندر ---- */
function drRenderTop(g){
 const me=g.me,ring='<div class="dring" id="dring"><b id="dtn"></b></div>';
 const dn=(g.players.find(p=>p.id===g.drawer)||{}).name||"—";
 const isD=g.drawer===me;
 const pls=g.players.map(p=>{const prev=DR.sc[p.id];DR.sc[p.id]=p.score;const up=prev!==undefined&&p.score>prev;
  return `<div class="dpl ${p.id===g.drawer&&g.status==="playing"?"dr":""} ${p.id===me?"me":""} ${up?"up":""} ${p.out?"out":""}"><u style="background:${COLORS[p.idx%4]}">${esc((p.name||"?").trim().charAt(0)||"?")}</u><span>${esc(p.name)}${p.id===g.drawer&&g.status==="playing"?" ✏️":""}${p.ans?" ✅":""}${p.out?" 🚪":""}</span><b>${fa(p.score)}</b></div>`}).join("");
 const mep=g.players.find(p=>p.id===me),exb=$("dexit");
 if(exb)exb.style.display=(g.status==="playing"&&mep&&!mep.out)?"":"none";
 let head;
 if(g.status==="lobby")head=`<div class="dhd"><div class="dtt"><b>🎨 نقاشی و حدس</b><small>منتظر شروع بازی…</small></div>${ring}</div>`;
 else if(g.status==="playing")head=`<div class="dhd"><div class="dch">دور ${fa(g.rnd)} از ${fa(g.rounds)}</div><div class="dtt"><b>${g.phase==="result"?"📊 نتیجهٔ دور":isD?"✏️ نوبت نقاشی توئه!":esc(dn)+" داره نقاشی می‌کشه"}</b><small>${g.phase==="result"?"آماده شو برای دور بعد":isD?"کلمه رو فقط تو می‌بینی؛ ۳۰ ثانیه وقت داری":g.spec?"👀 تماشاچی هستی؛ کلمه مخفیه":"بین گزینه‌ها حدس بزن چی می‌کشه!"}</small></div>${ring}</div>`;
 else head=`<div class="dhd"><div class="dtt"><b>${g.status==="done"?"🏁 بازی تموم شد":"❌ بازی لغو شد"}</b><small>${g.status==="done"?"نتیجهٔ نهایی پایین‌تره":"شرط‌ها برگشت"}</small></div></div>`;
 $("dtop").innerHTML=head+`<div class="dpls">${pls}</div>`+(g.watchers?`<div class="hint" style="text-align:center;margin-top:6px">👀 ${fa(g.watchers)} تماشاچی</div>`:"");DR.tmv=""}
function drRenderDock(g){
 const D=DR,dk=$("ddock");if(!dk||!D)return;
 if(g.status!=="playing"){dk.innerHTML=g.status==="done"||g.status==="cancelled"||g.status==="refunded"?`<button id="dback2" style="margin-top:12px">‹ بازگشت به بازی‌ها</button>`:"";
  const b=$("dback2");if(b)b.onclick=()=>$("dback").click();return}
 if(g.spec){dk.innerHTML=`<div class="dnote">👀 داری تماشا می‌کنی؛ پایین می‌تونی چت کنی</div>`;return}
 const mp=g.players.find(p=>p.id===g.me);
 if(mp&&mp.out){dk.innerHTML=`<div class="dnote">🚪 از بازی خارج شدی؛ شرطت سوخت</div>`;return}
 if(g.drawer===g.me){
  if(g.phase!=="draw"){dk.innerHTML=`<div class="dnote">نقاشی تموم شد 👏</div>`;return}
  dk.innerHTML=`<div class="dword"><small>کلمهٔ تو</small><b>${esc(g.word||"")}</b><small>بدون نوشتن؛ فقط با نقاشی!</small></div>
  <div class="dtools"><div class="dsw" id="dsw">${DCOL.slice(0,13).map((c,i)=>`<a data-c="${i}" style="background:${c};color:${c}"></a>`).join("")}</div>
  <div class="dsz" id="dsz"><a data-w="0"><i style="width:6px;height:6px"></i></a><a data-w="1"><i style="width:12px;height:12px"></i></a><a data-w="2"><i style="width:20px;height:20px"></i></a><a data-e="1">🧽</a><a data-clr="1">🗑</a></div></div>`;
  drSel();return}
 const my=g.my;
 const opts=(g.options||[]).map((w,i)=>{let cls="";
  if(my&&my.opt===i)cls=my.ok?"ok":"no";
  if(g.phase==="result"&&w===g.word)cls="ok";
  return `<button class="dop ${cls}" data-o="${i}" ${(my||g.phase!=="draw"||!g.total)?"disabled":""}>${esc(w)}</button>`}).join("");
 let note;
 if(g.phase==="result")note="دور تموم شد؛ نتیجه بالاست 👆";
 else if(my)note=my.ok?`✅ درست بود! <b>+${fa(my.pts)}</b> امتیاز — منتظر بقیه…`:"❌ غلط بود؛ منتظر پایان دور…";
 else note=g.total?"کدوم گزینه درسته؟ هرچی زودتر، امتیاز بیشتر ⚡":"⏳ منتظر شروع نقاشی…";
 dk.innerHTML=`<div class="dopts">${opts}</div><button class="alt" id="dreport" style="width:100%;margin-top:10px">⚠️ گزارش این نقاشی</button><div class="dnote">${note}</div>`;
 const rb=$("dreport");if(rb)rb.onclick=async()=>{rb.disabled=true;rb.textContent="در حال ارسال گزارش…";
  try{const r=await api("/api/drw/report",{gid:D.gid,rnd:D.rnd});const msgs={duplicate:"این نقاشی را قبلاً گزارش کردی.",no_support:"پشتیبانی در دسترس نیست؛ گزارش ثبت نشد.",db_error:"ثبت گزارش ناموفق بود.",report_unavailable:"الان امکان گزارش وجود ندارد.",no_drawing:"اول باید نقاشی ثبت شده باشد.",left:"تو از بازی خارج شدی.",internal:"خطای داخلی؛ دوباره امتحان کن."};toast(r.error?(msgs[r.error]||"گزارش ارسال نشد."):"✅ گزارش ثبت شد و برای تأیید پشتیبانی رفت؛ نتیجه برات میاد.",3500);}
  catch(e){toast("ارسال گزارش ناموفق بود.")}finally{rb.disabled=false;rb.textContent="⚠️ گزارش این نقاشی"}}
}
function drResultHTML(g){
 const L=g.last_round,me=g.me,last=g.rnd>=g.rounds;
 const rows=L.items.map(it=>`<div class="drow ${it.st==="ok"?"w":it.st==="wrong"?"l":""}"><span class="rk">${it.st==="ok"?"✅":it.st==="wrong"?"❌":"⏳"}</span><span class="nm2">${esc(it.name)}${it.id===me?" (تو)":""}</span><span class="st">${it.st==="ok"?"درست":it.st==="wrong"?"غلط":"جواب نداد"}</span><b>${it.pts?"+"+fa(it.pts):fa(0)}</b></div>`).join("");
 return `<div class="dcard"><small>🎨 نتیجهٔ دور ${fa(L.rnd)} از ${fa(g.rounds)}</small><div class="dwd">${esc(L.word)}</div>
 <div class="drow dr"><span class="rk">✏️</span><span class="nm2">${esc(L.drawer_name)}${L.drawer===me?" (تو)":""} · نقاش</span><span class="st">${L.empty?"نکشید 😅":"نقاشی"}</span><b>${L.drawer_pts?"+"+fa(L.drawer_pts):fa(0)}</b></div>${rows}
 <div class="dnext">${last?"نتیجهٔ نهایی":"دور بعدی"} تا <b id="dres">${fa(g.result_sec)}</b> ثانیه…</div></div>`}
function drFinalHTML(g){
 const me=g.me,w=g.winners||[],net=g.net||{};
 const rk=g.players.slice().sort((a,b)=>b.score-a.score),iw=w.includes(me),medal=["🥇","🥈","🥉","4️⃣"];
 let head;
 if(g.all_zero)head=`<div class="dbig">🤝</div><b>مساوی!</b><small>هیچ‌کس امتیازی نگرفت؛ شرط‌ها برگشت</small>`;
 else if(g.spec)head=`<div class="dbig">🏁</div><b>بازی تموم شد</b><small>برنده: ${esc(g.players.filter(p=>w.includes(p.id)).map(p=>p.name).join("، "))}</small>`;
 else{const n=Number(net[me]||0);head=`<div class="dbig">${iw?"🏆":"😵"}</div><b>${iw?"تو برنده شدی! 🎉":"باختی؛ دفعهٔ بعد جبران کن!"}</b><small>${iw?"+ "+fa(String(Math.max(0,n)))+" الماس خالص":"− "+fa(String(g.bet))+" الماس"}</small>`}
 const rows=rk.map((p,i)=>{const win=!g.all_zero&&w.includes(p.id);
  return `<div class="drow ${g.all_zero?"":win?"w":"l"}"><span class="rk">${medal[i]}</span><span class="nm2">${esc(p.name)}${p.id===me?" (تو)":""}</span><span class="st">${g.all_zero?"مساوی":win?"برنده 🏆":"بازنده"}</span><b>${fa(p.score)}</b></div>`}).join("");
 const prize=g.all_zero?"":`<div class="dnext">🎁 جایزه: <b>${fa(String(Number(g.payout)))}</b> الماس (۵٪ مالیات کسر شد)</div>`;
 const fo=g.forfeit?`<div class="dnext">🚪 یکی از بازیکن‌ها از بازی خارج شد</div>`:"";
 return `<div class="dcard">${head}${fo}<div style="margin-top:8px">${rows}</div>${prize}</div>`}
function drOverlay(g){
 const ov=$("dov");if(!ov)return;DR.tmv="";
 if(g.status==="playing"&&g.phase==="result"&&g.last_round){ov.innerHTML=drResultHTML(g);ov.className="dov on";return}
 if(g.status==="done"){ov.innerHTML=drFinalHTML(g);ov.className="dov on";
  if(!DR.confetti&&!g.all_zero&&(g.winners||[]).includes(g.me)){DR.confetti=true;
   const cl=["#ff1744","#ffd600","#00e5ff","#76ff03","#d500f9","#ff6d00"];
   for(let i=0;i<46;i++){const c=document.createElement("i");c.className="cf";c.style.left=(Math.random()*100)+"%";c.style.background=cl[i%6];c.style.animationDelay=(Math.random()*1.2)+"s";ov.appendChild(c)}}
  return}
 if(g.status==="cancelled"||g.status==="refunded"){ov.innerHTML=`<div class="dcard"><div class="dbig">❌</div><b>بازی لغو شد</b><small>شرط‌ها برگشت</small></div>`;ov.className="dov on";return}
 if(g.status==="lobby"){ov.innerHTML=`<div class="dcard"><div class="dbig">🎨</div><b>منتظر بازیکن‌ها…</b><small>با «شروع بازی» توی گروه یا خودکار شروع می‌شه</small><div class="dnext">مهلت: <b id="dres">${fa(g.left)}</b> ثانیه</div></div>`;ov.className="dov on";return}
 ov.className="dov";ov.innerHTML=""}
/* ---- تایمر (محلی، هر ۰٫۲۵ ثانیه) ---- */
setInterval(()=>{
 const D=DR;if(!D||!D.GS)return;const g=D.GS;
 const run=g.status==="playing"||g.status==="lobby";
 const left=run?Math.max(0,D.LEFT-(Date.now()-D.LAT)/1000):0;
 const tot=g.status==="lobby"?g.lobby_sec:(g.phase==="result"?g.result_sec:g.draw_sec);
 const s=Math.ceil(left),ring=$("dring"),tn=$("dtn"),rs=$("dres");
 if(ring)ring.style.background=`conic-gradient(${(g.phase==="draw"&&left<=8)?"#ff4d6d":"#19d3ff"} ${Math.min(360,(left/Math.max(1,tot))*360)}deg,rgba(255,255,255,.12) 0)`;
 const v=run?fa(s):"";
 if(v!==D.tmv){D.tmv=v;if(tn)tn.textContent=v;if(rs)rs.textContent=v}
 if(g.status==="playing"&&g.phase==="draw"&&s<=5&&s>0&&s!==D.lastSec){D.lastSec=s;SFX.tick()}
},250);

/* ---------- شروع ---------- */
(async()=>{
 const qp=new URLSearchParams(location.search);
 const sp=(tg&&tg.initDataUnsafe&&tg.initDataUnsafe.start_param)||"";
 const g0=parseInt(qp.get("g")||(sp.startsWith("snk_")?sp.slice(4):""),10);
 const d0=parseInt(qp.get("d")||(sp.startsWith("drw_")?sp.slice(4):""),10);
 if(qp.get("tab")&&PANES[qp.get("tab")])VIEW=qp.get("tab");
 try{const s=await api("/api/me");renderAll(s);showTab(VIEW);
  if(d0)openDraw(d0);else if(g0)openGame(g0,false);else if(s.game&&sp==="snk")openGame(s.game.id,false);else if(s.draw&&sp==="drw")openDraw(s.draw.id)}
 catch(e){$("v").className="";$("v").innerHTML=`<div class="msg">${e.message}<br><br>مینی‌اپ رو از داخل ربات باز کن.</div>`}
})();
</script></body></html>
"""
