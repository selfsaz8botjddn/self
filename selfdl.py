# -*- coding: utf-8 -*-
"""دانلودر سلف (نسخهٔ ۲۴): یوتیوب، اینستاگرام، تیک‌تاک، پینترست.

دستورها (از extras._dispatch صدا زده می‌شن؛ هر تابع (متن، نگه‌داشتن) برمی‌گردونه):
  .یوتیوب [لینک]   | .youtube [link]   → ارسال توی Saved Messages
  .اینستا [لینک]   | .insta [link]     → ارسال توی همون چت
  .تیک تاک [لینک]  | .tiktok [link]    → ارسال توی همون چت
  .پینترست [لینک]  | .pinterest [link] → ارسال توی همون چت
لینک رو می‌شه با ریپلای روی پیام لینک‌دار هم داد. نوع سایت از روی خود لینک تشخیص داده می‌شه.

موتورها: yt-dlp (ویدیو) و gallery-dl (عکس، آلبوم، استوری، پین).
متغیرهای اختیاری Railway:
  DL_COOKIES / YT_COOKIES / IG_COOKIES / TT_COOKIES / PIN_COOKIES   کوکی Netscape (متن یا مسیر فایل)
  DL_PROXY        پراکسی (مثل http://user:pass@host:port)
  DL_MAX_MB       سقف حجم هر فایل (پیش‌فرض 1500)
  DL_ALL_MAX      سقف تعداد کیفیت در «همه» (پیش‌فرض 8)
"""
import asyncio
import base64
import html as _html
import logging
import os
import re
import shutil
import sys
import tempfile
from urllib.parse import urlparse

import selfbot as sb

logger = logging.getLogger("selfdl")

CMDS = ("yt", "insta", "tiktok", "pinterest")
SITE_FA = {"yt": "یوتیوب", "insta": "اینستاگرام", "tiktok": "تیک‌تاک", "pinterest": "پینترست"}
SITE_ICON = {"yt": "▶️", "insta": "📸", "tiktok": "🎵", "pinterest": "📌"}
SITE_HOSTS = {
    "yt": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    "insta": ("instagram.com", "instagr.am"),
    "tiktok": ("tiktok.com",),
    "pinterest": ("pinterest.com", "pin.it"),
}
COOKIE_ENV = {"yt": "YT_COOKIES", "insta": "IG_COOKIES", "tiktok": "TT_COOKIES", "pinterest": "PIN_COOKIES"}

VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
AUDIO_EXT = (".m4a", ".mp3", ".opus", ".ogg", ".aac")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


DL_MAX_BYTES = _env_int("DL_MAX_MB", 1500) * 1024 * 1024
DL_ALL_MAX = max(1, _env_int("DL_ALL_MAX", 8))
DL_TIMEOUT = 40 * 60
ALBUM_MAX = 10

_busy = set()                         # هر کاربر هم‌زمان فقط یک دانلود
_sem = asyncio.Semaphore(2)           # سقف دانلود هم‌زمان کل سرور


class DLError(Exception):
    pass


