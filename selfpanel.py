# -*- coding: utf-8 -*-
"""
پنل کامل سلف برای مینی‌اپ (همون تنظیماتی که توی «.پنل» تلگرام هست، با کلید روشن/خاموش).

- فقط یک لایهٔ نازک روی منطق موجوده (selfbot / extras / selfmodes / selffonts)؛ هیچ منطق سلفی دوباره‌نویسی نشده،
  پس هر چی از مینی‌اپ عوض بشه همون لحظه توی پنل تلگرام هم دیده می‌شه (و برعکس).
- احراز هویت و محدودیت نرخ توی miniapp.py انجام می‌شه؛ این‌جا uid همیشه از initData امضاشده میاد.
- دسترسی: فقط کاربری که مدیر سلف رو براش فعال کرده (SelfAccess.enabled) مثل خود پنل ربات.

نصب: فایل کنار miniapp.py توی ریشهٔ ریپو؛ نیاز به تغییر دیگه‌ای نیست (miniapp.py خودش صدا می‌زنه).
"""
import asyncio
import html
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

FT_KEYS = ("always_online", "typing_mode", "pv_lock", "pv_join")
DL_TTL = 300            # لیست گپ‌ها تا ۵ دقیقه نگه‌داشته می‌شه تا انتخاب کاربر قابل‌اعتبارسنجی باشه
DL_LIMIT = 80
_dl_cache = {}          # (uid, kind) -> (ts, {id: (title, kind)})


def _sb():
    import selfbot
    return selfbot


def _ex():
    import extras
    return extras


def _safe_html(s):
    """فقط <b> و <code> مجازه؛ هر چیز دیگه escape می‌شه."""
    t = html.escape(s or "", quote=False)
    t = re.sub(r"&lt;(/?)(b|code)&gt;", r"<\1\2>", t)
    return t


# ---------- وضعیت ----------
def _slot_view(i, s):
    sb = _sb()
    s = s or {}
    word = s.get("word") or ""
    delay = int(s.get("delay", 0) or 0)
    miss = []
    if not word:
        miss.append("word")
    if not s.get("chat"):
        miss.append("chat")
    if delay < sb.MIN_AUTO:
        miss.append("delay")
    return {
        "i": i, "word": word, "title": s.get("title") or "", "has_chat": bool(s.get("chat")),
        "delay": delay, "delay_txt": sb.fmt_delay(delay) if delay else "",
        "pos": s.get("pos", "n") if s.get("pos", "n") in sb.POS else "n",
        "ready": not miss, "missing": miss,
    }


def _tools(ex):
    out = []
    for k in ex.TOOL_ORDER:
        g = ex.TOOL_GUIDES.get(k)
        if g:
            out.append({"k": k, "t": g[0], "b": _safe_html(g[1])})
    return out


