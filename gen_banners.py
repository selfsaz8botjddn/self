# -*- coding: utf-8 -*-
"""ساخت بنرهای «خرید الماس» و «فعال‌سازی سلف» → banner_buy.png / banner_activate.png
اجرا: python gen_banners.py   (نیاز به Pillow با raqm و فونت DejaVu Sans Bold؛ خروجی‌ها توی زیپ هست و دیگه لازم نیست اجرا بشه)"""
import math, os, random
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

W, H = 1280, 720
HERE = os.path.dirname(os.path.abspath(__file__))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ORANGE, CYAN, WHITE = (255, 138, 30), (0, 214, 255), (255, 255, 255)


def font(sz):
    return ImageFont.truetype(FONT, sz, layout_engine=ImageFont.Layout.RAQM)


def T(d, xy, txt, f, fill, anchor="mm"):
    d.text(xy, txt, font=f, fill=fill, anchor=anchor, direction="rtl", language="fa")


def background():
    import numpy as np
    y, x = np.mgrid[0:H, 0:W].astype("float32")
    nx, ny = x / W, y / H
    navy = np.array([10, 8, 58], "float32"); blue = np.array([8, 70, 140], "float32"); orange = np.array([150, 62, 18], "float32")
    img = navy[None, None, :] * np.ones((H, W, 1), "float32")
    gb = np.clip((nx - 0.45) * 1.6 * (1.2 - ny), 0, 1)[..., None]
    go = np.clip((0.75 - nx) * 1.5 * (ny - 0.25) * 1.6, 0, 1)[..., None]
    img = img * (1 - gb) + blue * gb
    img = img * (1 - go) + orange * go
    im = Image.fromarray(img.clip(0, 255).astype("uint8"))
    d = ImageDraw.Draw(im, "RGBA")
    for gx in range(0, W, 60): d.line([(gx, 0), (gx, H)], fill=(255, 255, 255, 10))
    for gy in range(0, H, 60): d.line([(0, gy), (W, gy)], fill=(255, 255, 255, 10))
    random.seed(7)
    for _ in range(70):
        px, py, r = random.randint(0, W), random.randint(0, H), random.choice([1, 2, 2, 3])
        d.ellipse((px - r, py - r, px + r, py + r), fill=random.choice([(255, 170, 70, 170), (140, 220, 255, 150), (255, 255, 255, 120)]))
    return im


def glow_text(im, xy, txt, size, fill, glow, blur=14, anchor="mm"):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, txt, font=font(size), fill=glow + (200,), anchor=anchor, direction="rtl", language="fa")
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    im.paste(layer, (0, 0), layer)
    T(ImageDraw.Draw(im), xy, txt, font(size), fill, anchor)


def pill(im, box, txt, size, fill=None, outline=None, text_fill=WHITE, width=2):
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    h = box[3] - box[1]
    d.rounded_rectangle(box, radius=h // 2 if h < 80 else 24, fill=fill, outline=outline, width=width)
    im.paste(ov, (0, 0), ov)
    T(ImageDraw.Draw(im), ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2 - 2), txt, font(size), text_fill)


def ring(im, cx=300, cy=450):
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse((cx - 205, cy - 205, cx + 205, cy + 205), outline=(255, 170, 70), width=7)
    d.ellipse((cx - 198, cy - 198, cx + 198, cy + 198), fill=(110, 50, 20))
    d.ellipse((cx - 190, cy - 190, cx + 190, cy + 190), fill=(12, 10, 28))
    d.arc((cx - 215, cy - 215, cx + 215, cy + 215), 200, 262, fill=CYAN, width=6)
    d.arc((cx - 215, cy - 215, cx + 215, cy + 215), 20, 82, fill=CYAN, width=6)


