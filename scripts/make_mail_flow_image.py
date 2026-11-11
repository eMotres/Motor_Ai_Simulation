#!/usr/bin/env python
"""Draw the at-a-glance picture of the free-simulation launch mail (owner 2026-11-10/11), EN and zh-CN.

    python scripts/make_mail_flow_image.py

Writes ``config/mail/images/free_simulation_flow.<lang>.png`` (1104 px wide = 2x the 552 px text column of the mail).
A hero strip with renders of OUR PUBLIC motors (owner 2026-11-11 «возьми картинки наших моторов»), then the five steps; step 3
shows the electromagnetic, thermal and displacement maps the landing page already publishes.  Sources, all public:

* ``web/public/landing/core-l12.webp`` and the three ``web/public/landing/*-map/field`` pictures - CIANO14 40 L12, a public
  catalogue motor (``scripts/landing_images.py``);
* ``config/mail/images/src/aerostator-core.png``, ``ciano14-40-l12.png`` and ``ciano42-150-30.png`` - trimmed copies of the renders on the public
  emotres.com pages (home, /shop/ciano-150-30/).

Never a customer or project motor.  No prices, dates or customer data.  The mailer embeds the PNG in the message (``cid:``),
so nothing is fetched from a server when the mail is opened.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "mail" / "images"
LANDING = ROOT / "web" / "public" / "landing"
HERO = [OUT / "src" / "ciano14-40-l12.png", OUT / "src" / "aerostator-core.png", OUT / "src" / "ciano42-150-30.png"]
MAPS = [LANDING / "em-field.webp", LANDING / "thermal-map.webp", LANDING / "rotor-displacement.webp"]

NAVY, NAVY2, BLUE, RED = (1, 36, 76), (8, 64, 128), (0, 125, 254), (252, 12, 20)
INK, GREY, PALE = (15, 23, 42), (75, 85, 99), (241, 245, 249)
W, PAD = 1104, 48

FONTS = {
    "en": ("C:/Windows/Fonts/NotoSans-Bold.ttf", "C:/Windows/Fonts/NotoSans-Regular.ttf"),
    "zh-CN": ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"),
}

TEXT = {
    "en": {
        "title": "Your motor, simulated free",
        "kicker": "Describe it. Our engineers simulate it.",
        "steps": [
            ("Describe your motor", "Specification: operating points, voltage, space, cooling"),
            ("Your AI agent or our assistant fills it in", "Through MCP, or with the specification assistant"),
            ("MOTRES engineers simulate it", "Electromagnetic, thermal and mechanical FEM"),
            ("Results in your project", "Shared with your team in the project"),
            ("Order your motor", "We send you the price and lead time"),
        ],
        "maps": ("Field", "Heat", "Mechanics"),
        "note": "Free. Your engineering data help train our AI models; personal data never.",
    },
    "zh-CN": {
        "title": "免费为您仿真电机",
        "kicker": "您来描述，我们的工程师来仿真。",
        "steps": [
            ("描述您的电机", "规格书：工况点、电压、安装空间、冷却方式"),
            ("人工智能代理或助手协助填写", "通过 MCP，或使用规格书助手"),
            ("MOTRES 工程师进行仿真", "电磁、热和机械有限元分析"),
            ("结果在您的项目中", "在项目中与您的团队共享"),
            ("订购电机", "我们为您提供价格和交货期"),
        ],
        "maps": ("磁场", "温度", "机械"),
        "note": "免费。您的工程数据用于帮助训练我们的人工智能模型；绝不使用个人数据。",
    },
}


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, cjk: bool) -> list[str]:
    units = list(text) if cjk else text.split(" ")
    lines, cur = [], ""
    for u in units:
        trial = cur + u if cjk else (cur + " " + u).strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = u
    if cur:
        lines.append(cur)
    return lines


def fit(path: Path, box: int) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    im = im.crop(im.getbbox())
    im.thumbnail((box, box), Image.LANCZOS)
    return im


def hero(lang: str, fonts: dict) -> Image.Image:
    """Navy gradient, title and kicker, three public motors standing on soft light discs."""
    h = 560
    img = Image.new("RGBA", (W, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        k = y / h
        d.line((0, y, W, y), fill=tuple(int(a + (b - a) * k) for a, b in zip(NAVY, NAVY2)) + (255,))
    t = TEXT[lang]
    d.text((W // 2, 74), t["title"], font=fonts["hero"], fill=(255, 255, 255), anchor="mm")
    d.text((W // 2, 140), t["kicker"], font=fonts["kicker"], fill=(191, 219, 254), anchor="mm")
    d.rectangle((W // 2 - 45, 176, W // 2 + 45, 182), fill=RED)
    cards = [(W * 0.19, 384, 290), (W * 0.50, 372, 330), (W * 0.81, 384, 290)]
    shadow = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for cx, cy, s in cards:
        sd.rounded_rectangle((cx - s / 2 + 6, cy - s / 2 + 14, cx + s / 2 + 6, cy + s / 2 + 14), radius=30, fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(img)
    for (cx, cy, s), path in zip(cards, HERO):
        d.rounded_rectangle((cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2), radius=30, fill=(255, 255, 255, 255))
        m = fit(path, int(s * .86))
        img.alpha_composite(m, (int(cx - m.width / 2), int(cy - m.height / 2)))
    return img


def icon(draw: ImageDraw.ImageDraw, i: int, x: int, y: int, s: int) -> None:
    """Flat icons in a rounded tile: 0 specification sheet, 1 chat bubble, 3 result bars, 4 order box (2 = the maps, drawn separately)."""
    draw.rounded_rectangle((x, y, x + s, y + s), radius=s // 5, fill=PALE)
    if i == 0:
        draw.rounded_rectangle((x + s * .27, y + s * .18, x + s * .73, y + s * .82), radius=8, fill=(255, 255, 255), outline=NAVY, width=5)
        for k in range(4):
            yy = y + s * (.33 + .12 * k)
            draw.line((x + s * .35, yy, x + s * (.65 if k % 2 == 0 else .55), yy), fill=BLUE if k else RED, width=6)
    elif i == 1:
        draw.rounded_rectangle((x + s * .18, y + s * .24, x + s * .82, y + s * .66), radius=18, fill=BLUE)
        draw.polygon([(x + s * .34, y + s * .64), (x + s * .30, y + s * .80), (x + s * .48, y + s * .65)], fill=BLUE)
        for k in range(3):
            cx = x + s * (.36 + .14 * k)
            draw.ellipse((cx - 8, y + s * .45 - 8, cx + 8, y + s * .45 + 8), fill=(255, 255, 255))
    elif i == 3:
        for k, h in enumerate((.30, .48, .38, .60)):
            bx = x + s * (.20 + .16 * k)
            draw.rounded_rectangle((bx, y + s * (.80 - h), bx + s * .11, y + s * .80), radius=5, fill=RED if k == 3 else BLUE)
        draw.line((x + s * .16, y + s * .82, x + s * .84, y + s * .82), fill=NAVY, width=5)
    elif i == 4:
        # a shipping box with a red tick
        draw.polygon([(x + s * .20, y + s * .38), (x + s * .50, y + s * .24), (x + s * .80, y + s * .38), (x + s * .50, y + s * .52)], fill=(219, 234, 254), outline=NAVY)
        draw.polygon([(x + s * .20, y + s * .38), (x + s * .50, y + s * .52), (x + s * .50, y + s * .84), (x + s * .20, y + s * .70)], fill=BLUE, outline=NAVY)
        draw.polygon([(x + s * .80, y + s * .38), (x + s * .50, y + s * .52), (x + s * .50, y + s * .84), (x + s * .80, y + s * .70)], fill=NAVY)
        draw.line([(x + s * .58, y + s * .64), (x + s * .65, y + s * .72), (x + s * .76, y + s * .55)], fill=RED, width=8, joint="curve")


def map_tile(path: Path, s: int) -> Image.Image:
    """The ring of a field map without its colour bar, on a white rounded tile."""
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    im = im.crop((0, 0, int(w * .826), h))                      # drop the colour bar (it starts after a white gap at ~83 %)
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    diff = Image.new("L", im.size, 0)
    px, dp = im.load(), diff.load()
    for yy in range(0, im.size[1]):
        for xx in range(0, im.size[0]):
            r, g, b, _ = px[xx, yy]
            dp[xx, yy] = 0 if r > 245 and g > 245 and b > 245 else 255
    box = diff.getbbox() or (0, 0, *im.size)
    im = im.crop(box)
    im.thumbnail((s - 16, s - 16), Image.LANCZOS)
    tile = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 6, fill=(255, 255, 255, 255), outline=(226, 232, 240), width=3)
    tile.alpha_composite(im, ((s - im.width) // 2, (s - im.height) // 2))
    del bg
    return tile


def make(lang: str) -> Path:
    t = TEXT[lang]
    cjk = lang == "zh-CN"
    bold, reg = FONTS[lang]
    fonts = {"hero": ImageFont.truetype(bold, 64), "kicker": ImageFont.truetype(reg, 38)}
    f_step, f_sub, f_num = ImageFont.truetype(bold, 40), ImageFont.truetype(reg, 31), ImageFont.truetype(bold, 34)
    f_map, f_note = ImageFont.truetype(bold, 26), ImageFont.truetype(bold, 32)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tile, num_d, map_s = 132, 56, 150
    text_x = PAD + num_d + 24 + tile + 28
    text_w = W - PAD - text_x
    rows = []
    for i, (title, sub) in enumerate(t["steps"]):
        tl, sl = wrap(probe, title, f_step, text_w, cjk), wrap(probe, sub, f_sub, text_w, cjk)
        body = len(tl) * 52 + len(sl) * 42 + 8
        extra = map_s + 52 if i == 2 else 0
        rows.append((tl, sl, max(tile, body) + extra))
    note_lines = wrap(probe, t["note"], f_note, W - 2 * PAD - 60, cjk)
    note_h = len(note_lines) * 46 + 44
    hero_img = hero(lang, fonts)
    H = hero_img.height + 44 + sum(r[2] for r in rows) + (len(rows) - 1) * 50 + 36 + note_h + PAD

    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    img.alpha_composite(hero_img, (0, 0))
    d = ImageDraw.Draw(img)
    y = hero_img.height + 44
    for i, (tl, sl, h) in enumerate(rows):
        head_h = h - (map_s + 52 if i == 2 else 0)
        cy = y + head_h // 2
        d.ellipse((PAD, cy - num_d // 2, PAD + num_d, cy + num_d // 2), fill=RED if i == 2 else NAVY)
        d.text((PAD + num_d // 2, cy), str(i + 1), font=f_num, fill=(255, 255, 255), anchor="mm")
        if i == 2:
            logo = fit(ROOT / "web" / "public" / "brand" / "aerostator-logo-512.png", int(tile * .8))
            ix, iy = PAD + num_d + 24, cy - tile // 2
            d.rounded_rectangle((ix, iy, ix + tile, iy + tile), radius=tile // 5, fill=PALE)
            img.alpha_composite(logo, (ix + (tile - logo.width) // 2, iy + (tile - logo.height) // 2))
        else:
            icon(d, i, PAD + num_d + 24, cy - tile // 2, tile)
        ty = cy - (len(tl) * 52 + len(sl) * 42) // 2
        for line in tl:
            d.text((text_x, ty), line, font=f_step, fill=INK)
            ty += 52
        ty += 4
        for line in sl:
            d.text((text_x, ty), line, font=f_sub, fill=GREY)
            ty += 42
        if i == 2:
            my = y + head_h + 16
            gap = (W - text_x - PAD - 3 * map_s) // 2
            for k, (p, label) in enumerate(zip(MAPS, t["maps"])):
                mx = text_x + k * (map_s + gap)
                img.alpha_composite(map_tile(p, map_s), (mx, my))
                d.text((mx + map_s // 2, my + map_s + 18), label, font=f_map, fill=GREY, anchor="mm")
        y += h
        if i < len(rows) - 1:
            ax = PAD + num_d // 2
            d.line((ax, y + 6, ax, y + 42), fill=(203, 213, 225), width=6)
            d.polygon([(ax - 14, y + 30), (ax + 14, y + 30), (ax, y + 46)], fill=BLUE)
            y += 50
    y += 36
    d.rounded_rectangle((PAD, y, W - PAD, y + note_h), radius=24, fill=PALE)
    ly = y + 22
    for line in note_lines:
        d.text((W // 2, ly + 20), line, font=f_note, fill=NAVY, anchor="mm")
        ly += 46
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"free_simulation_flow.{lang}.png"
    img.convert("RGB").save(p, optimize=True)
    return p


if __name__ == "__main__":
    for lang in TEXT:
        p = make(lang)
        print(p.relative_to(ROOT), p.stat().st_size, "bytes")