def state(uid):
    sb = _sb()
    row = sb.get_row(uid)
    allowed = bool(row and row.enabled)
    bot = sb._botref[0]
    out = {
        "allowed": allowed,
        "configured": bool(sb.configured()),
        "connected": uid in sb._clients,
        "has_session": bool(row and row.session_enc),
        "bot": getattr(bot, "username", "") or "",
    }
    if not allowed:
        return out
    import selffonts
    import selfmodes
    ex = _ex()
    d = sb._pget(uid)
    sl = sb._get_slots(uid)
    st_modes = selfmodes._load(uid)
    cur_font = d.get("font", 1)
    if not (isinstance(cur_font, int) and 1 <= cur_font <= len(sb.CLOCK_FONTS)):
        cur_font = 1
    fm = d.get("font_mode", "ساده")
    if fm not in selffonts.FONTS:
        fm = "ساده"
    spd = d.get("type_speed", 2)
    spd = spd if spd in selffonts.SPEEDS else 2
    fonts = []
    for n in selffonts.names():
        s, tag = selffonts.render("Abc سلام ۱۲۳", n)
        fonts.append({"n": n, "s": s, "tag": tag or ""})
    mu = d.get("mu_ids") or {}
    feats = {}
    for kind, f in sb.FEATS.items():
        w = sb.feat_get(uid, kind)
        feats[kind] = {"label": f["label"], "allowed": w["allowed"], "active": w["active"],
                       "title": w["title"] or "", "every": sb._fmt_interval(kind), "what": sb._feat_what(kind)}
    tg = {k: bool(d.get(k)) for k in FT_KEYS}
    tg.update({k: bool(d.get(k)) for k in ex.TOGGLES})
    out.update({
        "paused": bool(row.paused),
        "toggles": tg,
        "clock": {"on": bool(d.get("clock_on")), "font": cur_font,
                  "fonts": [{"i": i, "name": n, "sample": sb.fmt_clock(i)} for i, (n, _, _) in enumerate(sb.CLOCK_FONTS, 1)]},
        "text": {"mode": fm, "fx": bool(d.get("type_fx")), "speed": spd,
                 "speeds": {str(k): v[0] for k, v in selffonts.SPEEDS.items()}, "fonts": fonts},
        "typing": {"target": d.get("typing_target_name") or "", "has_target": bool(d.get("typing_target_id"))},
        "slots": [_slot_view(i, sl[i]) for i in range(sb.MAX_SLOTS)],
        "pos": sb.POS,
        "min_auto": sb.MIN_AUTO,
        "modes": {m: {"on": bool(st_modes["on"][m]), "n": len(st_modes["t"][m]),
                      "names": [str(x.get("n") or "؟")[:24] for x in list(st_modes["t"][m].values())[:6]],
                      "max": selfmodes.MAX_TARGETS} for m in ("friend", "enemy")},
        "ar_text": d.get("ar_text") or ex.DEFAULT_AR,
        "wl": list(d.get("wl_items") or []),
        "wl_max": ex.WL_MAX,
        "mu": [{"id": str(k), "name": str(v)} for k, v in mu.items()],
        "mu_max": ex.MU_MAX,
        "feats": feats,
        "tools": _tools(ex),
        "meme": _safe_html(selfmodes.meme_help()),
    })
    return out


# ---------- لیست گپ‌ها ----------
async def dialogs(uid, kind="slot", q=""):
    """kind: slot/feat → گروه‌ها و چت ربات‌ها | typing → گروه‌ها و پیوی کاربرها. خروجی: {ok, items} یا {ok False, error}"""
    sb = _sb()
    client = sb._clients.get(uid)
    if not client:
        return {"ok": False, "error": "not_connected"}
    if kind not in ("slot", "feat", "typing"):
        kind = "slot"
    qq = sb.norm(q)
    c = _dl_cache.get((uid, kind))
    if qq and c and time.time() - c[0] < 90:      # جستجوی تایپ‌شده از لیست تازه فیلتر می‌شه؛ هر حرف یه درخواست به تلگرام نمی‌ره
        rows = [{"id": str(cid), "t": v[0], "k": v[1]} for cid, v in c[1].items() if qq in sb.norm(v[0])]
        return {"ok": True, "items": rows[:DL_LIMIT], "total": len(rows)}
    items = {}
    try:
        if kind == "typing":
            for cid, name, is_group in await sb._dialogs(client):
                items[cid] = (str(name)[:60], "group" if is_group else "user")
        else:
            async for dlg in client.iter_dialogs(limit=300):
                if dlg.id == sb._bot_id[0]:
                    continue
                if dlg.is_group:
                    items[dlg.id] = ((dlg.name or str(dlg.id))[:60], "group")
                elif dlg.is_user and getattr(dlg.entity, "bot", False):
                    items[dlg.id] = ((dlg.name or str(dlg.id))[:60], "bot")
    except Exception as e:  # noqa: BLE001
        logger.info("selfpanel dialogs %s: %s", uid, e)
        return {"ok": False, "error": "tg_error"}
    _dl_cache[(uid, kind)] = (time.time(), items)
    rows = [{"id": str(cid), "t": v[0], "k": v[1]} for cid, v in items.items() if not qq or qq in sb.norm(v[0])]
    return {"ok": True, "items": rows[:DL_LIMIT], "total": len(rows)}