# ---------- لینک‌ها ----------
def _host(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def site_of(url):
    h = _host(url)
    for site, hosts in SITE_HOSTS.items():
        if any(h == x or h.endswith("." + x) or (x.endswith(".") and x in h) for x in hosts):
            return site
    return None


def find_urls(text):
    out = []
    for tok in re.split(r"\s+", text or ""):
        tok = tok.strip("()[]<>«»\"'.,،؛!؟")
        if not tok:
            continue
        if re.match(r"^https?://", tok, re.I):
            out.append(tok)
        elif re.match(r"^(?:[\w-]+\.)+[a-z]{2,}(?:/|$)", tok, re.I):
            out.append("https://" + tok)
    return out


def _strip_urls(text):
    return " ".join(t for t in re.split(r"\s+", text or "") if t and not find_urls(t))


async def _reply_urls(r):
    urls = []
    if r is None:
        return urls
    try:
        for ent, txt in (r.get_entities_text() or []):
            u = getattr(ent, "url", None) or txt
            urls += find_urls(u)
    except Exception:  # noqa: BLE001
        pass
    urls += find_urls(r.raw_text or "")
    try:     # دکمه‌های لینکی زیر پیام
        for row in (r.buttons or []):
            for b in row:
                if getattr(b, "url", None):
                    urls += find_urls(b.url)
    except Exception:  # noqa: BLE001
        pass
    seen, res = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            res.append(u)
    return res


# ---------- ابزارهای کمکی ----------
def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg")


def _cookie_file(site, td):
    """کوکی Netscape از متغیر محیطی (متن، base64 یا مسیر فایل) → فایل موقت؛ وگرنه None."""
    for name in (COOKIE_ENV.get(site), "DL_COOKIES"):
        v = os.environ.get(name, "").strip() if name else ""
        if not v:
            continue
        p = os.path.join(td, f"cookies_{site}.txt")
        try:
            if os.path.isfile(v):
                shutil.copy(v, p)
                return p
            if "\t" not in v and "\\t" not in v and "Netscape" not in v and re.fullmatch(r"[A-Za-z0-9+/=\s]+", v):
                try:
                    v = base64.b64decode(v).decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001
                    pass
            if "\n" not in v:
                v = v.replace("\\n", "\n")
            v = v.replace("\\t", "\t")
            with open(p, "w", encoding="utf-8") as f:
                f.write(v if v.endswith("\n") else v + "\n")
            return p
        except Exception as e:  # noqa: BLE001
            logger.info("cookie file %s: %s", name, e)
    return None


def _media_files(d):
    res = []
    for root, _dirs, names in os.walk(d):
        for n in names:
            if n.lower().endswith(VIDEO_EXT + AUDIO_EXT + IMAGE_EXT) and not n.endswith(".part"):
                res.append(os.path.join(root, n))
    res.sort(key=lambda p: (os.path.getmtime(p), p))
    return res


async def _in_thread(fn, *a):
    return await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, fn, *a), DL_TIMEOUT)


async def _status(ev, text):
    try:
        await ev.edit(sb.fancy_html(text), parse_mode="html")
    except Exception:  # noqa: BLE001
        pass


def _fmt_size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024
    return ""


