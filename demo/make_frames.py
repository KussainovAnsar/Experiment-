#!/usr/bin/env python3
"""Generate the demo "screen share" keyframes for demo/lesson_01 with Pillow.

The lesson is an online Russian-language class (one teacher, one 6th-grade student, Kazakh school) on
dative / prepositional case endings with a secondary -тся/-ться thread. The frames imitate what a Zoom
recording would show: the meeting layout with neutral avatars (no faces), slides with the rule table,
exercise lists, the student's typed answers (including the mistake «учится» where «учиться» is required)
and the corrected version.

Usage::

    python demo/make_frames.py            # writes demo/lesson_01/frames/0000.jpg … and frames.json
    python demo/make_frames.py --out DIR  # custom output directory

``frames.json`` keeps the REAL lesson timestamps (seconds from the start of the 35-minute lesson), which
is what ``analysis.json`` refers to via ``frame_ids``. Each JPEG is kept under 150 KB.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEMO_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = DEMO_DIR / "lesson_01" / "frames"

WIDTH, HEIGHT = 1280, 720
MAX_JPEG_BYTES = 150 * 1024

# Palette (hex strings are fine for Pillow).
INK = "#1f2937"
MUTED = "#6b7280"
PAPER = "#f8fafc"
WHITE = "#ffffff"
BLUE = "#1e3a8a"
BLUE_SOFT = "#dbeafe"
GREEN = "#15803d"
GREEN_SOFT = "#dcfce7"
ORANGE = "#c2410c"
ORANGE_SOFT = "#ffedd5"
RED = "#b91c1c"
RED_SOFT = "#fee2e2"
PURPLE = "#6d28d9"
PURPLE_SOFT = "#ede9fe"
YELLOW_SOFT = "#fef08a"
ZOOM_BG = "#1b1b1f"
ZOOM_TILE = "#2a2a30"
MARKER_BLUE = "#1d4ed8"
MARKER_RED = "#dc2626"

# Candidate Cyrillic-capable fonts (regular, bold). matplotlib's bundled DejaVu is the fallback.
_FONT_CANDIDATES: list[tuple[str, str]] = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/DejaVuSans.ttf", "/Library/Fonts/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/DejaVuSans.ttf", "C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
    (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
]


def find_fonts() -> tuple[Path, Path]:
    """Return (regular, bold) TTF paths that can render Cyrillic; fall back to matplotlib's DejaVuSans."""
    for regular, bold in _FONT_CANDIDATES:
        if Path(regular).is_file():
            bold_path = Path(bold) if Path(bold).is_file() else Path(regular)
            return Path(regular), bold_path
    try:
        import matplotlib  # type: ignore[import-not-found]

        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        regular_p, bold_p = base / "DejaVuSans.ttf", base / "DejaVuSans-Bold.ttf"
        if regular_p.is_file():
            return regular_p, (bold_p if bold_p.is_file() else regular_p)
    except ImportError:
        pass
    raise SystemExit(
        "No Cyrillic-capable TTF font found. Install fonts-dejavu or run: pip install matplotlib "
        "(its bundled DejaVuSans.ttf will be used)."
    )