def _pick(uid, kind, raw_id):
    """id گپ باید از لیست تازهٔ همین کاربر باشه؛ (id, title) یا None."""
    c = _dl_cache.get((uid, kind))
    if not c or time.time() - c[0] > DL_TTL:
        return None
    try:
        cid = int(raw_id)
    except (TypeError, ValueError):
        return None
    v = c[1].get(cid)
    return (cid, v[0]) if v else None


# ---------- عملیات ----------
def _b(data, k="on"):
    v = data.get(k)
    if not isinstance(v, bool):
        raise ValueError(k)
    return v


def _slot_idx(data):
    i = data.get("i")
    if not isinstance(i, int) or not (0 <= i < _sb().MAX_SLOTS):
        raise ValueError("i")
    return i


async def act(uid, data):
    """یک عملیات رو انجام می‌ده. خروجی: dict شامل state تازه + ok/error/msg."""
    sb = _sb()
    row = sb.get_row(uid)
    if not (row and row.enabled):
        return {"ok": False, "error": "not_allowed", "state": state(uid)}
    a = str(data.get("a") or "")
    msg = None
    try:
        r = await _do(uid, sb, a, data)
        if isinstance(r, dict):          # خطای صریح
            r["state"] = state(uid)
            return r
        msg = r
    except ValueError:
        return {"ok": False, "error": "bad_request", "state": state(uid)}
    except Exception as e:  # noqa: BLE001
        logger.warning("selfpanel act %s %s: %s", uid, a, e)
        return {"ok": False, "error": "server", "state": state(uid)}
    return {"ok": True, "msg": msg, "state": state(uid)}


def _err(code, text=None):
    return {"ok": False, "error": code, "msg": text}


