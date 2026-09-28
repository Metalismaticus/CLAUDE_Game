#!/usr/bin/env python3
"""Лист сравнения: образец рядом с вариантами, чтобы выбирать глазом, а не словами.

Развёрнут плагином studio (/setup). Нужна Pillow, numpy не нужна:
python -m pip install pillow

    python tools/look_sheet.py --ref docs/refs/деревья/ref-valheim-1.png \
        --var A=shots/a.png --var B=shots/b.png --crop листва=0.40,0.20,0.25,0.25 \
        --ref-crop листва=0.55,0.10,0.30,0.30 --time вечер --axis форма \
        --out docs/refs/деревья/sheet-2026-09-25.png --json docs/refs/деревья/sheet-2026-09-25.json
    python tools/look_sheet.py --only-ref --ref a.png --ref b.png --out sheet.png --json refs.json
    python tools/look_sheet.py --sanity shots/lip.png --var A=shots/a.png --var B=shots/b.png         --prev shots/a.png=rounds/p2-k1-r1/a.png [--axis форма]

Лист: ряд 1 — REF | A | B … одной высоты с крупными подписями (рамки — где
вырезки); ряд 2 — оттенки серого; ряд 3 — размытие («прищур»); ряд 4 —
вырезки в исходном разрешении (x,y,w,h — доли кадра 0..1); палитра из 6 цветов
с HEX и числа. Длинная сторона листа ≤ 2576 px. `--only-ref` — только образцы
(лист можно не строить: хватит `--json`).

`--ref-crop ИМЯ=x,y,w,h` — своя вырезка у REF под тем же именем, что у
`--crop`: образец другого ракурса режется там, где его предмет (задана —
пропорции целых кадров не сверяются, только вырезок). `--time <час>` — час
суток кадров (столбец «Кадры» паспорта): в шапку листа и в JSON, чтобы
проверяющий сверил с паспортом. `--axis <ось>` — ось выбора («форма», «цвет»,
«приём»): в JSON и в шапку; при `--axis форма` варианты, различимые только
оттенком, — брак, код 1; без оси — предупреждение; при другой оси («приём»,
«цвет», «свет» — варианты и должны быть одной формы) — заметка.

JSON: у каждой картинки и вырезки — средняя яркость, контраст (ст. откл.
яркости), средняя насыщенность, гистограмма тона (12 корзин по 30°, только
цветные пиксели), палитра; отличия каждой картинки от первого REF; `time`,
`axis`, `ref_crops`; `defects` — брак (код 1): при `--axis форма` варианты
различаются только оттенком; `warnings` — совет, показ листа не запрещает:
пропорции REF и варианта расходятся больше чем вдвое (нужна вырезка REF или
образец того же ракурса), «только оттенком» без оси, совпадение краёв «на
грани»; `notes` — к сведению: при другой оси варианты одной формы, разного
тона; `edge_same` — совпадение карты краёв по парам вариантов. Числа — грубая
ориентировка по свету и цвету, не мера похожести: решает владелец выбором по
листу.

`--sanity [<png>…] [--var A=<png>…] [--prev <кадр.png>=<прошлый.png>…]` — без
листа, грубый брак до «Вижу:»: каждый кадр — не пустой ли, не однотонный ли
(серое или чёрное окно: контраст и детализация ниже порога); кадры `--var` —
не совпадают ли почти попарно (варианты не отличить глазом; голые кадры
`--sanity` между собой не сравниваются — два одинаковых снимка стенда
проверяют повторяемость) и не различаются ли **только оттенком** — там, где
цвет разный, сила краёв (FIND_EDGES без цвета) совпадает (корреляция ≥ 0,92;
0,9–0,92 — «на грани», не брак): та же форма, подкрашенная по-разному; с
`--axis форма` это брак, без оси — предупреждение, при другой оси — заметка;
кадр из `--prev` — не совпадает ли почти со снимком прошлого круга (правка не
дошла до кадра). Каждая находка — одной строкой «брак: <файл> — <что
словами>»; код 1 — брак, кадры владельцу не показывать. Пороги подобраны на
снимках водопада VoxelWorld. Ограничение: варианты снимать в одном свете и
часе, глобальный тон между ними не менять — подкраска всего кадра держит
корреляцию краёв на неизменном рельефе, и новая форма малого предмета (крона
в кадре меньше трети) пройдёт как «только оттенком».

Коды возврата: 0 — готово (у --sanity — «кадры в порядке»); 1 — брак
(--sanity) или «только оттенком» при --axis форма; 2 — нет Pillow, нет файла
или неверные аргументы.
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat
except ImportError:
    Image = None
else:
    RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
    RESAMPLE_BOX = getattr(Image, "Resampling", Image).BOX
    MEDIANCUT = getattr(Image, "Quantize", Image).MEDIANCUT

LIMIT = 2576  # длинная сторона листа
NOTE = "статистика — грубая ориентировка по свету и цвету, не мера похожести"
STATS_SIDE = 512  # числа считаются на уменьшенной копии: разрешение кадра их не сдвигает
MAX_IMAGES = 8
MAX_CROPS = 4
HUE_NAMES = ("красный", "оранжевый", "жёлтый", "жёлто-зелёный", "зелёный", "бирюзовый",
             "голубой", "лазурный", "синий", "фиолетовый", "пурпурный", "малиновый")
LEGEND = {
    "brightness": "средняя яркость 0..1",
    "contrast": "контраст: стандартное отклонение яркости 0..1",
    "saturation": "средняя насыщенность 0..1",
    "colored_share": "доля цветных пикселей (насыщенность и яркость ≥ 0.1)",
    "hue_hist": "доли цветных пикселей по 12 корзинам тона, центры hue_bins_deg",
    "palette": "6 цветов квантованием Pillow, по убыванию доли",
    "diff_from_ref": "картинка минус первый REF; hue_hist и palette_shift — расстояние 0..1",
    "time": "час суток кадров из паспорта (--time); проверяющий сверяет с паспортом",
    "axis": "ось выбора (--axis): форма, цвет, приём",
    "defects": "брак, код 1: при --axis форма варианты различаются только оттенком — лист владельцу не показывать, развести приёмами",
    "warnings": "совет, показ листа не запрещает: пропорции REF и варианта (нужна --ref-crop или образец того же ракурса), "
                "«только оттенком» без оси, совпадение краёв на грани — судит координатор глазом",
    "notes": "к сведению: при оси приём, цвет или свет варианты одной формы, разного тона — допустимо",
    "edge_same": "совпадение карты краёв по парам вариантов 0..1 там, где цвет разный: ≥ 0.92 — та же форма, 0.9–0.92 — на грани",
}

BG = (38, 38, 38)
TEXT = (236, 236, 236)
DIM = (160, 160, 160)
REF_COLOR = (255, 200, 87)
VAR_COLOR = (130, 200, 255)
CROP_COLORS = ((255, 64, 160), (0, 220, 255), (170, 255, 60), (255, 150, 0))

M, G = 24, 16  # поле листа и промежуток между столбцами
LBL, SUB, RH, CAP, PS, SL, FOOT, HEAD = 58, 28, 32, 26, 26, 23, 34, 30
MIN_CW = 250  # столбец не уже: иначе не влезут числа
STAT_LINES = 5
ASPECT_GAP = 2.0  # пропорции REF и варианта расходятся больше чем вдвое — предупреждение
FORM_AXIS = "форма"

# --sanity: числа на копии шириной 256 px. На снимках водопада VoxelWorld у
# настоящих кадров контраст ≥ 0.08, детализация ≥ 0.02, одна яркость ≤ 69 %
# кадра; кадры одной сцены без правки расходятся меньше чем в 0.03 % пикселей.
SANITY_SIDE = 256
FLAT_CONTRAST = 0.03  # ст. откл. яркости 0..1
FLAT_DETAIL = 0.006  # средний перепад соседних пикселей 0..1
FLAT_SHARE = 0.97  # доля кадра в одной яркости ±3
SAME_LEVEL = 13  # пиксель «другой», если разница больше 12 из 255
SAME_SHARE = 0.0003  # другой меньше чем 0.03 % кадра — кадры совпадают
EDGE_SAME = 0.9  # сила краёв в перекрашенной области совпадает (корреляция ≥ 0.9) — только оттенком
EDGE_SURE = 0.92  # ниже — «на грани»: подкраски 0.91–0.99, формы 0.45–0.88, зазор три сотых — не брак
EDGE_GROW = 5  # перекрашенная область растёт на 2 px, чтобы взять её края
KINDS = ("брак", "предупреждение", "заметка")


class Args(argparse.ArgumentParser):
    def error(self, message):
        fail(2, f"неверные аргументы: {message}")


def fail(code, text):
    print(text, file=sys.stderr)
    sys.exit(code)


def flat(groups):
    return [item for group in groups for item in group]


def parse_vars(items):
    result, used = [], set()
    letters = iter("ABCDEFGH")
    for item in items:
        label, path = "", item
        if "=" in item:
            left, right = item.split("=", 1)
            if left and len(left) <= 16 and not any(c in left for c in "/\\:."):
                label, path = left.strip(), right
        if not label:
            label = next(c for c in letters if c not in used)
        if label in used or label.upper().startswith("REF"):
            fail(2, f"неверные аргументы: имя варианта «{label}» повторяется или занято образцом")
        if not path:
            fail(2, f"неверные аргументы: у варианта «{label}» нет файла (пишется A=<png>)")
        used.add(label)
        result.append((label, path))
    return result


def parse_crops(items, flag="--crop"):
    crops = []
    for n, item in enumerate(items, 1):
        name, _, box = item.rpartition("=")
        name = name.strip() or f"вырезка {n}"
        try:
            x, y, w, h = (float(v) for v in box.split(","))
        except ValueError:
            fail(2, f"неверные аргументы: вырезка {flag} «{item}» — нужно <имя>=x,y,w,h, доли 0..1")
        eps = 1e-6
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
                and x + w <= 1 + eps and y + h <= 1 + eps):
            fail(2, f"неверные аргументы: вырезка {flag} «{item}» выходит за кадр — доли 0..1, x+w и y+h ≤ 1")
        crops.append((name, (x, y, w, h)))
    if len({name for name, _ in crops}) != len(crops):
        fail(2, f"неверные аргументы: имена вырезок {flag} повторяются")
    return crops


def load(path):
    if not os.path.isfile(path):
        fail(2, f"нет файла: {path}")
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        fail(2, f"не открыть картинку {path}: {error}")
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        backdrop = Image.new("RGBA", image.size, (128, 128, 128, 255))  # прозрачное — на нейтральный серый
        backdrop.alpha_composite(image)
        image = backdrop
    return image.convert("RGB")


def crop_box(image, box):
    x, y, w, h = box
    left, top = round(x * image.width), round(y * image.height)
    right = max(left + 1, min(image.width, round((x + w) * image.width)))
    bottom = max(top + 1, min(image.height, round((y + h) * image.height)))
    return left, top, right, bottom


def stats(image):
    small = image.copy()
    small.thumbnail((STATS_SIDE, STATS_SIDE), RESAMPLE_BOX)
    luma = ImageStat.Stat(small.convert("L"))
    hue, sat, val = small.convert("HSV").split()
    mask = ImageChops.multiply(sat.point(lambda v: 255 if v >= 26 else 0),
                               val.point(lambda v: 255 if v >= 26 else 0))
    counts = hue.histogram(mask=mask)
    colored = sum(counts)
    bins = [0] * 12
    for value, count in enumerate(counts):
        bins[int(((value * 360 / 256) + 15) % 360 // 30)] += count
    quant = small.quantize(colors=6, method=MEDIANCUT)
    colors = quant.getpalette() or []
    used = sorted(quant.getcolors() or [], reverse=True)
    total = sum(count for count, _ in used) or 1
    palette = [{"hex": "#%02X%02X%02X" % tuple(colors[index * 3:index * 3 + 3]),
                "share": round(count / total, 3)} for count, index in used]
    return {
        "brightness": round(luma.mean[0] / 255, 3),
        "contrast": round(luma.stddev[0] / 255, 3),
        "saturation": round(ImageStat.Stat(sat).mean[0] / 255, 3),
        "colored_share": round(colored / (small.width * small.height), 3),
        "hue_hist": [round(b / colored, 3) if colored else 0.0 for b in bins],
        "palette": palette,
    }


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def diff(item, ref):
    shift = 0.0
    for color in item["palette"]:
        near = min((sum((a - b) ** 2 for a, b in zip(rgb(color["hex"]), rgb(other["hex"]))) ** 0.5
                    for other in ref["palette"]), default=0.0)
        shift += color["share"] * near / 441.673
    result = {key: round(item[key] - ref[key], 3)
              for key in ("brightness", "contrast", "saturation", "colored_share")}
    result["hue_hist"] = round(sum(abs(a - b) for a, b in zip(item["hue_hist"], ref["hue_hist"])) / 2, 3)
    result["palette_shift"] = round(shift, 3)
    return result


_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _fonts:
        return _fonts[key]
    names = (("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf") if bold else
             ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"))
    chosen = None
    for name in names:
        try:
            chosen = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    if chosen is None:
        try:
            chosen = ImageFont.load_default(size)
        except TypeError:  # Pillow < 10.1
            chosen = ImageFont.load_default()
    _fonts[key] = chosen
    return chosen


def fit(draw, text, face, width):
    if draw.textlength(text, font=face) <= width:
        return text
    while text and draw.textlength(text + "…", font=face) > width:
        text = text[:-1]
    return text + "…"


def signed(value):
    return ("+" if value > 0 else "−" if value < 0 else "±") + f"{abs(value):.2f}"


def fmt_box(box):
    return ",".join(f"{v:g}" for v in box)


def layout(entries, crops, height, crop_max, head):
    """Размеры листа при высоте ряда `height` и пределе высоты вырезки `crop_max`."""
    images = [entry["image"] for entry in entries]
    cw = max(MIN_CW, max(round(height * im.width / im.height) for im in images))
    rows = []
    for name, _ in crops:
        tiles = []
        for entry in entries:
            left, top, right, bottom = crop_box(entry["image"], entry["boxes"][name])
            pw, ph = right - left, bottom - top
            scale = min(1.0, cw / pw, crop_max / ph)
            tiles.append(((left, top, right, bottom), scale, max(1, round(pw * scale)), max(1, round(ph * scale))))
        rows.append(tiles)
    width = 2 * M + len(images) * cw + (len(images) - 1) * G
    total = (M + (HEAD if head else 0) + LBL + SUB + height + 2 * (RH + height)
             + sum(RH + max(t[3] for t in tiles) + CAP for tiles in rows)
             + RH + 6 * PS + STAT_LINES * SL + FOOT + M)
    return cw, rows, width, total


def build_sheet(entries, crops, head=""):
    images = [entry["image"] for entry in entries]
    count = len(images)
    avail = LIMIT - 2 * M - (count - 1) * G
    height = min(720, max(im.height for im in images))
    height = max(80, min(height, int(avail / count / max(im.width / im.height for im in images))))
    crop_max = 640
    cw, rows, width, total = layout(entries, crops, height, crop_max, head)
    # не влезает: сначала ужать вырезки, потом главные ряды, потом вырезки ещё
    for shrink_height, floor in ((False, 240), (True, 160), (False, 80)):
        while total > LIMIT and (height if shrink_height else crop_max) > floor:
            if shrink_height:
                height = int(height * 0.92)
            else:
                crop_max = int(crop_max * 0.85)
            cw, rows, width, total = layout(entries, crops, height, crop_max, head)

    sheet = Image.new("RGB", (width, total), BG)
    draw = ImageDraw.Draw(sheet)
    big, small, bold_small = font(44, True), font(17), font(18, True)
    xs = [M + i * (cw + G) for i in range(count)]
    y = M
    if head:  # шапка: час суток и ось выбора — проверяющий сверяет с паспортом
        draw.text((M, y + 4), fit(draw, head, bold_small, width - 2 * M), font=bold_small, fill=REF_COLOR)
        y += HEAD

    tiles = []
    for i, entry in enumerate(entries):
        im = entry["image"]
        tile = im.resize((round(height * im.width / im.height), height), RESAMPLE)
        tiles.append(tile)
        color = REF_COLOR if entry["role"] == "ref" else VAR_COLOR
        draw.text((xs[i], y), entry["label"], font=big, fill=color)
        name = f"{os.path.basename(entry['file'])} · {im.width}×{im.height}"
        draw.text((xs[i], y + LBL), fit(draw, name, small, cw), font=small, fill=DIM)
    y += LBL + SUB
    for i, tile in enumerate(tiles):
        x = xs[i] + (cw - tile.width) // 2
        sheet.paste(tile, (x, y))
        for k, (name, _) in enumerate(crops):
            bx, by, bw, bh = entries[i]["boxes"][name]
            rect = (x + bx * tile.width, y + by * height,
                    x + (bx + bw) * tile.width - 1, y + (by + bh) * height - 1)
            draw.rectangle(rect, outline=CROP_COLORS[k], width=3)
            draw.text((rect[0] + 5, rect[1] + 2), str(k + 1), font=bold_small, fill=CROP_COLORS[k])
    y += height

    for title, make in (("оттенки серого — свет и тень без цвета", lambda t: t.convert("L").convert("RGB")),
                        ("прищур — размытие: крупные пятна света и цвета",
                         lambda t: t.filter(ImageFilter.GaussianBlur(max(2, height / 40))))):
        draw.text((M, y + 6), title, font=bold_small, fill=TEXT)
        y += RH
        for i, tile in enumerate(tiles):
            sheet.paste(make(tile), (xs[i] + (cw - tile.width) // 2, y))
        y += height

    for k, ((name, box), row) in enumerate(zip(crops, rows)):
        draw.rectangle((M, y + 9, M + 14, y + 23), fill=CROP_COLORS[k])
        head_line = f"вырезка {k + 1} «{name}» — x,y,w,h {fmt_box(box)}, исходное разрешение"
        ref_boxes = {fmt_box(e["boxes"][name]) for e in entries if e["role"] == "ref"} - {fmt_box(box)}
        if ref_boxes:
            head_line += f"; у REF — {', '.join(sorted(ref_boxes))}"
        draw.text((M + 22, y + 6), fit(draw, head_line, bold_small, width - 2 * M - 22), font=bold_small, fill=TEXT)
        y += RH
        row_h = max(t[3] for t in row)
        for i, (pixels, scale, tw, th) in enumerate(row):
            part = images[i].crop(pixels)
            if scale < 1:
                part = part.resize((tw, th), RESAMPLE)
            sheet.paste(part, (xs[i] + (cw - tw) // 2, y))
            pw, ph = pixels[2] - pixels[0], pixels[3] - pixels[1]
            cap = f"1:1 · {pw}×{ph}" if scale >= 1 else f"уменьшено ×{scale:.2f} · {pw}×{ph}"
            draw.text((xs[i], y + row_h + 3), fit(draw, cap, small, cw), font=small, fill=DIM)
        y += row_h + CAP

    draw.text((M, y + 6), "палитра — 6 цветов, доля; числа — у REF как есть, у остальных «от REF»",
              font=bold_small, fill=TEXT)
    y += RH
    for i, entry in enumerate(entries):
        st, x = entry["stats"], xs[i]
        for n, color in enumerate(st["palette"][:6]):
            top = y + n * PS
            draw.rectangle((x, top + 2, x + PS - 6, top + PS - 4), fill=rgb(color["hex"]), outline=DIM)
            share = f"{round(color['share'] * 100)}%" if color["share"] >= 0.005 else "<1%"
            draw.text((x + PS + 2, top + 1), f"{color['hex']}  {share}", font=small, fill=TEXT)
        ty = y + 6 * PS
        top_hues = sorted(range(12), key=lambda b: -st["hue_hist"][b])[:2]
        hue_text = ", ".join(HUE_NAMES[b] for b in top_hues if st["hue_hist"][b] > 0) or "нет цвета"
        if i == 0:
            lines = [f"яркость {st['brightness']:.2f}", f"контраст {st['contrast']:.2f}",
                     f"насыщенность {st['saturation']:.2f}", f"тон: {hue_text}",
                     f"цветных пикселей {round(st['colored_share'] * 100)}%"]
        else:
            d = entry["diff"]
            lines = [f"яркость {st['brightness']:.2f} ({signed(d['brightness'])})",
                     f"контраст {st['contrast']:.2f} ({signed(d['contrast'])})",
                     f"насыщенность {st['saturation']:.2f} ({signed(d['saturation'])})",
                     f"тон: {hue_text}",
                     f"от {d['vs']}: тон {d['hue_hist']:.2f} · палитра {d['palette_shift']:.2f}"]
        for n, line in enumerate(lines):
            draw.text((x, ty + n * SL), fit(draw, line, small, cw), font=small, fill=TEXT)
    y += 6 * PS + STAT_LINES * SL

    draw.text((M, y + 6), fit(draw, NOTE, font(20), width - 2 * M), font=font(20), fill=DIM)
    if max(sheet.size) > LIMIT:  # вырезок и картинок слишком много: ужать весь лист
        scale = LIMIT / max(sheet.size)
        sheet = sheet.resize((int(sheet.width * scale), int(sheet.height * scale)), RESAMPLE)
    return sheet


def sanity_small(path):
    return shrink(load(path))


def shrink(image):
    width = min(SANITY_SIDE, image.width)
    return image.resize((width, max(1, round(width * image.height / image.width))), RESAMPLE_BOX)


def flat_problem(small):
    """Пустой или однотонный кадр — слова для человека, иначе None."""
    gray = small.convert("L")
    contrast = ImageStat.Stat(gray).stddev[0] / 255
    edges = gray.filter(ImageFilter.FIND_EDGES).crop((1, 1, max(2, gray.width - 1), max(2, gray.height - 1)))
    detail = ImageStat.Stat(edges).mean[0] / 255
    hist = gray.histogram()
    share = max(sum(hist[max(0, i - 3):i + 4]) for i in range(256)) / (gray.width * gray.height)
    if contrast >= FLAT_CONTRAST and detail >= FLAT_DETAIL and share < FLAT_SHARE:
        return None
    tone = "чёрное" if ImageStat.Stat(gray).mean[0] < 24 else "однотонное"
    return (f"пустой кадр: почти одного цвета ({tone} окно: продукт не отрисовал сцену?); "
            f"контраст {contrast:.3f}, детализация {detail:.3f}, одной яркости {share:.0%} кадра")


def changed_share(a, b):
    """Доля заметно разных пикселей; None — кадры разной формы, не сравнить."""
    if a.size != b.size:
        return None
    soft = ImageFilter.GaussianBlur(1)  # шум рендера и сжатия не в счёт
    bands = ImageChops.difference(a.filter(soft), b.filter(soft)).split()
    most = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])  # по цвету, не по яркости
    return sum(most.histogram()[SAME_LEVEL:]) / (a.width * a.height)


def pixels(image):
    return list(getattr(image, "get_flattened_data", image.getdata)())


def hue_only(a, b):
    """(совпадение краёв там, где цвет разный, доля разных пикселей) или None — не сравнить.

    Где цвет разный, сравнивается сила краёв (FIND_EDGES без цвета): подкраска её не двигает,
    новая форма — двигает. Порог 0.9 снят с 55 пар снимков VoxelWorld: подкраски неба 0.92–0.99,
    варианты формы кроны, полотна и рельефа 0.29–0.88. Ограничение: подкраска всего кадра при
    новой форме малого предмета (крона меньше трети кадра) даёт 0.98 — корреляцию держит
    неизменный рельеф; варианты снимать в одном свете и часе, глобальный тон не менять.
    """
    changed = changed_share(a, b)
    if changed is None or changed < SAME_SHARE:
        return None  # разной формы или почти одинаковы — это другие строки
    soft = ImageFilter.GaussianBlur(1)
    a, b = a.filter(soft), b.filter(soft)
    bands = ImageChops.difference(a, b).split()
    most = ImageChops.lighter(ImageChops.lighter(bands[0], bands[1]), bands[2])
    mask = pixels(most.point(lambda v: 255 if v >= SAME_LEVEL else 0).filter(ImageFilter.MaxFilter(EDGE_GROW)))
    ea = pixels(a.convert("L").filter(ImageFilter.FIND_EDGES))
    eb = pixels(b.convert("L").filter(ImageFilter.FIND_EDGES))
    xs = [x for m, x in zip(mask, ea) if m]
    ys = [y for m, y in zip(mask, eb) if m]
    n = len(xs)
    if not n:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    if not sxx or not syy:  # краёв нет вовсе: ровная заливка перекрашена
        same = 1.0 if max(mx, my) < 1 else 0.0
    else:
        same = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5
    return same, changed


def hue_only_lines(variants, small, axis):
    """По парам вариантов: строки (вид из KINDS, текст) о совпадении краёв и `same` по парам для JSON.

    Края там же (≥ EDGE_SURE): без оси — предупреждение «варианты различаются только оттенком», при
    `--axis форма` — брак, при другой оси (приём, цвет, свет — варианты и должны быть одной формы) —
    заметка; 0.9–0.92 — «на грани», предупреждение при любой оси.
    """
    lines, edges = [], {}
    for i, (la, a) in enumerate(variants):
        for lb, b in variants[i + 1:]:
            found = hue_only(small[a], small[b])
            if not found:
                continue
            same, changed = found
            edges[f"{la}/{lb}"] = round(same, 3)
            if same < EDGE_SAME:
                continue
            seen = f"цвет разный ({changed:.1%} пикселей), а края там же ({same:.0%})"
            if axis and axis != FORM_AXIS:
                lines.append(("заметка", f"{la} и {lb} ({a} и {b}) — варианты одной формы, разного тона: {seen} — "
                                         f"по оси «{axis}» допустимо"))
            elif same < EDGE_SURE:
                lines.append(("предупреждение", f"на грани: {la} и {lb} ({a} и {b}) — {seen} — не знаю, та же ли "
                                                "форма: смотреть глазом"))
            else:
                lines.append(("брак" if axis == FORM_AXIS else "предупреждение",
                              f"варианты различаются только оттенком: {la} и {lb} ({a} и {b}) — {seen} — та же "
                              "форма, подкрашенная по-разному; развести приёмами, а не числами"))
    return lines, edges


def aspect_lines(entries, ref_crops):
    """Пропорции REF и варианта расходятся больше чем вдвое — вырезка REF или образец того же ракурса.

    Есть `--ref-crop` — образец заведомо другого ракурса, целые кадры не сверяются, только вырезки.
    """
    lines = []
    refs = [e for e in entries if e["role"] == "ref"]
    for ref in refs:
        for entry in entries:
            if entry["role"] == "ref":
                continue
            ra = ref["image"].width / ref["image"].height
            va = entry["image"].width / entry["image"].height
            if not ref_crops and max(ra, va) / min(ra, va) > ASPECT_GAP:
                lines.append(f"пропорции {ref['label']} ({ref['image'].width}×{ref['image'].height}) и {entry['label']} "
                             f"({entry['image'].width}×{entry['image'].height}) расходятся больше чем вдвое: "
                             "нужна вырезка образца (--ref-crop) или образец того же ракурса")
            for name in entry["boxes"]:
                rb, vb = crop_box(ref["image"], ref["boxes"][name]), crop_box(entry["image"], entry["boxes"][name])
                ra = (rb[2] - rb[0]) / (rb[3] - rb[1])
                va = (vb[2] - vb[0]) / (vb[3] - vb[1])
                if max(ra, va) / min(ra, va) > ASPECT_GAP:
                    lines.append(f"вырезка «{name}»: пропорции у {ref['label']} и {entry['label']} расходятся "
                                 "больше чем вдвое — поправить --ref-crop")
    return lines


def parse_prev(items):
    pairs = []
    for item in items:
        frame, sep, prev = item.rpartition("=")
        if not sep or not frame or not prev:
            fail(2, f"неверные аргументы: --prev «{item}» — нужно <кадр.png>=<снимок прошлого круга.png>")
        pairs.append((frame, prev))
    return pairs


def sanity(frames, variants, prevs, axis):
    """--sanity: грубый брак кадров. Код 0 — «кадры в порядке», 1 — брак."""
    paths = list(dict.fromkeys(frames + [path for _, path in variants] + [frame for frame, _ in prevs]))
    if not paths:
        fail(2, "неверные аргументы: нет кадров — --sanity <png>… , --var A=<png>… или --prev <кадр>=<прошлый>")
    small = {path: sanity_small(path) for path in paths}
    flats = [(path, flat_problem(small[path])) for path in paths]
    found = [f"брак: {path} — {problem}" for path, problem in flats if problem]
    for i, (la, a) in enumerate(variants):
        for lb, b in variants[i + 1:]:
            share = changed_share(small[a], small[b])
            if share is not None and share < SAME_SHARE:
                found.append(f"брак: {a} и {b} — варианты {la} и {lb} почти одинаковы, глазом не отличить "
                             f"(разных пикселей {share:.3%}); развести сильнее или снять ракурс, где видна разница")
    warnings, notes = [], []
    for kind, line in hue_only_lines(variants, small, axis)[0]:
        {"брак": found, "предупреждение": warnings, "заметка": notes}[kind].append(f"{kind}: {line}")
    for frame, prev in prevs:
        share = changed_share(small[frame], sanity_small(prev))
        if share is not None and share < SAME_SHARE:
            found.append(f"брак: {frame} — почти как снимок прошлого круга {prev}: правка не дошла до кадра "
                         f"(разных пикселей {share:.3%}); пересобрать и переснять")
    for line in found + warnings + notes:
        print(line)
    print(f"брак кадров: {len(found)} — владельцу не показывать, сначала починить" if found
          else f"кадры в порядке ({len(paths)})" + (f"; предупреждений {len(warnings)}" if warnings else "")
          + (f"; заметок {len(notes)}" if notes else ""))
    return 1 if found else 0


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = Args(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", action="append", nargs="+", default=[], metavar="PNG",
                        help="образец; можно несколько, первый — точка отсчёта")
    parser.add_argument("--var", action="append", nargs="+", default=[], metavar="ИМЯ=PNG",
                        help="вариант: A=<png>; можно несколько")
    parser.add_argument("--crop", action="append", nargs="+", default=[], metavar="ИМЯ=x,y,w,h",
                        help="вырезка, доли кадра 0..1; можно несколько")
    parser.add_argument("--ref-crop", action="append", nargs="+", default=[], metavar="ИМЯ=x,y,w,h",
                        help="своя вырезка у REF под именем из --crop: образец другого ракурса")
    parser.add_argument("--time", help="час суток кадров из паспорта («вечер», «полдень», «18:30») — в шапку и JSON")
    parser.add_argument("--axis", help="ось выбора: форма | цвет | приём; при «форма» варианты только оттенком — код 1, "
                                       "без оси — предупреждение, при другой оси — заметка")
    parser.add_argument("--out", help="куда сохранить лист (png)")
    parser.add_argument("--json", help="куда сохранить числа (json)")
    parser.add_argument("--only-ref", action="store_true", help="лист и числа только образцов")
    parser.add_argument("--sanity", action="append", nargs="*", default=[], metavar="PNG",
                        help="без листа: грубый брак кадров; варианты сравниваются между собой, только если --var")
    parser.add_argument("--prev", action="append", nargs="+", default=[], metavar="КАДР=ПРОШЛЫЙ",
                        help="с --sanity: <кадр.png>=<снимок прошлого круга.png>; можно несколько")
    args = parser.parse_args()

    if Image is None:
        fail(2, "нужна Pillow: python -m pip install pillow")
    axis = (args.axis or "").strip().lower()
    if args.sanity or args.prev:
        if args.ref or args.crop or args.ref_crop or args.out or args.json or args.only_ref or args.time or not args.sanity:
            fail(2, "неверные аргументы: --sanity [<png>…] [--var A=<png>…] [--prev <кадр>=<прошлый>…] [--axis <ось>] — без листа")
        return sanity(flat(args.sanity), parse_vars(flat(args.var)), parse_prev(flat(args.prev)), axis)

    refs = flat(args.ref)
    variants = parse_vars(flat(args.var))
    crops = parse_crops(flat(args.crop))
    ref_crops = dict(parse_crops(flat(args.ref_crop), "--ref-crop"))
    unknown = [name for name in ref_crops if name not in dict(crops)]
    if unknown:
        fail(2, f"неверные аргументы: --ref-crop «{unknown[0]}» — такой вырезки нет среди --crop (имена те же)")
    if not refs:
        fail(2, "неверные аргументы: нужен хотя бы один --ref <образец>")
    if args.only_ref and variants:
        print("варианты не показаны: задан --only-ref", file=sys.stderr)
        variants = []
    if not args.only_ref and not variants:
        fail(2, "неверные аргументы: нет вариантов — --var A=<png> …; только образцы — --only-ref")
    if not args.out and not (args.only_ref and args.json):
        fail(2, "неверные аргументы: нужен --out <лист.png> (без листа — только --only-ref --json)")
    if len(refs) + len(variants) > MAX_IMAGES:
        fail(2, f"неверные аргументы: на листе не больше {MAX_IMAGES} картинок — разделите на два листа")
    if len(crops) > MAX_CROPS:
        fail(2, f"неверные аргументы: не больше {MAX_CROPS} вырезок на листе")

    entries = []
    for n, path in enumerate(refs, 1):
        entries.append({"label": "REF" if len(refs) == 1 else f"REF {n}", "role": "ref", "file": path})
    for label, path in variants:
        entries.append({"label": label, "role": "variant", "file": path})
    for entry in entries:
        entry["image"] = load(entry["file"])
        entry["boxes"] = {name: (ref_crops.get(name, box) if entry["role"] == "ref" else box) for name, box in crops}
    for entry in entries:
        image = entry["image"]
        entry["stats"] = stats(image)
        entry["crops"] = {name: stats(image.crop(crop_box(image, entry["boxes"][name]))) for name, _ in crops}
    base = entries[0]
    for entry in entries[1:]:
        entry["diff"] = diff(entry["stats"], base["stats"])
        entry["diff"]["vs"] = base["label"]
        entry["diff"]["crops"] = {name: diff(entry["crops"][name], base["crops"][name]) for name, _ in crops}
    warnings = aspect_lines(entries, ref_crops)
    small = {e["file"]: shrink(e["image"]) for e in entries if e["role"] == "variant"}
    hue_lines, edge_same = hue_only_lines(variants, small, axis)
    defects = [line for kind, line in hue_lines if kind == "брак"]
    warnings += [line for kind, line in hue_lines if kind == "предупреждение"]
    notes = [line for kind, line in hue_lines if kind == "заметка"]

    head = " · ".join(part for part in ((f"час суток: {args.time}" if args.time else ""),
                                        (f"ось выбора: {args.axis}" if args.axis else "")) if part)
    sheet_size = None
    if args.out:
        sheet = build_sheet(entries, crops, head)
        folder = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(folder, exist_ok=True)
        try:
            sheet.save(args.out)
        except (OSError, ValueError) as error:
            fail(2, f"не сохранить лист {args.out}: {error}")
        sheet_size = list(sheet.size)

    report = {
        "note": NOTE,
        "sheet": args.out,
        "sheet_size": sheet_size,
        "time": args.time,
        "axis": args.axis,
        "stats_side_px": STATS_SIDE,
        "hue_bins_deg": [i * 30 for i in range(12)],
        "hue_bin_names": list(HUE_NAMES),
        "crops": [{"name": name, "box": list(box)} for name, box in crops],
        "ref_crops": {name: list(box) for name, box in ref_crops.items()},
        "legend": LEGEND,
        "images": [dict({"label": e["label"], "role": e["role"], "file": e["file"],
                         "size": list(e["image"].size)}, **e["stats"], crops=e["crops"])
                   for e in entries],
        "diff_from_ref": {e["label"]: e["diff"] for e in entries[1:]},
        "edge_same": edge_same,
        "defects": defects,
        "warnings": warnings,
        "notes": notes,
    }
    if args.json:
        folder = os.path.dirname(os.path.abspath(args.json))
        os.makedirs(folder, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)

    if args.out:
        print(f"лист: {args.out} ({sheet_size[0]}×{sheet_size[1]})" + (f" · {head}" if head else ""))
    if args.json:
        print(f"числа: {args.json}")
    for e in entries:
        st = e["stats"]
        line = (f"{e['label']:<6} яркость {st['brightness']:.2f}  контраст {st['contrast']:.2f}  "
                f"насыщенность {st['saturation']:.2f}  палитра {' '.join(c['hex'] for c in st['palette'][:3])}")
        if "diff" in e:
            d = e["diff"]
            line += (f"  | от {d['vs']}: яркость {signed(d['brightness'])}, контраст {signed(d['contrast'])}, "
                     f"насыщенность {signed(d['saturation'])}, тон {d['hue_hist']:.2f}")
        print(line)
    for kind, lines in zip(KINDS, (defects, warnings, notes)):
        for line in lines:
            print(f"{kind}: {line}")
    print(NOTE)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