REGULAR_TTF, BOLD_TTF = find_fonts()
_font_cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Cached TrueType font."""
    key = (bold, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(BOLD_TTF if bold else REGULAR_TTF), size)
    return _font_cache[key]


# ---------------------------------------------------------------- drawing helpers


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=fnt)
    return right - left


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap for a single paragraph."""
    words, lines, current = text.split(), [], ""
    for w in words:
        candidate = f"{current} {w}".strip()
        if text_width(draw, candidate, fnt) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def draw_paragraph(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fnt: ImageFont.FreeTypeFont,
    *,
    fill: str = INK,
    max_width: int = WIDTH - 160,
    line_gap: int = 8,
) -> int:
    """Draw wrapped text; return the y coordinate below the last line."""
    x, y = xy
    for line in wrap(draw, text, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + line_gap
    return y


def header_band(draw: ImageDraw.ImageDraw, title: str, color: str, *, subtitle: str | None = None) -> int:
    """Coloured title band across the top of a slide; returns the y below it."""
    h = 118 if subtitle else 96
    draw.rectangle((0, 0, WIDTH, h), fill=color)
    draw.text((64, 26), title, font=font(40, bold=True), fill=WHITE)
    if subtitle:
        draw.text((64, 76), subtitle, font=font(22), fill="#e5e7eb")
    return h


def share_badge(draw: ImageDraw.ImageDraw, label: str = "Вы смотрите демонстрацию экрана Айгерим С.") -> None:
    """Thin green Zoom-like bar at the top edge of a shared screen."""
    draw.rectangle((0, 0, WIDTH, 22), fill="#16a34a")
    f = font(15)
    draw.text(((WIDTH - text_width(draw, label, f)) // 2, 3), label, font=f, fill=WHITE)


def thumbnail_tiles(draw: ImageDraw.ImageDraw) -> None:
    """Small participant thumbnails in the top-right corner (avatars only, no faces)."""
    x0, y0, w, h = WIDTH - 250, 30, 110, 72
    for i, (name, initials, col) in enumerate([("Айгерим С.", "АС", "#0ea5e9"), ("Данияр", "Д", "#f59e0b")]):
        x = x0 + i * (w + 10)
        draw.rounded_rectangle((x, y0, x + w, y0 + h), radius=8, fill=ZOOM_TILE)
        cx, cy, r = x + w // 2, y0 + 28, 18
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
        f = font(14, bold=True)
        draw.text((cx - text_width(draw, initials, f) // 2, cy - 9), initials, font=f, fill=WHITE)
        draw.text((x + 6, y0 + h - 20), name, font=font(13), fill="#e5e7eb")


def new_slide(bg: str = PAPER) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    return img, ImageDraw.Draw(img)


def numbered_list(
    draw: ImageDraw.ImageDraw, items: list[str], y: int, *, fnt: ImageFont.FreeTypeFont, x: int = 96, gap: int = 18
) -> int:
    for i, item in enumerate(items, 1):
        draw.text((x, y), f"{i}.", font=font(fnt.size, bold=True), fill=MUTED)
        y = draw_paragraph(draw, (x + 48, y), item, fnt, max_width=WIDTH - x - 140) + gap
    return y


def doc_chrome(draw: ImageDraw.ImageDraw, title: str) -> int:
    """Grey toolbar strip of an online document editor; returns y below it."""
    draw.rectangle((0, 22, WIDTH, 86), fill="#e5e7eb")
    draw.text((40, 34), title, font=font(20, bold=True), fill=INK)
    draw.text((40, 60), "Файл   Правка   Вид   Вставка   Формат   Инструменты", font=font(14), fill=MUTED)
    for i, w in enumerate((60, 40, 40, 90, 60)):
        x = 520 + i * 80
        draw.rounded_rectangle((x, 56, x + w, 76), radius=4, fill="#f3f4f6", outline="#d1d5db")
    return 86


# ---------------------------------------------------------------- frames


@dataclass(frozen=True)
class FrameSpec:
    index: int
    timestamp_sec: float
    reason: str  # scene | interval
    caption: str  # for frames.json / docs


def frame_00_zoom_layout() -> Image.Image:
    img, d = new_slide(ZOOM_BG)
    d.text((32, 20), "Zoom · Урок русского языка · 6 класс", font=font(20), fill="#d1d5db")
    d.text((WIDTH - 290, 20), "● Идёт запись  00:00:06", font=font(18), fill="#f87171")
    tiles = [("Айгерим Сериковна", "АС", "#0ea5e9", True), ("Данияр", "Д", "#f59e0b", False)]
    tile_w, tile_h, gap = 580, 420, 40
    x = (WIDTH - (tile_w * 2 + gap)) // 2
    for name, initials, col, speaking in tiles:
        d.rounded_rectangle(
            (x, 90, x + tile_w, 90 + tile_h),
            radius=14,
            fill=ZOOM_TILE,
            outline="#22c55e" if speaking else ZOOM_TILE,
            width=4,
        )
        cx, cy, r = x + tile_w // 2, 90 + tile_h // 2 - 20, 90
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
        f = font(64, bold=True)
        d.text((cx - text_width(d, initials, f) // 2, cy - 40), initials, font=f, fill=WHITE)
        d.rounded_rectangle(
            (x + 16, 90 + tile_h - 54, x + 16 + text_width(d, name, font(22)) + 40, 90 + tile_h - 16),
            radius=8,
            fill="#111114",
        )
        d.text((x + 28, 90 + tile_h - 48), name, font=font(22), fill=WHITE)
        d.text((x + tile_w - 60, 90 + tile_h - 48), "●", font=font(22), fill="#22c55e")
        x += tile_w + gap
    # toolbar
    d.rectangle((0, HEIGHT - 86, WIDTH, HEIGHT), fill="#111114")
    labels = ["Звук", "Видео", "Участники (2)", "Чат", "Демонстрация экрана", "Запись", "Реакции"]
    f = font(16)
    total = sum(text_width(d, lb, f) + 60 for lb in labels)
    tx = (WIDTH - total) // 2
    for lb in labels:
        w = text_width(d, lb, f) + 40
        fill = "#16a34a" if lb.startswith("Демонстрация") else "#2a2a30"
        d.rounded_rectangle((tx, HEIGHT - 66, tx + w, HEIGHT - 22), radius=8, fill=fill)
        d.text((tx + 20, HEIGHT - 54), lb, font=f, fill=WHITE)
        tx += w + 20
    return img


def frame_01_title() -> Image.Image:
    img, d = new_slide(PAPER)
    share_badge(d)
    d.rectangle((0, 22, WIDTH, 300), fill=BLUE)
    d.text((64, 70), "Падежные окончания", font=font(58, bold=True), fill=WHITE)
    d.text((64, 140), "имён существительных", font=font(58, bold=True), fill=WHITE)
    d.text((64, 222), "Дательный и предложный падеж", font=font(32), fill="#bfdbfe")
    thumbnail_tiles(d)
    d.text((64, 340), "Русский язык · 6 класс · урок 12", font=font(26), fill=MUTED)
    y = 400
    d.rounded_rectangle((64, y, WIDTH - 64, y + 220), radius=16, fill=BLUE_SOFT)
    d.text((96, y + 24), "Цель урока", font=font(26, bold=True), fill=BLUE)
    draw_paragraph(
        d,
        (96, y + 70),
        "Научиться выбирать окончание существительного по вопросу: кому? чему? → -е / -у / -и;  "
        "о ком? о чём? где? → -е / -и.",
        font(26),
        max_width=WIDTH - 220,
    )
    d.text((96, y + 170), "Сабақтың мақсаты: септік жалғауларын сұрақ арқылы таңдау", font=font(20), fill=MUTED)
    return img


def frame_02_table() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    y0 = header_band(d, "Дательный и предложный падеж: окончания", BLUE, subtitle="Единственное число")
    # shift band below the share badge
    d.rectangle((0, 0, WIDTH, 22), fill="#16a34a")
    headers = ["Падеж", "Вопросы", "1 скл. (мама)", "2 скл. (брат, стол)", "3 скл. (дочь)", "Қазақша"]
    col_x = [48, 250, 480, 660, 920, 1090]
    y = y0 + 28
    d.rectangle((48, y, WIDTH - 40, y + 46), fill="#e0e7ff")
    for h, x in zip(headers, col_x, strict=True):
        d.text((x + 10, y + 11), h, font=font(19, bold=True), fill=BLUE)
    rows = [
        ("Дательный", "кому? чему?", "мам-Е", "брат-У, стол-У", "дочер-И", "-ға/-ге\n-қа/-ке"),
        (
            "Предложный",
            "о ком? о чём?\nгде? (в, на, о)",
            "о мам-Е",
            "о брат-Е, на стол-Е",
            "о дочер-И",
            "-да/-де\n-та/-те",
        ),
    ]
    y += 46
    for r_i, row in enumerate(rows):
        h = 150
        d.rectangle((48, y, WIDTH - 40, y + h), fill=WHITE if r_i % 2 == 0 else PAPER, outline="#c7d2fe")
        for c_i, (cell, x) in enumerate(zip(row, col_x, strict=True)):
            fnt = font(24, bold=True) if c_i == 0 else font(22)
            fill = INK
            if c_i in (2, 3, 4):
                fill = ORANGE
            yy = y + 24
            for line in cell.split("\n"):
                d.text((x + 10, yy), line, font=fnt, fill=fill)
                yy += fnt.size + 10
        y += h
    y += 24
    d.rounded_rectangle((48, y, WIDTH - 40, y + 90), radius=12, fill=GREEN_SOFT)
    draw_paragraph(
        d,
        (72, y + 16),
        "Запомни: окончание выбираем по ВОПРОСУ, а не по предлогу.  Я подарил цветы (кому?) мамЕ.  "
        "Книга лежит (где?) на столЕ.",
        font(24),
        max_width=WIDTH - 160,
        fill=GREEN,
    )
    return img


def frame_03_where_vs_whereto() -> Image.Image:
    img, d = new_slide(PAPER)
    share_badge(d)
    d.text((64, 50), "Куда? или Где?", font=font(52, bold=True), fill=INK)
    d.text((64, 118), "Самая частая путаница: направление (куда?) и место (где?)", font=font(24), fill=MUTED)
    thumbnail_tiles(d)
    cols = [
        (
            64,
            "КУДА?",
            "винительный падеж",
            GREEN,
            GREEN_SOFT,
            ["иду  в школУ", "идём  на почтУ", "ходили  в библиотекУ"],
            "мектепКЕ  ·  поштаҒА  ·  кітапханаҒА",
        ),
        (
            664,
            "ГДЕ?",
            "предложный падеж",
            ORANGE,
            ORANGE_SOFT,
            ["учусь  в школЕ", "работаю  на почтЕ", "сидим  в библиотекЕ"],
            "мектепТЕ  ·  поштаДА  ·  кітапханаДА",
        ),
    ]
    for x, q, case, col, soft, examples, kazakh in cols:
        d.rounded_rectangle((x, 170, x + 552, 650), radius=18, fill=soft)
        d.text((x + 32, 196), q, font=font(48, bold=True), fill=col)
        d.text((x + 32, 258), case, font=font(24), fill=col)
        yy = 316
        for ex in examples:
            d.text((x + 32, yy), ex, font=font(32), fill=INK)
            yy += 60
        d.line((x + 32, 520, x + 520, 520), fill=col, width=3)
        d.text((x + 32, 540), "Қазақша:", font=font(20, bold=True), fill=MUTED)
        d.text((x + 32, 572), kazakh, font=font(24), fill=INK)
    # arrow between columns
    d.polygon([(616, 400), (664, 400), (640, 440)], fill=MUTED)
    return img


def frame_04_exercise_2() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    header_band(
        d, "Упражнение 2. Поставь слово в нужный падеж", PURPLE, subtitle="Устно: задай вопрос и назови окончание"
    )
    d.rectangle((0, 0, WIDTH, 22), fill="#16a34a")
    items = [
        "Я подарил цветы (мама) ______ .",
        "Вчера мы были в (музей) ______ .",
        "Данияр идёт к (врач) ______ .",
        "Книга лежит на (стол) ______ .",
        "Расскажи мне о (брат) ______ .",
        "Мы ходили в (библиотека) ______ .",
    ]
    numbered_list(d, items, 150, fnt=font(32), gap=22)
    d.rounded_rectangle((860, 560, WIDTH - 40, 680), radius=12, fill=PURPLE_SOFT)
    d.text((884, 578), "Подсказка", font=font(20, bold=True), fill=PURPLE)
    d.text((884, 610), "кому? чему? → Д. п.", font=font(20), fill=INK)
    d.text((884, 640), "где? о ком? → П. п.", font=font(20), fill=INK)
    return img


def frame_05_whiteboard() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    # faint grid
    for gx in range(0, WIDTH, 40):
        d.line((gx, 22, gx, HEIGHT), fill="#f1f5f9")
    for gy in range(22, HEIGHT, 40):
        d.line((0, gy, WIDTH, gy), fill="#f1f5f9")
    d.text((40, 40), "Доска · разбор ошибки", font=font(22), fill=MUTED)
    f_big = font(54, bold=True)
    rows = [
        (120, "иду  В ШКОЛУ", "— куда?", "мектепКЕ", MARKER_BLUE),
        (250, "учусь  В ШКОЛЕ", "— где?", "мектепТЕ", MARKER_BLUE),
        (380, "ходили  В БИБЛИОТЕКУ", "— куда?", "кітапханаҒА", MARKER_BLUE),
    ]
    for y, phrase, q, kk, col in rows:
        d.text((60, y), phrase, font=f_big, fill=col)
        w = text_width(d, phrase, f_big)
        # circle the ending (last 2 letters)
        end_w = text_width(d, phrase[-2:], f_big)
        d.ellipse((60 + w - end_w - 14, y - 10, 60 + w + 16, y + 68), outline=MARKER_RED, width=5)
        d.text((60 + w + 40, y + 10), q, font=font(36), fill=MARKER_RED)
        d.text((60 + w + 40, y + 52), kk, font=font(24), fill=MUTED)
    d.line((60, 500, 700, 500), fill=MARKER_RED, width=4)
    d.text((60, 520), "иду в школЕ  ✗", font=font(40, bold=True), fill=MARKER_RED)
    d.line((60, 548, 60 + text_width(d, "иду в школЕ", font(40, bold=True)), 548), fill=MARKER_RED, width=4)
    d.text((60, 590), "Сначала вопрос — потом окончание!", font=font(30), fill=MARKER_BLUE)
    return img


EXERCISE_3 = [
    "После уроков я иду в (школа) ______ .",
    "Мой дедушка живёт в (деревня) ______ .",
    "Каждый день я помогаю (бабушка) ______ .",
    "Мы долго говорили о (поездка) ______ .",
    "Летом мы поедем в (Астана) ______ .",
    "Вчера я написал письмо (друг) ______ .",
]


def frame_06_exercise_3_blank() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    y = doc_chrome(d, "Урок 12 — упражнения Данияра")
    d.rectangle((120, y + 20, WIDTH - 120, HEIGHT), fill=WHITE, outline="#e5e7eb")
    d.text((160, y + 44), "Упражнение 3. Допиши окончание (6 предложений)", font=font(28, bold=True), fill=INK)
    d.text((160, y + 86), "Печатай ответ прямо в документе", font=font(18), fill=MUTED)
    numbered_list(d, EXERCISE_3, y + 130, fnt=font(26), x=160, gap=14)
    return img


def frame_07_exercise_3_typed() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    y = doc_chrome(d, "Урок 12 — упражнения Данияра")
    d.rectangle((40, y + 20, 880, HEIGHT), fill=WHITE, outline="#e5e7eb")
    d.text((72, y + 40), "Упражнение 3. Допиши окончание", font=font(26, bold=True), fill=INK)
    answers = ["в школу", "в деревне", "бабушке", "о поездке", "в Астане", ""]
    marks = ["✓", "✓", "✓", "✓", "?", ""]
    yy = y + 90
    for i, (item, ans, mark) in enumerate(zip(EXERCISE_3, answers, marks, strict=True), 1):
        base = item.replace(" ______ .", "")
        d.text((72, yy), f"{i}. {base}", font=font(22), fill=INK)
        if ans:
            ax = 72 + text_width(d, f"{i}. {base} ", font(22))
            if mark == "?":
                d.rectangle(
                    (ax - 4, yy - 2, ax + text_width(d, ans, font(22, bold=True)) + 4, yy + 28), fill=YELLOW_SOFT
                )
            d.text((ax, yy), ans, font=font(22, bold=True), fill=MARKER_BLUE)
            d.text(
                (ax + text_width(d, ans, font(22, bold=True)) + 14, yy - 2),
                mark,
                font=font(24, bold=True),
                fill=GREEN if mark == "✓" else ORANGE,
            )
        else:
            d.rectangle(
                (
                    72 + text_width(d, f"{i}. {base} ", font(22)),
                    yy + 2,
                    72 + text_width(d, f"{i}. {base} ", font(22)) + 3,
                    yy + 26,
                ),
                fill=INK,
            )
        yy += 64
    # comments panel (structurally different from the blank version)
    d.rectangle((900, y + 20, WIDTH, HEIGHT), fill="#f3f4f6")
    d.text((924, y + 40), "Комментарии", font=font(22, bold=True), fill=INK)
    d.rounded_rectangle((924, y + 84, WIDTH - 24, y + 230), radius=10, fill=WHITE, outline="#d1d5db")
    d.ellipse((940, y + 100, 972, y + 132), fill="#0ea5e9")
    d.text((948, y + 106), "АС", font=font(14, bold=True), fill=WHITE)
    d.text((984, y + 104), "Айгерим С.", font=font(16, bold=True), fill=INK)
    draw_paragraph(
        d, (940, y + 144), "№5: задай вопрос — куда? или где? Поедем … куда?", font(18), max_width=WIDTH - 24 - 956
    )
    d.rounded_rectangle((924, y + 250, WIDTH - 24, y + 330), radius=10, fill=WHITE, outline="#d1d5db")
    d.text((940, y + 266), "Решено самостоятельно: 4 из 6", font=font(17), fill=GREEN)
    d.text((940, y + 294), "С подсказкой: №5, №6", font=font(17), fill=ORANGE)
    return img


def frame_08_tsya_mistake() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    y = doc_chrome(d, "Урок 12 — упражнения Данияра")
    d.rectangle((120, y + 20, WIDTH - 120, HEIGHT), fill=WHITE, outline="#e5e7eb")
    d.text((160, y + 44), "Упражнение 4. Составь два предложения", font=font(28, bold=True), fill=INK)
    d.text((160, y + 86), "со словами «учиться» и «учится»", font=font(22), fill=MUTED)
    f = font(40)
    d.text((160, y + 170), "1. Данияр хочет учится в Алматы.", font=f, fill=MARKER_BLUE)
    d.text((160, y + 260), "2. Мой брат учится в университете.", font=f, fill=MARKER_BLUE)
    # typing cursor after the last sentence
    cx = 160 + text_width(d, "2. Мой брат учится в университете.", f) + 6
    d.rectangle((cx, y + 262, cx + 3, y + 304), fill=INK)
    d.text((160, y + 380), "Напечатано учеником · 26:30", font=font(18), fill=MUTED)
    return img


def frame_09_tsya_fixed() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    y = doc_chrome(d, "Урок 12 — упражнения Данияра")
    d.rectangle((120, y + 20, WIDTH - 120, 420), fill=WHITE, outline="#e5e7eb")
    d.text((160, y + 44), "Упражнение 4. Исправленный вариант", font=font(28, bold=True), fill=INK)
    f = font(40)
    d.text((160, y + 120), "1. Данияр хочет учиться в Алматы.", font=f, fill=MARKER_BLUE)
    x_word = 160 + text_width(d, "1. Данияр хочет ", f)
    w_word = text_width(d, "учиться", f)
    d.line((x_word, y + 170, x_word + w_word, y + 170), fill=GREEN, width=5)
    d.text(
        (160 + text_width(d, "1. Данияр хочет учиться в Алматы.", f) + 24, y + 122),
        "✓",
        font=font(44, bold=True),
        fill=GREEN,
    )
    d.text((160, y + 210), "2. Мой брат учится в университете.", font=f, fill=MARKER_BLUE)
    d.text(
        (160 + text_width(d, "2. Мой брат учится в университете.", f) + 24, y + 212),
        "✓",
        font=font(44, bold=True),
        fill=GREEN,
    )
    # rule box
    d.rounded_rectangle((120, 450, WIDTH - 120, 690), radius=16, fill=GREEN_SOFT)
    d.text((160, 472), "Проверка вопросом", font=font(28, bold=True), fill=GREEN)
    d.text((160, 522), "что делать?   →   учитьСЯ   (-ться, с мягким знаком)", font=font(30), fill=INK)
    d.text((160, 572), "что делает?   →   учитСЯ      (-тся, без мягкого знака)", font=font(30), fill=INK)
    d.text((160, 630), "хочет (что делать?) учиться · брат (что делает?) учится", font=font(24), fill=MUTED)
    return img


def frame_10_recap() -> Image.Image:
    img, d = new_slide(PURPLE_SOFT)
    share_badge(d)
    header_band(d, "Что мы сегодня узнали", PURPLE)
    d.rectangle((0, 0, WIDTH, 22), fill="#16a34a")
    thumbnail_tiles(d)
    items = [
        ("кому? чему?", "дательный: мамЕ, братУ, дочерИ"),
        ("где? о ком? о чём?", "предложный: в школЕ, о братЕ, о дочерИ"),
        ("куда? ≠ где?", "в школУ (иду) — в школЕ (учусь)"),
        ("-тся / -ться", "что делает? учится — что делать? учиться"),
    ]
    y = 150
    for q, a in items:
        d.rounded_rectangle((64, y, WIDTH - 64, y + 100), radius=14, fill=WHITE)
        d.text((96, y + 20), q, font=font(30, bold=True), fill=PURPLE)
        d.text((520, y + 26), a, font=font(28), fill=INK)
        y += 118
    d.text((64, y + 10), "Проверь себя: «Я мечтаю о (поездка)… в (Астана)…»", font=font(24), fill=MUTED)
    return img


def frame_11_homework() -> Image.Image:
    img, d = new_slide(WHITE)
    share_badge(d)
    header_band(d, "Домашнее задание", ORANGE, subtitle="10–15 минут, к четвергу")
    d.rectangle((0, 0, WIDTH, 22), fill="#16a34a")
    items = [
        "Упражнение 148 (учебник, с. 92): поставить слова в дательный и предложный падеж.",
        "Написать 5 предложений «куда я иду» и 5 предложений «где я нахожусь».",
        "К каждому глаголу на -тся/-ться задать вопрос: что делает? или что делать?",
        "Рассказать маме по-русски, чем отличаются «в школу» и «в школе».",
    ]
    numbered_list(d, items, 160, fnt=font(28), gap=22)
    d.rounded_rectangle((64, 580, WIDTH - 64, 680), radius=14, fill=ORANGE_SOFT)
    d.text((96, 600), "Следующий урок: четверг, 16:00 · Тема: родительный падеж", font=font(26), fill=ORANGE)
    d.text((96, 640), "Сабақ аяқталды. Жарайсың, Данияр!", font=font(22), fill=MUTED)
    return img


FRAMES: list[tuple[FrameSpec, callable]] = [
    (FrameSpec(0, 6.0, "interval", "Zoom-раскладка: два участника, аватары, запись началась"), frame_00_zoom_layout),
    (FrameSpec(1, 160.0, "scene", "Титульный слайд: тема и цель урока"), frame_01_title),
    (FrameSpec(2, 200.0, "scene", "Таблица окончаний дательного и предложного падежа"), frame_02_table),
    (FrameSpec(3, 676.0, "scene", "Слайд «Куда? или Где?» — направление и место"), frame_03_where_vs_whereto),
    (FrameSpec(4, 932.0, "scene", "Упражнение 2: шесть предложений с пропусками"), frame_04_exercise_2),
    (FrameSpec(5, 1168.0, "scene", "Доска: разбор ошибки «иду в школе», параллели с казахским"), frame_05_whiteboard),
    (FrameSpec(6, 1256.0, "scene", "Упражнение 3 в документе: шесть предложений (пустые)"), frame_06_exercise_3_blank),
    (
        FrameSpec(7, 1330.0, "scene", "Упражнение 3: напечатанные ответы ученика, №5 выделено"),
        frame_07_exercise_3_typed,
    ),
    (
        FrameSpec(8, 1590.0, "scene", "Упражнение 4: напечатано «хочет учится» (ошибка -тся/-ться)"),
        frame_08_tsya_mistake,
    ),
    (
        FrameSpec(9, 1700.0, "scene", "Упражнение 4: исправлено «учиться», правило проверки вопросом"),
        frame_09_tsya_fixed,
    ),
    (FrameSpec(10, 1846.0, "scene", "Итоги урока: четыре правила"), frame_10_recap),
    (FrameSpec(11, 1962.0, "scene", "Домашнее задание и следующий урок"), frame_11_homework),
]


def save_jpeg(img: Image.Image, path: Path) -> int:
    """Save under the size budget by lowering quality if needed; return the byte size."""
    for quality in (88, 82, 76, 70, 62, 55):
        img.save(path, format="JPEG", quality=quality, optimize=True, progressive=True)
        size = path.stat().st_size
        if size <= MAX_JPEG_BYTES:
            return size
    return path.stat().st_size


def build(out_dir: Path) -> list[dict]:
    """Render all frames into ``out_dir`` and write ``frames.json``; return its content."""
    out_dir.mkdir(parents=True, exist_ok=True)
    meta: list[dict] = []
    for spec, fn in FRAMES:
        img = fn()
        path = out_dir / f"{spec.index:04d}.jpg"
        size = save_jpeg(img, path)
        meta.append(
            {
                "index": spec.index,
                "timestamp_sec": spec.timestamp_sec,
                "reason": spec.reason,
                "caption": spec.caption,
                "file": path.name,
                "width": WIDTH,
                "height": HEIGHT,
            }
        )
        print(f"{path.name}  t={spec.timestamp_sec:7.1f}s  {size / 1024:6.1f} KB  {spec.caption}")
    (out_dir / "frames.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output directory (default: demo/lesson_01/frames)"
    )
    args = parser.parse_args(argv)
    meta = build(args.out)
    print(f"{len(meta)} frames written to {args.out} (fonts: {REGULAR_TTF.name}, {BOLD_TTF.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