async def _run_ff(ff, *args, timeout=60):
    proc = await asyncio.create_subprocess_exec(ff, *args, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, ""
    return proc.returncode, (err or b"").decode("utf-8", "ignore")


async def _probe(ff, path):
    """(مدت ثانیه، عرض، ارتفاع) از خروجی ffmpeg -i (ffprobe لازم نیست)."""
    if not ff:
        return 0, 0, 0
    _, err = await _run_ff(ff, "-hide_banner", "-i", path)
    dur = w = h = 0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        dur = int(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
    m = re.search(r"Video:.*?[ ,](\d{2,5})x(\d{2,5})[ ,\[]", err)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    return dur, w, h


async def _thumb(ff, path, dur):
    if not ff:
        return None
    out = path + ".thumb.jpg"
    rc, _ = await _run_ff(ff, "-y", "-ss", str(min(1, dur // 2) if dur else 0), "-i", path, "-frames:v", "1",
                          "-vf", "scale=320:-2", out)
    return out if rc == 0 and os.path.exists(out) else None


async def _to_jpg(ff, path):
    """webp/heic/bmp → jpg (تلگرام اینا رو عکس نمی‌شناسه)."""
    if not ff or path.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
        return path
    out = os.path.splitext(path)[0] + "_c.jpg"
    rc, _ = await _run_ff(ff, "-y", "-i", path, "-q:v", "2", out)
    return out if rc == 0 and os.path.exists(out) else path


def _caption(text):
    text = " ".join((text or "").split())
    return _html.escape(text[:300], quote=False) if text else ""


# ---------- ارسال ----------
async def _send_one(client, dest, path, caption, ff, reply_to=None, title=None, uploader=None):
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo
    ext = os.path.splitext(path)[1].lower()
    kw = {"caption": caption or None, "parse_mode": "html", "reply_to": reply_to}
    if ext in VIDEO_EXT:
        dur, w, h = await _probe(ff, path)
        thumb = await _thumb(ff, path, dur)
        attrs = [DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True)] if w and h else None
        await client.send_file(dest, path, attributes=attrs, thumb=thumb, supports_streaming=True, **kw)
    elif ext in AUDIO_EXT:
        dur, _, _ = await _probe(ff, path)
        attrs = [DocumentAttributeAudio(duration=dur, title=(title or "")[:60] or None,
                                        performer=(uploader or "")[:60] or None)]
        await client.send_file(dest, path, attributes=attrs, **kw)
    elif ext in IMAGE_EXT:
        path = await _to_jpg(ff, path)
        big = os.path.getsize(path) > 9.5 * 1024 * 1024
        await client.send_file(dest, path, force_document=big, **kw)
    else:
        await client.send_file(dest, path, force_document=True, **kw)


async def _deliver(client, dest, files, caption, ff, reply_to=None):
    """عکس‌ها آلبوم‌های ۱۰تایی، ویدیوها تکی با مشخصات. → (تعداد ارسال‌شده، ردشده‌های حجم)."""
    ok = [f for f in files if os.path.getsize(f) <= DL_MAX_BYTES]
    skipped = len(files) - len(ok)
    imgs = [f for f in ok if f.lower().endswith(IMAGE_EXT) and not f.lower().endswith(".gif")]
    rest = [f for f in ok if f not in imgs]
    sent, first = 0, True
    if len(imgs) == 1 and not rest:
        await _send_one(client, dest, imgs[0], caption, ff, reply_to)
        return 1, skipped
    imgs = [await _to_jpg(ff, f) for f in imgs]
    for i in range(0, len(imgs), ALBUM_MAX):
        chunk = imgs[i:i + ALBUM_MAX]
        if len(chunk) == 1:
            await _send_one(client, dest, chunk[0], caption if first else "", ff, reply_to)
        else:
            await client.send_file(dest, chunk, caption=(caption or None) if first else None, parse_mode="html",
                                   reply_to=reply_to)
        first = False
        sent += len(chunk)
    for f in rest:
        await _send_one(client, dest, f, caption if first else "", ff, reply_to)
        first = False
        sent += 1
    return sent, skipped


# ---------- yt-dlp ----------
def _ydl_base(site, td, ff):
    o = {"quiet": True, "no_warnings": True, "noprogress": True, "restrictfilenames": True,
         "socket_timeout": 30, "retries": 3, "fragment_retries": 3, "concurrent_fragment_downloads": 4,
         "merge_output_format": "mp4", "paths": {"home": td, "temp": td}}
    if ff:
        o["ffmpeg_location"] = ff
    ck = _cookie_file(site, td)
    if ck:
        o["cookiefile"] = ck
    px = os.environ.get("DL_PROXY", "").strip()
    if px:
        o["proxy"] = px
    for name in ("deno", "node"):          # یوتیوب برای حل چالش به یه JS runtime نیاز داره
        if shutil.which(name):
            o["js_runtimes"] = {name: {}}
            break
    return o


def _ydl_info(url, td, ff, site):
    import yt_dlp
    o = _ydl_base(site, td, ff)
    o.update({"skip_download": True, "noplaylist": True})
    with yt_dlp.YoutubeDL(o) as y:
        info = y.extract_info(url, download=False)
    if info and info.get("_type") == "playlist":
        ents = [e for e in (info.get("entries") or []) if e]
        info = ents[0] if ents else None
    if not info:
        raise DLError("اطلاعاتی از لینک گرفته نشد")
    return info


def _ydl_download(url, td, ff, site, fmt, tag):
    import yt_dlp
    d = os.path.join(td, f"yt_{tag}")
    os.makedirs(d, exist_ok=True)
    o = _ydl_base(site, td, ff)
    o.update({"format": fmt, "noplaylist": True, "paths": {"home": d, "temp": d},
              "outtmpl": "%(id)s_" + str(tag) + ".%(ext)s"})
    with yt_dlp.YoutubeDL(o) as y:
        y.extract_info(url, download=True)
    files = _media_files(d)
    if not files:
        raise DLError("فایلی دانلود نشد")
    return max(files, key=os.path.getsize)


def _ydl_generic(site, url, td, ff):
    import yt_dlp
    d = os.path.join(td, "yd")
    os.makedirs(d, exist_ok=True)
    o = _ydl_base(site, td, ff)
    o.update({"paths": {"home": d, "temp": d}, "outtmpl": "%(id)s_%(autonumber)s.%(ext)s",
              "format": "bv*[vcodec^=avc1]+ba/b[vcodec^=avc1]/bv*+ba/b", "noplaylist": False,
              "playlist_items": "1-10"})
    with yt_dlp.YoutubeDL(o) as y:
        info = y.extract_info(url, download=True)
    files = _media_files(d)
    if not files:
        raise DLError("فایلی دانلود نشد")
    cap = ""
    if info:
        cap = (info.get("description") or "") if site in ("insta", "tiktok") else (info.get("title") or "")
    return files, cap


def _fsize(f, dur):
    s = f.get("filesize") or f.get("filesize_approx") or 0
    if not s and f.get("tbr") and dur:
        s = f["tbr"] * 1000 / 8 * dur
    return int(s or 0)


def qualities(info):
    """[(ارتفاع، حجم تخمینی یا 0)] صعودی، با احتساب بهترین صدا برای فرمت‌های بدون صدا."""
    fm = info.get("formats") or []
    dur = info.get("duration") or 0
    has = lambda c: c not in (None, "none")  # noqa: E731
    a_size = max((_fsize(f, dur) for f in fm if has(f.get("acodec")) and not has(f.get("vcodec"))), default=0)
    best = {}
    for f in fm:
        h = f.get("height")
        if not h or not has(f.get("vcodec")):
            continue
        s = _fsize(f, dur)
        tot = s + (0 if has(f.get("acodec")) or not s else a_size)
        if h not in best or (tot and (not best[h] or tot < best[h])):
            best[h] = tot
    return sorted(best.items())


def pick_height(quals, want, limit):
    """کیفیت خواسته (یا نزدیک‌ترین پایین‌تر)؛ پیش‌فرض بالاترین کیفیتی که تو سقف حجم جا بشه."""
    if not quals:
        return None
    if want:
        lower = [h for h, _ in quals if h <= want]
        return lower[-1] if lower else quals[0][0]
    fit = [h for h, s in quals if s == 0 or s <= limit]
    return fit[-1] if fit else quals[0][0]


def _fmt_for(h):
    return (f"bv*[height={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height={h}]+ba/b[height={h}]"
            f"/bv*[height<={h}]+ba/b[height<={h}]")


def parse_yt_opts(rest):
    """بعد از حذف لینک: کیفیت (720 / 1080p / 4k) یا همه / صدا / لیست."""
    mode, want = "best", None
    for t in sb.fa2en(sb.norm(rest)).split():
        t = t.lower()
        m = re.fullmatch(r"(\d{3,4})p?", t)
        if m:
            want = int(m.group(1))
        elif t in ("4k", "uhd"):
            want = 2160
        elif t in ("2k",):
            want = 1440
        elif t in ("همه", "تمام", "all", "full"):
            mode = "all"
        elif t in ("صدا", "audio", "mp3", "music", "آهنگ", "اهنگ"):
            mode = "audio"
        elif t in ("لیست", "list", "کیفیت", "کیفیت‌ها", "کیفیتها", "qualities"):
            mode = "list"
    return mode, want


def _resolve_sync(url):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl()


# ---------- gallery-dl ----------
async def _gallery(site, url, td):
    d = os.path.join(td, "gd")
    os.makedirs(d, exist_ok=True)
    cmd = [sys.executable, "-m", "gallery_dl", "-D", d, "--range", f"1-{ALBUM_MAX}"]
    ck = _cookie_file(site, td)
    if ck:
        cmd += ["--cookies", ck]
    px = os.environ.get("DL_PROXY", "").strip()
    if px:
        cmd += ["--proxy", px]
    cmd.append(url)
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), 600)
    except asyncio.TimeoutError:
        proc.kill()
        raise DLError("زمان دانلود تموم شد")
    files = _media_files(d)
    if not files:
        raise DLError((err or b"").decode("utf-8", "ignore")[-300:] or "gallery-dl: چیزی پیدا نشد")
    return files, ""


# ---------- پیام خطا ----------
def explain(site, errs):
    t = " | ".join(str(e) for e in errs).lower()
    logger.info("dl fail %s: %s", site, t[:400])
    if "no module named" in t:
        return "❌ yt-dlp / gallery-dl نصب نیست؛ requirements.txt رو آپدیت و دوباره دیپلوی کن."
    if site == "yt" and any(k in t for k in ("not a bot", "sign in to confirm", "confirm you", "cookies")):
        return "❌ یوتیوب برای IP سرور «تأیید ربات نبودن» می‌خواد. کوکی اکانت یوتیوب رو توی متغیر YT_COOKIES (یا DL_COOKIES) بذار."
    if any(k in t for k in ("login", "log in", "logged in", "authentication", "rate-limit", "rate limit", "401", "403",
                            "checkpoint", "cookies")):
        env = COOKIE_ENV.get(site, "DL_COOKIES")
        return f"❌ {SITE_FA[site]} برای این لینک ورود می‌خواد (استوری و بعضی پست‌ها). کوکی اکانت رو توی متغیر {env} (یا DL_COOKIES) بذار."
    if any(k in t for k in ("private", "not available", "unavailable", "removed", "deleted", "404", "not found",
                            "does not exist", "no video")):
        return "❌ این لینک خصوصی، حذف‌شده یا در دسترس نیست."
    if "unsupported url" in t:
        return "❌ این نوع لینک پشتیبانی نمی‌شه."
    if "ffmpeg" in t:
        return "❌ ffmpeg پیدا نشد؛ imageio-ffmpeg رو نصب کن یا ffmpeg سیستم رو اضافه کن."
    first = str(errs[0]).strip().splitlines()[-1][:120] if errs else ""
    return "❌ دانلود انجام نشد." + (f"\n{first}" if first else "")


def _usage(cmd):
    return {"yt": "▶️ مثل: .یوتیوب https://youtu.be/xxxx   (یا .youtube [لینک])\n"
                  "کیفیت: .یوتیوب 720 [لینک] | همهٔ کیفیت‌ها: .یوتیوب همه [لینک] | فقط صدا: .یوتیوب صدا [لینک] | لیست: .یوتیوب لیست [لینک]",
            "insta": "📸 مثل: .اینستا https://www.instagram.com/reel/xxxx   (یا .insta [لینک])",
            "tiktok": "🎵 مثل: .تیک تاک https://vm.tiktok.com/xxxx   (یا .tiktok [لینک])",
            "pinterest": "📌 مثل: .پینترست https://pin.it/xxxx   (یا .pinterest [لینک])",
            }[cmd] + "\nیا روی پیام لینک‌دار ریپلای کن و فقط دستور رو بنویس."


# ---------- یوتیوب ----------
async def _job_youtube(client, ev, url, rest, td, ff):
    mode, want = parse_yt_opts(rest)
    info = await _in_thread(_ydl_info, url, td, ff, "yt")
    if info.get("live_status") in ("is_live", "is_upcoming"):
        return "❌ لایو و ویدیوی زمان‌بندی‌شده پشتیبانی نمی‌شه.", False
    title = info.get("title") or "video"
    uploader = info.get("uploader") or info.get("channel") or ""
    quals = qualities(info)
    qline = " · ".join(f"{h}p" for h, _ in quals) or "—"

    if mode == "list":
        lines = [f"▶️ {title[:80]}", "کیفیت‌های موجود:"]
        for h, s in quals:
            lines.append(f"• {h}p" + (f"  ≈ {_fmt_size(s)}" if s else ""))
        lines.append("\nمثال: .یوتیوب 720 [لینک]  |  .یوتیوب همه [لینک]")
        return "\n".join(lines), True

    jobs = []     # (برچسب، فرمت، تگ)
    if mode == "audio":
        jobs.append(("🎧 صدا", "bestaudio[ext=m4a]/bestaudio/best", "a"))
    elif not quals:
        jobs.append(("", "b", "b"))
    elif mode == "all":
        hs = [h for h, s in quals if s == 0 or s <= DL_MAX_BYTES][-DL_ALL_MAX:] or [quals[0][0]]
        jobs += [(f"{h}p", _fmt_for(h), str(h)) for h in hs]
    else:
        h = pick_height(quals, want, DL_MAX_BYTES)
        jobs.append((f"{h}p", _fmt_for(h), str(h)))

    sent, skipped, errs = 0, 0, []
    for i, (label, fmt, tag) in enumerate(jobs, 1):
        await _status(ev, f"⬇️ دانلود {label or 'ویدیو'}" + (f" ({i}/{len(jobs)})" if len(jobs) > 1 else "") + "…")
        try:
            path = await _in_thread(_ydl_download, url, td, ff, "yt", fmt, tag)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            errs.append(e)
            logger.info("yt download %s: %s", label, e)
            continue
        if os.path.getsize(path) > DL_MAX_BYTES:
            skipped += 1
            os.remove(path)
            continue
        await _status(ev, f"⬆️ ارسال {label or 'ویدیو'}" + (f" ({i}/{len(jobs)})" if len(jobs) > 1 else "") + "…")
        cap = f"<b>{_html.escape(title[:200], quote=False)}</b>"
        if label and mode != "audio":
            cap += f"\n🎞 {label}"
        if mode != "audio" and len(quals) > 1:
            cap += f"\n📊 {qline}"
        await _send_one(client, "me", path, cap, ff, title=title, uploader=uploader)
        sent += 1
        try:
            os.remove(path)
        except OSError:
            pass
    if not sent:
        if skipped:
            return f"❌ حجم فایل بیشتر از سقف {_fmt_size(DL_MAX_BYTES)} بود (DL_MAX_MB).", False
        return explain("yt", errs), False
    note = f"✅ {sent} فایل توی Saved Messages ذخیره شد."
    if skipped:
        note += f"\n⚠️ {skipped} کیفیت به‌خاطر حجم رد شد."
    return note, False


# ---------- اینستا / تیک‌تاک / پینترست ----------
async def _fetch_generic(site, url, td, ff):
    errs = []
    order = ("gallery", "ytdlp") if site == "pinterest" else ("ytdlp", "gallery")
    for be in order:
        try:
            if be == "ytdlp":
                files, cap = await _in_thread(_ydl_generic, site, url, td, ff)
            else:
                files, cap = await _gallery(site, url, td)
            if files:
                return files, cap
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            errs.append(e)
            logger.info("dl %s via %s: %s", site, be, e)
    raise DLError(explain(site, errs))


async def _job_generic(client, ev, site, url, td, ff):
    if _host(url) == "pin.it":
        try:
            url = await _in_thread(_resolve_sync, url)
        except Exception as e:  # noqa: BLE001
            logger.info("pin.it resolve: %s", e)
    files, cap = await _fetch_generic(site, url, td, ff)
    await _status(ev, f"⬆️ ارسال ({len(files)} فایل)…")
    reply_to = ev.reply_to_msg_id if ev.is_reply else None
    sent, skipped = await _deliver(client, ev.chat_id, files, _caption(cap), ff, reply_to)
    if not sent:
        return f"❌ حجم فایل بیشتر از سقف {_fmt_size(DL_MAX_BYTES)} بود (DL_MAX_MB).", False
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    if skipped:
        await client.send_message(ev.chat_id, f"⚠️ {skipped} فایل به‌خاطر حجم رد شد.")
    return None, False


# ---------- نقطهٔ ورود ----------
async def run(uid, ev, cmd, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    arg = arg or ""
    if cmd == "tiktok":
        arg = re.sub(r"^(?:تاک|tok)\s*", "", arg, flags=re.I)      # «.تیک تاک» دو کلمه‌ست
    urls = find_urls(arg)
    if not urls and ev.is_reply:
        urls = await _reply_urls(await ev.get_reply_message())
    if not urls:
        return _usage(cmd), True
    url = next((u for u in urls if site_of(u)), None)
    if not url:
        return "❌ فقط لینک یوتیوب، اینستاگرام، تیک‌تاک و پینترست پشتیبانی می‌شه.", False
    site = site_of(url)
    if uid in _busy:
        return "⏳ یه دانلود در حال انجامه؛ صبر کن تموم شه.", False
    _busy.add(uid)
    try:
        async with _sem:
            await _status(ev, f"⏳ در حال بررسی لینک {SITE_FA[site]}…")
            ff = _ffmpeg()
            with tempfile.TemporaryDirectory(prefix="dl_") as td:
                try:
                    if site == "yt":
                        return await _job_youtube(client, ev, url, _strip_urls(arg), td, ff)
                    return await _job_generic(client, ev, site, url, td, ff)
                except DLError as e:
                    return str(e) if str(e).startswith("❌") else explain(site, [e]), False
                except asyncio.TimeoutError:
                    return "❌ زمان دانلود تموم شد.", False
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    return explain(site, [e]), False
    finally:
        _busy.discard(uid)
