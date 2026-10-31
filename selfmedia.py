# -*- coding: utf-8 -*-
"""ابزارهای رسانه‌ای سلف (نسخهٔ ۲۵): ساخت استیکر و ویس به متن.

  .استیکر           (ریپلای روی عکس / ویدیو / گیف / ویدیو مسیج / متن) → همون پیام استیکر می‌شه و ریپلای می‌خوره
  .استیکر متن سلام  (بدون ریپلای) → استیکر متنی
  .استیکر           (ریپلای روی استیکر) → تبدیل به عکس (PNG) یا ویدیو (mp4)
  .متن ویس          (ریپلای روی ویس / ویدیو مسیج) → متن ویس به‌صورت ریپلای
  .ویس سلام         (یا ریپلای روی متن و .ویس) → متن به ویس با Edge TTS؛ .ویس مرد ... | .ویس زن ... | سریع / آهسته
  .میم متن بالا | متن پایین   (ریپلای روی عکس / ویدیو / گیف) → میم؛ عکس ← عکس، ویدیو و گیف ← ویدیو (همین‌طور: .ویدیو میم ...)
از extras._dispatch صدا زده می‌شن؛ هر تابع (متن، نگه‌داشتن) برمی‌گردونه.
"""
import asyncio
import logging
import mimetypes
import os
import re
import tempfile

import selfbot as sb
import selfdl as dl

logger = logging.getLogger("selfmedia")

CMDS = ("sticker", "stt", "tts", "meme")

# اگه پایتون سرور این پسوندها رو نشناسه، تلگرام فایل رو «استیکر» نمی‌گیره و عمومی می‌فرستتش
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/webm", ".webm")

ALT_EMOJI = "🙂"
STICKER_MAX_BYTES = 512 * 1024         # استیکر ثابت
VSTICKER_MAX_BYTES = 256 * 1024        # استیکر ویدیویی
VSTICKER_SECONDS = 3
SRC_MAX_BYTES = 40 * 1024 * 1024
TEXT_MAX = 200
STT_WAIT = 40                           # حداکثر ثانیهٔ انتظار برای رونویسی

