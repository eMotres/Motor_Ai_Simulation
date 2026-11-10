#!/usr/bin/env python
"""Draw the at-a-glance picture of the free-simulation launch mail (owner 2026-11-10), EN and zh-CN.

    python scripts/make_mail_flow_image.py

Writes ``config/mail/images/free_simulation_flow.<lang>.png`` (1104 px wide = 2x the 552 px text column of the mail).  Flat,
brand colours of the approved logo (navy, blue, red), the logo itself from ``web/public/brand``.  No prices, dates or customer data.
The mailer embeds the PNG in the message (``cid:``), so nothing is fetched from a server when the mail is opened.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config" / "mail" / "images"
LOGO = ROOT / "web" / "public" / "brand" / "aerostator-logo-512.png"

NAVY, BLUE, RED = (1, 36, 76), (0, 125, 254), (252, 12, 20)
INK, GREY, LINE, PALE = (15, 23, 42), (75, 85, 99), (226, 232, 240), (241, 245, 249)
W, PAD = 1104, 48

FONTS = {
    "en": ("C:/Windows/Fonts/NotoSans-Bold.ttf", "C:/Windows/Fonts/NotoSans-Regular.ttf"),
    "zh-CN": ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"),
}

TEXT = {
    "en": {
        "title": "Your motor, simulated free",
        "steps": [
            ("Describe your motor", "Specification: operating points, voltage, space, cooling"),
            ("Your AI agent or our assistant fills it in", "Through MCP, or with the specification assistant"),
            ("MOTRES engineers simulate it, free", "Electromagnetic, thermal and mechanical FEM"),
            ("Results in your project", "Shared with your team in the project"),
        ],
        "free": ("FREE", "No fee. Your engineering data may train MOTRES's own AI models; personal data never."),
        "private": ("PRIVATE", "Paid. No AI training on your data."),
    },
    "zh-CN": {
        "title": "免费为您仿真电机",
        "steps": [
            ("描述您的电机", "规格书：工况点、电压、安装空间、冷却方式"),
            ("人工智能代理或助手协助填写", "通过 MCP，或使用规格书助手"),
            ("MOTRES 工程师免费仿真", "电磁、热和机械有限元分析"),
            ("结果在您的项目中", "在项目中与您的团队共享"),
        ],
        "free": ("免费模式", "不收费。您的工程数据可用于训练 MOTRES 自有的人工智能模型；绝不使用个人数据。"),
        "private": ("私有模式", "付费。不使用您的数据进行人工智能训练。"),
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


def icon(draw: ImageDraw.ImageDraw, base: Image.Image, i: int, x: int, y: int, s: int) -> None:
    """Flat icons in a rounded tile: 0 specification sheet, 1 chat bubble, 2 the motor (logo), 3 result bars."""
    draw.rounded_rectangle((x, y, x + s, y + s), radius=s // 5, fill=PALE)
    c = s // 2
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
    elif i == 2:
        logo = Image.open(LOGO).convert("RGBA").resize((int(s * .78), int(s * .78)), Image.LANCZOS)
        base.alpha_composite(logo, (int(x + s * .11), int(y + s * .11)))
    else:
        for k, h in enumerate((.30, .48, .38, .60)):
            bx = x + s * (.20 + .16 * k)
            draw.rounded_rectangle((bx, y + s * (.80 - h), bx + s * .11, y + s * .80), radius=5, fill=RED if k == 3 else BLUE)
        draw.line((x + s * .16, y + s * .82, x + s * .84, y + s * .82), fill=NAVY, width=5)


def make(lang: str) -> Path:
    t = TEXT[lang]
    cjk = lang == "zh-CN"
    bold, reg = FONTS[lang]
    f_title, f_step, f_sub = ImageFont.truetype(bold, 52), ImageFont.truetype(bold, 40), ImageFont.truetype(reg, 31)
    f_num, f_tag, f_note = ImageFont.truetype(bold, 34), ImageFont.truetype(bold, 30), ImageFont.truetype(reg, 29)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tile, num_d = 132, 56
    text_x = PAD + num_d + 24 + tile + 28
    text_w = W - PAD - text_x
    rows = []
    for title, sub in t["steps"]:
        tl = wrap(probe, title, f_step, text_w, cjk)
        sl = wrap(probe, sub, f_sub, text_w, cjk)
        rows.append((tl, sl, max(tile, len(tl) * 52 + len(sl) * 42 + 8)))
    note_w = W - 2 * PAD - 40 - 190
    notes = [(tag, wrap(probe, txt, f_note, note_w, cjk)) for tag, txt in (t["free"], t["private"])]
    notes_h = sum(max(56, len(l) * 40) + 18 for _, l in notes) + 40
    H = PAD + 70 + 34 + sum(r[2] for r in rows) + 3 * 58 + 40 + notes_h + PAD

    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=28, outline=LINE, width=3)
    d.text((PAD, PAD), t["title"], font=f_title, fill=INK)
    d.rectangle((PAD, PAD + 78, PAD + 90, PAD + 84), fill=RED)
    y = PAD + 70 + 34
    for i, (tl, sl, h) in enumerate(rows):
        cy = y + h // 2
        d.ellipse((PAD, cy - num_d // 2, PAD + num_d, cy + num_d // 2), fill=NAVY)
        d.text((PAD + num_d // 2, cy), str(i + 1), font=f_num, fill=(255, 255, 255), anchor="mm")
        icon(d, img, i, PAD + num_d + 24, cy - tile // 2, tile)
        ty = cy - (len(tl) * 52 + len(sl) * 42) // 2
        for line in tl:
            d.text((text_x, ty), line, font=f_step, fill=INK)
            ty += 52
        ty += 4
        for line in sl:
            d.text((text_x, ty), line, font=f_sub, fill=GREY)
            ty += 42
        y += h
        if i < 3:
            ax = PAD + num_d // 2
            d.line((ax, y + 6, ax, y + 50), fill=LINE, width=6)
            d.polygon([(ax - 14, y + 38), (ax + 14, y + 38), (ax, y + 54)], fill=BLUE)
            y += 58
    y += 40
    d.rounded_rectangle((PAD, y, W - PAD, y + notes_h - 20), radius=20, fill=PALE)
    ny = y + 22
    for (tag, lines), colour in zip(notes, (BLUE, NAVY)):
        h = max(56, len(lines) * 40)
        d.rounded_rectangle((PAD + 20, ny, PAD + 20 + 170, ny + 52), radius=26, fill=colour)
        d.text((PAD + 105, ny + 26), tag, font=f_tag, fill=(255, 255, 255), anchor="mm")
        ly = ny + 8
        for line in lines:
            d.text((PAD + 20 + 190, ly), line, font=f_note, fill=INK)
            ly += 40
        ny += h + 18
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"free_simulation_flow.{lang}.png"
    img.convert("RGB").save(p, optimize=True)
    return p


if __name__ == "__main__":
    for lang in TEXT:
        p = make(lang)
        print(p.relative_to(ROOT), p.stat().st_size, "bytes")