def gem(im, cx=300, cy=450, s=150):
    """الماس با وجه‌های رنگی."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    top_l, top_r = (cx - s * 0.62, cy - s * 0.42), (cx + s * 0.62, cy - s * 0.42)
    mid_l, mid_r = (cx - s * 0.30, cy - s * 0.78), (cx + s * 0.30, cy - s * 0.78)
    bot = (cx, cy + s * 0.9)
    c1, c2 = (cx - s * 0.2, cy - s * 0.42), (cx + s * 0.2, cy - s * 0.42)
    faces = [([top_l, mid_l, c1], (120, 235, 255)), ([mid_l, mid_r, c2, c1], (200, 250, 255)), ([mid_r, top_r, c2], (60, 190, 245)),
             ([top_l, c1, bot], (40, 150, 230)), ([c1, c2, bot], (90, 215, 255)), ([c2, top_r, bot], (20, 110, 200))]
    for pts, col in faces:
        d.polygon(pts, fill=col + (255,), outline=(255, 255, 255, 200))
    gl = ov.filter(ImageFilter.GaussianBlur(26))
    glow = Image.new("RGBA", (W, H), (0, 214, 255, 0)); glow.putalpha(gl.split()[3].point(lambda a: int(a * 0.8)))
    im.paste(glow, (0, 0), glow); im.paste(ov, (0, 0), ov)


def fox(im, cx=300, cy=450):
    base = Image.open(os.path.join(HERE, "banner_base.png")).convert("RGB")
    r = 206
    crop = base.crop((300 - r, 450 - r, 300 + r, 450 + r))
    mask = Image.new("L", (2 * r * 4, 2 * r * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 2 * r * 4 - 1, 2 * r * 4 - 1), fill=255)
    mask = mask.resize((2 * r, 2 * r), Image.LANCZOS)
    im.paste(crop, (cx - r, cy - r), mask)
    d = ImageDraw.Draw(im)
    d.arc((cx - 215, cy - 215, cx + 215, cy + 215), 200, 262, fill=CYAN, width=6)
    d.arc((cx - 215, cy - 215, cx + 215, cy + 215), 20, 82, fill=CYAN, width=6)


def chips(im, labels):
    xs = [(940, 1230), (630, 920)]
    ys = [286, 354, 422]
    i = 0
    for yy in ys:
        for x0, x1 in xs:
            pill(im, (x0, yy, x1, yy + 54), labels[i], 27, fill=(255, 255, 255, 28), outline=(255, 150, 60, 230), width=2); i += 1


def build(name, title, tsize, sub, labels, orange_txt, cyan_txt, icon):
    im = background().convert("RGB")
    glow_text(im, (640, 120), title, tsize, WHITE, (255, 140, 40))
    T(ImageDraw.Draw(im), (930, 238), sub, font(44), (255, 165, 60), "mm")
    chips(im, labels)
    pill(im, (640, 500, 1230, 565), orange_txt, 40, fill=(255, 122, 20, 255), outline=None)
    pill(im, (640, 585, 1230, 655), cyan_txt, 36, fill=(10, 30, 60, 200), outline=CYAN + (255,), text_fill=(160, 235, 255), width=3)
    if icon == "gem":
        ring(im); gem(im)
    else:
        fox(im)
    ImageDraw.Draw(im).text((60, 688), "RUBAHIO SELF", font=ImageFont.truetype(FONT, 30), fill=WHITE, anchor="lm")
    im.save(os.path.join(HERE, name), optimize=True)


if __name__ == "__main__":
    build("banner_buy.png", "خرید الماس", 150, "سریع  •  مطمئن  •  دلخواه",
          ["تعداد دلخواه", "کارت به کارت", "کد تخفیف", "واریز بعد از تأیید", "الماس رایگان", "پشتیبانی تلگرامی"],
          "۲۵۰ الماس هدیهٔ شروع", "الماس = سوخت سلف روباهیو", "gem")
    build("banner_activate.png", "فعال‌سازی سلف", 128, "قدرتمند  •  سریع  •  امن",
          ["پاسخ خودکار", "ضدحذف و ویرایش", "ماشین‌حساب", "ترجمه و تگ اعضا", "ذخیرهٔ استوری", "همیشه آنلاین"],
          "فعال‌سازی فقط ۵ الماس", "اتصال امن اکانت شخصی", "fox")