_busy = set()
_sem = asyncio.Semaphore(2)
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_CANDIDATES = (
    os.environ.get("STICKER_FONT", ""),
    os.path.join(_HERE, "fonts", "DejaVuSans-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _mime(m):
    return (getattr(getattr(m, "file", None), "mime_type", "") or "").lower()


def _kind(r):
    """نوع پیام ریپلای‌شده: image / video / sticker_img / sticker_vid / sticker_anim / text / None."""
    if r is None:
        return None
    mime = _mime(r)
    if getattr(r, "sticker", None):
        if mime == "application/x-tgsticker":
            return "sticker_anim"
        return "sticker_vid" if mime.startswith("video/") else "sticker_img"
    if getattr(r, "video", None) or getattr(r, "gif", None) or getattr(r, "video_note", None) or mime.startswith("video/"):
        return "video"
    if getattr(r, "photo", None) or mime.startswith("image/"):
        return "image"
    if (r.raw_text or "").strip():
        return "text"
    return None


# ---------- پردازش تصویر ----------
def _load_font(size, basic):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if p and os.path.isfile(p):
            try:
                if basic:
                    return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.BASIC)
                return ImageFont.truetype(p, size)
            except Exception as e:  # noqa: BLE001
                logger.info("font %s: %s", p, e)
    try:
        return ImageFont.load_default(size)          # Pillow>=10.1: اندازه‌دار (فقط لاتین)
    except TypeError:
        return ImageFont.load_default()


def _has_font():
    return any(p and os.path.isfile(p) for p in _FONT_CANDIDATES)


def _shaper():
    """(تابع شکل‌دهی، BASIC؟). اگه raqm باشه خودش حروف فارسی رو می‌چسبونه؛ وگرنه arabic-reshaper + bidi."""
    try:
        from PIL import features
        if features.check("raqm"):
            return (lambda t: t), False
    except Exception:  # noqa: BLE001
        pass
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return (lambda t: get_display(arabic_reshaper.reshape(t))), True
    except Exception:  # noqa: BLE001
        return (lambda t: t), False


def _save_webp(im, out, limit=STICKER_MAX_BYTES):
    for q in (95, 85, 70, 55, 40, 25):
        im.save(out, "WEBP", quality=q, method=4)
        if os.path.getsize(out) <= limit:
            return True
    return os.path.getsize(out) <= limit


def image_to_sticker(src, out):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    w, h = im.size
    s = 512 / max(w, h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    if not _save_webp(im, out):
        raise dl.DLError("❌ حجم استیکر از حد مجاز تلگرام بیشتر شد.")


def text_to_sticker(text, out):
    from PIL import Image, ImageDraw
    text = "\n".join(" ".join(p.split()) for p in (text or "").strip()[:TEXT_MAX].splitlines())
    if not text.strip():
        raise dl.DLError("❌ متنی برای ساخت استیکر پیدا نشد.")
    shape, basic = _shaper()
    box = 480
    size = 150
    while True:
        font = _load_font(size, basic)
        stroke = max(2, size // 12)
        lines = []
        for para in text.splitlines() or [text]:
            cur = ""
            for word in para.split():
                trial = (cur + " " + word).strip()
                if cur and font.getlength(shape(trial)) + 2 * stroke > box:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
        lh = int(size * 1.3)
        widest = max((font.getlength(shape(ln)) for ln in lines), default=0) + 2 * stroke
        if (widest <= box and lh * len(lines) <= box) or size <= 22:
            break
        size -= 6
    im = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = (512 - lh * len(lines)) / 2
    for ln in lines:
        sh = shape(ln)
        x = (512 - font.getlength(sh)) / 2
        d.text((x, y), sh, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += lh
    if not _save_webp(im, out):
        raise dl.DLError("❌ حجم استیکر از حد مجاز تلگرام بیشتر شد.")


# ---------- ویدیو ----------
async def video_to_sticker(ff, src, out):
    """webm VP9 بدون صدا، ضلع بلندتر ۵۱۲، ≤۳ ثانیه، ≤۲۵۶ کیلوبایت."""
    vf = "scale='if(gte(iw,ih),512,-2)':'if(gte(iw,ih),-2,512)',fps=30,format=yuv420p"
    last = ""
    for br in (600, 380, 240, 150, 90):
        rc, err = await dl._run_ff(ff, "-y", "-i", src, "-t", str(VSTICKER_SECONDS), "-an", "-vf", vf,
                                   "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-b:v", f"{br}k",
                                   "-maxrate", f"{br}k", "-bufsize", f"{br * 2}k", "-deadline", "good",
                                   "-cpu-used", "5", "-row-mt", "1", out, timeout=240)
        last = err
        if rc == 0 and os.path.exists(out) and os.path.getsize(out) <= VSTICKER_MAX_BYTES:
            return
    if "unknown encoder" in last.lower() or "libvpx" in last.lower() and "not" in last.lower():
        raise dl.DLError("❌ این ffmpeg انکودر VP9 نداره؛ ffmpeg سیستم (با libvpx) رو نصب کن.")
    raise dl.DLError("❌ تبدیل به استیکر ویدیویی انجام نشد.")


async def vsticker_to_mp4(ff, src, out):
    rc, _ = await dl._run_ff(ff, "-y", "-c:v", "libvpx-vp9", "-i", src, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", "-an", out, timeout=120)
    if rc != 0 or not os.path.exists(out):
        # بعضی بیلدها دیکودر libvpx-vp9 ندارن؛ بدون اجبار دیکودر دوباره امتحان
        rc, _ = await dl._run_ff(ff, "-y", "-i", src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", out, timeout=120)
    if rc != 0 or not os.path.exists(out):
        raise dl.DLError("❌ تبدیل استیکر به ویدیو انجام نشد.")


# ---------- ارسال ----------
async def _send_sticker(client, chat, path, reply_to, video=False, ff=None):
    from telethon.tl.types import (DocumentAttributeImageSize, DocumentAttributeSticker,
                                   DocumentAttributeVideo, InputStickerSetEmpty)
    attrs = [DocumentAttributeSticker(alt=ALT_EMOJI, stickerset=InputStickerSetEmpty())]
    if video:
        dur, w, h = await dl._probe(ff, path)
        attrs.append(DocumentAttributeVideo(duration=max(dur, 1), w=w or 512, h=h or 512))
    else:
        from PIL import Image
        with Image.open(path) as im:
            attrs.append(DocumentAttributeImageSize(*im.size))
    # force_document=True تلگرام رو مجبور می‌کرد «فایل» بفرسته و ویژگی استیکر نادیده گرفته می‌شد
    await client.send_file(chat, path, attributes=attrs, force_document=False, reply_to=reply_to)


# ---------- .استیکر ----------
async def cmd_sticker(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    arg = (arg or "").strip()
    r = await ev.get_reply_message() if ev.is_reply else None
    arg_text = re.sub(r"^(?:متن|text)\s+", "", arg, flags=re.I).strip() if arg else ""
    kind = "text" if (r is None and arg_text) else _kind(r)
    if kind is None:
        return ("🧷 روی عکس، ویدیو، گیف یا متن ریپلای کن و بنویس: .استیکر\n"
                "یا بدون ریپلای: .استیکر متن سلام"), True
    if kind == "sticker_anim":
        return "❌ استیکر متحرک (TGS) پشتیبانی نمی‌شه؛ استیکر ثابت یا ویدیویی بفرست.", False
    src_size = getattr(getattr(r, "file", None), "size", 0) or 0
    if r is not None and kind != "text" and src_size > SRC_MAX_BYTES:
        return "❌ فایل بزرگ‌تر از 40 مگابایته.", False
    ff = dl._ffmpeg()
    if kind in ("video", "sticker_vid") and not ff:
        return "❌ ffmpeg نصب نیست؛ imageio-ffmpeg رو نصب کن و دوباره دیپلوی کن.", False
    reply_to = r.id if r is not None else None
    await dl._status(ev, "⏳ در حال ساخت…")
    with tempfile.TemporaryDirectory(prefix="stk_") as td:
        if kind == "text":
            text = arg_text if (r is None) else (r.raw_text or "")
            out = os.path.join(td, "text.webp")
            await asyncio.get_running_loop().run_in_executor(None, text_to_sticker, text, out)
            await _send_sticker(client, ev.chat_id, out, reply_to)
        else:
            src = await asyncio.wait_for(client.download_media(r, file=os.path.join(td, "in")), 180)
            if not src:
                return "❌ دانلود فایل انجام نشد.", False
            if kind == "image":
                out = os.path.join(td, "img.webp")
                await asyncio.get_running_loop().run_in_executor(None, image_to_sticker, src, out)
                await _send_sticker(client, ev.chat_id, out, reply_to)
            elif kind == "sticker_img":                       # استیکر ثابت → عکس PNG
                from PIL import Image
                out = os.path.join(td, "sticker.png")
                Image.open(src).convert("RGBA").save(out, "PNG")
                await client.send_file(ev.chat_id, out, reply_to=reply_to, force_document=False)
            elif kind == "video":
                out = os.path.join(td, "vid.webm")
                await video_to_sticker(ff, src, out)
                await _send_sticker(client, ev.chat_id, out, reply_to, video=True, ff=ff)
            else:                                             # استیکر ویدیویی → mp4
                out = os.path.join(td, "sticker.mp4")
                await vsticker_to_mp4(ff, src, out)
                await dl._send_one(client, ev.chat_id, out, "", ff, reply_to)
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    return None, False


# ---------- .میم ----------
MEME_TEXT_MAX = 120
MEME_VIDEO_SIDE = 720                  # ضلع بلندتر ویدیوی خروجی
MEME_VIDEO_SECONDS = 60
MEME_USAGE = ("😂 روی عکس، ویدیو یا گیف ریپلای کن و بنویس:\n.میم متن بالا | متن پایین\n"
              "فقط بالا: .میم سلام   |   فقط پایین: .میم | سلام\n"
              "(خروجی عکس ← عکس، ویدیو/گیف ← ویدیو)")


def parse_meme(arg):
    """'بالا | پایین' (یا دو خط) → (بالا، پایین). فقط یه متن = متن بالا."""
    t = (arg or "").strip()
    if "|" in t:
        top, bottom = t.split("|", 1)
    elif "\n" in t:
        top, bottom = t.split("\n", 1)
    else:
        top, bottom = t, ""
    return tuple(" ".join(x.split())[:MEME_TEXT_MAX].upper() for x in (top, bottom))


def _meme_layer(w, h, top, bottom):
    """لایهٔ شفاف w×h با متن سفیدِ حاشیه‌مشکی بالا و پایین (فارسی و لاتین)."""
    from PIL import Image, ImageDraw
    if any(ord(c) > 127 for c in top + bottom) and not _has_font():
        raise dl.DLError("❌ فونت پیدا نشد؛ فایل fonts/DejaVuSans-Bold.ttf رو کنار فایل‌های سلف بذار و دوباره دیپلوی کن.")
    shape, basic = _shaper()
    margin = max(6, int(min(w, h) * 0.03))
    box_w = w - 2 * margin
    max_h = int(h * 0.32)

    def layout(text):
        size = max(16, int(min(w, h) * 0.13))
        while True:
            font = _load_font(size, basic)
            stroke = max(2, size // 9)
            lines, cur = [], ""
            for word in text.split():
                trial = (cur + " " + word).strip()
                if cur and font.getlength(shape(trial)) + 2 * stroke > box_w:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            lines.append(cur)
            lh = int(size * 1.2)
            widest = max(font.getlength(shape(ln)) for ln in lines) + 2 * stroke
            if (widest <= box_w and lh * len(lines) <= max_h) or size <= 16:
                return font, stroke, lines, lh
            size -= 2

    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for text, at_top in ((top, True), (bottom, False)):
        if not text:
            continue
        font, stroke, lines, lh = layout(text)
        y = margin if at_top else h - margin - lh * len(lines)
        for ln in lines:
            sh = shape(ln)
            x = (w - font.getlength(sh)) / 2
            d.text((x, y), sh, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            y += lh
    return layer


def make_image_meme(src, out, top, bottom):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(src))
    if im.mode != "RGB":                              # شفافیت (مثلاً استیکر) روی پس‌زمینهٔ سفید
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg.convert("RGB")
    w, h = im.size
    big = max(w, h)
    k = 1280 / big if big > 1280 else (640 / big if big < 640 else 1.0)
    if k != 1.0:
        im = im.resize((max(2, round(w * k)), max(2, round(h * k))), Image.LANCZOS)
    layer = _meme_layer(im.size[0], im.size[1], top, bottom)
    Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB").save(out, "JPEG", quality=92)


async def _video_size(ff, path):
    """(عرض، ارتفاع) واقعی بعد از چرخش (ویدیوهای موبایل متادیتای چرخش دارن)."""
    _, err = await dl._run_ff(ff, "-hide_banner", "-i", path)
    m = re.search(r"Video:.*?[ ,](\d{2,5})x(\d{2,5})[ ,\[]", err)
    if not m:
        return 0, 0
    w, h = int(m.group(1)), int(m.group(2))
    r = re.search(r"rotation of (-?\d+(?:\.\d+)?) degrees", err)
    if r and int(abs(float(r.group(1)))) % 180 == 90:
        w, h = h, w
    return w, h


async def make_video_meme(ff, src, out, top, bottom, td):
    w, h = await _video_size(ff, src)
    if not (w and h):
        raise dl.DLError("❌ ابعاد ویدیو خونده نشد.")
    k = min(1.0, MEME_VIDEO_SIDE / max(w, h))
    W, H = max(2, int(w * k) // 2 * 2), max(2, int(h * k) // 2 * 2)
    png = os.path.join(td, "meme_layer.png")
    layer = await asyncio.get_running_loop().run_in_executor(None, _meme_layer, W, H, top, bottom)
    layer.save(png)
    fc = f"[0:v]scale={W}:{H},setsar=1[b];[b][1:v]overlay=0:0,format=yuv420p[v]"
    rc, err = await dl._run_ff(ff, "-y", "-i", src, "-i", png, "-filter_complex", fc,
                               "-map", "[v]", "-map", "0:a?", "-t", str(MEME_VIDEO_SECONDS),
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out, timeout=300)
    if rc != 0 or not os.path.exists(out):
        logger.info("meme ffmpeg: %s", err[-300:])
        raise dl.DLError("❌ ساخت میم ویدیویی انجام نشد (فرمت ویدیو پشتیبانی نمی‌شه یا زمانش تموم شد).")


async def cmd_meme(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    top, bottom = parse_meme(arg)
    r = await ev.get_reply_message() if ev.is_reply else None
    kind = _kind(r)
    if kind not in ("image", "video", "sticker_img", "sticker_vid") or not (top or bottom):
        return MEME_USAGE, True
    src_size = getattr(getattr(r, "file", None), "size", 0) or 0
    if src_size > SRC_MAX_BYTES:
        return "❌ فایل بزرگ‌تر از 40 مگابایته.", False
    ff = dl._ffmpeg()
    if kind in ("video", "sticker_vid") and not ff:
        return "❌ ffmpeg نصب نیست؛ imageio-ffmpeg رو نصب کن و دوباره دیپلوی کن.", False
    await dl._status(ev, "⏳ در حال ساخت میم…")
    with tempfile.TemporaryDirectory(prefix="meme_") as td:
        src = await asyncio.wait_for(client.download_media(r, file=os.path.join(td, "in")), 180)
        if not src:
            return "❌ دانلود فایل انجام نشد.", False
        if kind in ("image", "sticker_img"):
            out = os.path.join(td, "meme.jpg")
            await asyncio.get_running_loop().run_in_executor(None, make_image_meme, src, out, top, bottom)
            await client.send_file(ev.chat_id, out, reply_to=r.id, force_document=False)
        else:
            out = os.path.join(td, "meme.mp4")
            await make_video_meme(ff, src, out, top, bottom, td)
            await dl._send_one(client, ev.chat_id, out, "", ff, r.id)
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    return None, False


# ---------- .متن ویس ----------
_STT_ERRORS = (
    ("PREMIUM", "❌ رونویسی ویس فقط با تلگرام پرمیوم (یا سهمیهٔ رایگان محدود تلگرام) کار می‌کنه و این اکانت الان دسترسی نداره."),
    ("TRIAL", "❌ سهمیهٔ رایگان رونویسی این هفتهٔ اکانت تموم شده (تلگرام پرمیوم محدودیتش بیشتره)."),
    ("FLOOD", "⏳ تلگرام موقتاً محدودت کرده؛ چند دقیقه بعد دوباره امتحان کن."),
    ("TOO_LONG", "❌ ویس برای رونویسی خیلی طولانیه."),
    ("FAILED", "❌ تلگرام نتونست این ویس رو رونویسی کنه (زبان یا کیفیت صدا)."),
)


async def cmd_stt(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    r = await ev.get_reply_message() if ev.is_reply else None
    ok = r is not None and (getattr(r, "voice", None) or getattr(r, "video_note", None) or getattr(r, "audio", None))
    if not ok:
        return "🎙 روی یه ویس یا ویدیو مسیج ریپلای کن و بنویس: .متن ویس", True
    from telethon import events
    from telethon.tl import types
    from telethon.tl.functions.messages import TranscribeAudioRequest
    await dl._status(ev, "⏳ در حال تبدیل ویس به متن…")
    peer = await ev.get_input_chat()
    got = {}

    async def _on_update(upd):
        tid = getattr(upd, "transcription_id", None)
        if tid is not None:
            got[tid] = (bool(getattr(upd, "pending", False)), getattr(upd, "text", "") or "")

    handler = events.Raw(types=types.UpdateTranscribedAudio)
    client.add_event_handler(_on_update, handler)
    try:
        try:
            res = await asyncio.wait_for(client(TranscribeAudioRequest(peer=peer, msg_id=r.id)), 30)
        except Exception as e:  # noqa: BLE001
            msg = (getattr(e, "message", "") or str(e)).upper()
            logger.info("stt: %s", e)
            for key, text in _STT_ERRORS:
                if key in msg:
                    return text, False
            return "❌ رونویسی انجام نشد.", False
        tid = getattr(res, "transcription_id", None)
        pending, text = bool(getattr(res, "pending", False)), getattr(res, "text", "") or ""
        waited = 0
        while pending and waited < STT_WAIT:
            await asyncio.sleep(2)
            waited += 2
            if tid in got:
                pending, text = got[tid][0], got[tid][1] or text
                continue
            if waited % 8 == 0:                               # اگه آپدیت نیومد، دوباره بپرس
                try:
                    res = await asyncio.wait_for(client(TranscribeAudioRequest(peer=peer, msg_id=r.id)), 20)
                    pending, text = bool(getattr(res, "pending", False)), getattr(res, "text", "") or text
                except Exception as e:  # noqa: BLE001
                    logger.info("stt poll: %s", e)
    finally:
        try:
            client.remove_event_handler(_on_update, handler)
        except Exception:  # noqa: BLE001
            pass
    text = (text or "").strip()
    if not text:
        return ("⏳ تلگرام هنوز رونویسی رو تموم نکرد؛ چند ثانیه بعد دوباره امتحان کن." if pending
                else "❌ متنی تشخیص داده نشد."), False
    await client.send_message(ev.chat_id, sb.fancy_html("🎙 متن ویس:\n" + text[:3800]), reply_to=r.id, parse_mode="html")
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    return None, False


# ---------- .ویس (متن به ویس) ----------
TTS_MAX = 500
TTS_VOICES = {("fa", "m"): "fa-IR-FaridNeural", ("fa", "f"): "fa-IR-DilaraNeural",
              ("en", "m"): "en-US-GuyNeural", ("en", "f"): "en-US-JennyNeural"}
_TTS_MALE = ("مرد", "آقا", "پسر", "male", "man", "m")
_TTS_FEMALE = ("زن", "خانم", "دختر", "female", "woman", "f")
_TTS_FAST = ("سریع", "تند", "fast")
_TTS_SLOW = ("آهسته", "کند", "slow")


def parse_tts(arg):
    """گزینه‌های ابتدای متن (مرد/زن/سریع/آهسته) رو جدا می‌کنه. → (متن، جنسیت، rate)"""
    words = (arg or "").split()
    gender, rate = "f", "+0%"
    while words:
        w = sb.norm(words[0])
        if w in _TTS_MALE:
            gender = "m"
        elif w in _TTS_FEMALE:
            gender = "f"
        elif w in _TTS_FAST:
            rate = "+25%"
        elif w in _TTS_SLOW:
            rate = "-25%"
        else:
            break
        words.pop(0)
    return " ".join(words), gender, rate


def _lang(text):
    fa = len(re.findall(r"[\u0600-\u06FF]", text))
    en = len(re.findall(r"[A-Za-z]", text))
    return "fa" if fa >= en else "en"


async def cmd_tts(uid, ev, arg):
    client = sb._clients.get(uid)
    if client is None:
        return "اکانت سلف وصل نیست.", False
    text, gender, rate = parse_tts(arg)
    r = await ev.get_reply_message() if ev.is_reply else None
    if not text and r is not None:
        text = (r.raw_text or "").strip()
    text = " ".join(text.split())
    if not text:
        return ("🔊 مثل: .ویس سلام چطوری\n.ویس مرد سلام | .ویس زن سلام | .ویس سریع سلام\n"
                "یا روی یه متن ریپلای کن و بنویس: .ویس"), True
    if len(text) > TTS_MAX:
        return f"❌ متن بلنده؛ حداکثر {TTS_MAX} حرف.", False
    ff = dl._ffmpeg()
    if not ff:
        return "❌ ffmpeg نصب نیست؛ imageio-ffmpeg رو نصب کن و دوباره دیپلوی کن.", False
    try:
        import edge_tts
    except ImportError:
        return "❌ edge-tts نصب نیست؛ requirements.txt رو آپدیت و دوباره دیپلوی کن.", False
    voice = TTS_VOICES[(_lang(text), gender)]
    await dl._status(ev, "⏳ در حال ساخت ویس…")
    with tempfile.TemporaryDirectory(prefix="tts_") as td:
        mp3, ogg = os.path.join(td, "t.mp3"), os.path.join(td, "t.ogg")
        try:
            await asyncio.wait_for(edge_tts.Communicate(text, voice, rate=rate).save(mp3), 60)
        except asyncio.TimeoutError:
            return "❌ سرویس صدا جواب نداد؛ چند لحظه بعد دوباره امتحان کن.", False
        except Exception as e:  # noqa: BLE001
            logger.info("tts: %s", e)
            return "❌ سرویس صدا در دسترس نیست؛ بعداً دوباره امتحان کن.", False
        if not os.path.exists(mp3) or os.path.getsize(mp3) < 200:
            return "❌ صدایی ساخته نشد (متن قابل خوندن نبود).", False
        rc, _ = await dl._run_ff(ff, "-y", "-i", mp3, "-vn", "-c:a", "libopus", "-b:a", "32k", "-ar", "48000",
                                 "-ac", "1", "-f", "ogg", ogg, timeout=90)
        if rc != 0 or not os.path.exists(ogg):
            return "❌ تبدیل صدا به ویس انجام نشد (ffmpeg انکودر opus نداره).", False
        from telethon.tl.types import DocumentAttributeAudio
        dur, _, _ = await dl._probe(ff, ogg)
        await client.send_file(ev.chat_id, ogg, voice_note=True, reply_to=r.id if r is not None else None,
                               attributes=[DocumentAttributeAudio(duration=max(dur, 1), voice=True)])
    try:
        await ev.delete()
    except Exception:  # noqa: BLE001
        pass
    return None, False


# ---------- نقطهٔ ورود ----------
async def run(uid, ev, cmd, arg):
    if uid in _busy:
        return "⏳ یه کار رسانه‌ای در حال انجامه؛ صبر کن تموم شه.", False
    _busy.add(uid)
    try:
        async with _sem:
            try:
                if cmd == "sticker":
                    return await cmd_sticker(uid, ev, arg)
                if cmd == "meme":
                    return await cmd_meme(uid, ev, arg)
                if cmd == "stt":
                    return await cmd_stt(uid, ev, arg)
                if cmd == "tts":
                    a = sb.norm(arg)
                    if a == "به متن" or a.startswith("به متن ") or a == "ویس به متن":   # «.ویس به متن» = ویس به متن
                        return await cmd_stt(uid, ev, "")
                    return await cmd_tts(uid, ev, arg)
            except dl.DLError as e:
                return str(e), False
            except asyncio.TimeoutError:
                return "❌ زمان انجام کار تموم شد.", False
    finally:
        _busy.discard(uid)
    return None, False