async def _do(uid, sb, a, data):
    ex = _ex()
    import selffonts
    import selfmodes

    if a == "pause":                                  # توقف موقت / فعال‌سازی
        want_paused = _b(data, "paused")
        sb.save_row(uid, paused=want_paused)
        if uid in sb._clients:
            sb._load_runtime(uid, sb.get_row(uid))
        return "⏸ سلف متوقف شد." if want_paused else "▶️ سلف فعال شد."

    if a == "ft":                                     # آنلاین / تایپینگ / قفل پیوی / عضویت اجباری پیوی
        key, on = str(data.get("k")), _b(data)
        if key not in FT_KEYS:
            raise ValueError("k")
        d = sb._pget(uid)
        if key == "typing_mode" and on and not d.get("typing_target_id"):
            return _err("need_target", "اول گپ تایپینگ رو انتخاب کن.")
        d[key] = on
        sb._pput(uid, d)
        if key in ("always_online", "typing_mode"):
            sb._sync_presence(uid)
        return "✅ روشن شد." if on else "⏹ خاموش شد."

    if a == "mt":                                     # سوئیچ‌های امکانات بیشتر
        key, on = str(data.get("k")), _b(data)
        if key not in ex.TOGGLES:
            raise ValueError("k")
        d = sb._pget(uid)
        d[key] = on
        if key == "ar_on" and on and not d.get("ar_text"):
            d["ar_text"] = ex.DEFAULT_AR
        sb._pput(uid, d)
        return "✅ روشن شد." if on else "⏹ خاموش شد."

    if a == "mode":                                   # حالت دوست / دشمن
        m = str(data.get("m"))
        if m not in ("friend", "enemy"):
            raise ValueError("m")
        st = selfmodes._load(uid)
        if data.get("clear") is True:
            st["t"][m] = {}
            selfmodes._save(uid, st)
            return "🗑 همهٔ افراد پاک شدن."
        st["on"][m] = _b(data)
        selfmodes._save(uid, st)
        return "✅ روشن شد." if st["on"][m] else "⏹ خاموش شد."

    if a in ("clock", "clock_font"):                  # ساعت زنده
        if uid not in sb._clients:
            return _err("not_connected", "اول اکانتت رو وصل کن.")
        if a == "clock":
            res = await sb.clock_set(uid, on=_b(data))
        else:
            f = data.get("font")
            if not isinstance(f, int) or not (1 <= f <= len(sb.CLOCK_FONTS)):
                raise ValueError("font")
            res = await sb.clock_set(uid, on=True, font=f)
        if res.startswith(("⚠️", "❌")):
            return _err("clock_failed", res[:160])
        return res.split("\n")[0]

    if a == "font_mode":                              # فونت پیام‌ها
        name = str(data.get("n"))
        if name not in selffonts.FONTS:
            raise ValueError("n")
        d = sb._pget(uid)
        d["font_mode"] = name
        sb._pput(uid, d)
        return "🔤 فونت: " + name
    if a == "type_fx":
        d = sb._pget(uid)
        d["type_fx"] = _b(data)
        sb._pput(uid, d)
        return "⌨️ تایپ حرف‌به‌حرف " + ("روشن شد." if d["type_fx"] else "خاموش شد.")
    if a == "type_speed":
        v = data.get("v")
        if v not in selffonts.SPEEDS:
            raise ValueError("v")
        d = sb._pget(uid)
        d["type_speed"] = v
        sb._pput(uid, d)
        return "سرعت تایپ: " + selffonts.SPEEDS[v][0]

    if a == "slot":                                   # کلمه / زمان / دکمه
        i = _slot_idx(data)
        sl = sb._get_slots(uid)
        f = str(data.get("f"))
        if f == "word":
            w = " ".join(str(data.get("v") or "").split())[:40]
            if not w:
                raise ValueError("v")
            if sb.is_banned_word(w):
                return _err("banned_word", "⛔ این کلمه مجاز نیست.")
            sl[i]["word"] = w
        elif f == "delay":
            v = data.get("v")
            n = sb.parse_delay(str(v)) if not isinstance(v, int) or isinstance(v, bool) else (v if 0 <= v <= sb.MAX_DELAY else None)
            if n is None or 0 < n < sb.MIN_AUTO:
                return _err("bad_delay", f"زمان باید ۰ یا بین {sb.MIN_AUTO} ثانیه تا ۶۰ دقیقه باشه.")
            sl[i]["delay"] = n
        elif f == "pos":
            v = str(data.get("v"))
            if v not in sb.POS:
                raise ValueError("v")
            sl[i]["pos"] = v
        else:
            raise ValueError("f")
        sl[i].setdefault("delay", 0)
        sb._put_slots(uid, sl)
        sb._cur[uid] = i
        return "✅ ذخیره شد."
    if a == "slot_chat":
        i = _slot_idx(data)
        pk = _pick(uid, "slot", data.get("id"))
        if not pk:
            return _err("stale", "لیست قدیمی شده؛ دوباره باز کن.")
        sl = sb._get_slots(uid)
        sl[i]["chat"], sl[i]["title"] = pk[0], pk[1][:40]
        sl[i].setdefault("delay", 0)
        sb._put_slots(uid, sl)
        sb._cur[uid] = i
        return "💬 گپ انتخاب شد."
    if a == "slot_clear":
        i = _slot_idx(data)
        sl = sb._get_slots(uid)
        sl[i] = {}
        sb._put_slots(uid, sl)
        return "🗑 اسلات پاک شد."

    if a == "typing_set":                             # مقصد تایپینگ
        pk = _pick(uid, "typing", data.get("id"))
        client = sb._clients.get(uid)
        if not pk or not client:
            return _err("stale", "لیست قدیمی شده؛ دوباره باز کن.")
        d = sb._pget(uid)
        d["typing_target_id"] = pk[0]
        d["typing_target_name"] = pk[1][:70]
        d["typing_mode"] = True
        sb._pput(uid, d)
        sb._sync_presence(uid)
        return "⌨️ تایپینگ روی «" + pk[1][:30] + "» فعال شد."
    if a == "typing_clear":
        d = sb._pget(uid)
        d.pop("typing_target_id", None)
        d.pop("typing_target_name", None)
        d["typing_mode"] = False
        sb._pput(uid, d)
        sb._sync_presence(uid)
        return "🗑 مقصد حذف شد."

    if a == "ar_text":
        t = str(data.get("v") or "").strip()[:500]
        if not t:
            raise ValueError("v")
        d = sb._pget(uid)
        d["ar_text"] = t
        sb._pput(uid, d)
        return "✅ متن پاسخ خودکار ذخیره شد."
    if a == "wl_add":
        d = sb._pget(uid)
        items = d.get("wl_items") or []
        added = 0
        for x in re.split(r"[,\n،]+", str(data.get("v") or "")):
            x = x.strip()[:40]
            if not x or x.lower() in [i.lower() for i in items] or len(items) >= ex.WL_MAX:
                continue
            items.append(x)
            added += 1
        d["wl_items"] = items
        if added:
            d["wl_on"] = True
        sb._pput(uid, d)
        return f"✅ {added} مورد اضافه شد." if added else _err("nothing", "موردی اضافه نشد (تکراری، خالی یا سقف پر).")
    if a == "wl_del":
        d = sb._pget(uid)
        items = d.get("wl_items") or []
        k = data.get("i")
        if not isinstance(k, int) or not (0 <= k < len(items)):
            raise ValueError("i")
        items.pop(k)
        d["wl_items"] = items
        sb._pput(uid, d)
        return "🗑 حذف شد."
    if a == "mu_add":
        n = await ex._mu_add_text(uid, str(data.get("v") or ""))
        return f"🔇 {n} نفر ساکت شد." if n else _err("nothing", "موردی اضافه نشد (پیدا نشد، تکراری، سقف پر یا اکانت وصل نیست).")
    if a == "mu_del":
        d = sb._pget(uid)
        ids = d.get("mu_ids") or {}
        ids.pop(str(data.get("id")), None)
        d["mu_ids"] = ids
        sb._pput(uid, d)
        return "🔊 سکوت برداشته شد."

    if a == "feat":                                   # کار / درس (فقط اگه پشتیبانی مجوز داده)
        kind = str(data.get("k"))
        if kind not in sb.FEATS:
            raise ValueError("k")
        lb = sb.FEATS[kind]["label"]
        w = sb.feat_get(uid, kind)
        if not w["allowed"]:
            return _err("feat_denied", f"قابلیت «{lb}» برای حساب تو فعال نیست؛ از پشتیبانی بخواه.")
        if _b(data):
            if uid not in sb._clients:
                return _err("not_connected", "اول اکانتت رو وصل کن.")
            pk = _pick(uid, "feat", data.get("id"))
            if not pk:
                return _err("stale", "اول گپ هدف رو از لیست انتخاب کن.")
            sb.feat_save(uid, kind, active=True, chat=pk[0], title=pk[1][:40])
            sb._sync_feat(uid, kind)
            return f"✅ «{lb}» توی «{pk[1][:30]}» روشن شد."
        sb.feat_save(uid, kind, active=False, chat=None, title=None)
        sb._sync_feat(uid, kind)
        return f"⏹ «{lb}» خاموش شد."

    if a == "ping":
        client = sb._clients.get(uid)
        if not client:
            return _err("not_connected", "اکانت سلف وصل نیست.")
        t0 = time.perf_counter()
        try:
            await asyncio.wait_for(client.get_me(), 15)
        except Exception:  # noqa: BLE001
            return _err("tg_error", "پینگ ناموفق بود.")
        return f"🏓 پینگ: {max(1, int((time.perf_counter() - t0) * 1000))} ms"

    if a == "disconnect":                             # همون «قطع اتصال» پنل ربات (خروج کامل از سشن)
        await sb.stop_client(uid, logout=True, restore=True)
        sb.save_row(uid, session_enc=None, phone=None)
        return "🔌 اتصال قطع و سشن بسته شد."

    raise ValueError("a")
