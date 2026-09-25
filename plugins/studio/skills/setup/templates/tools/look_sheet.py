#!/usr/bin/env python3
"""Лист сравнения: образец рядом с вариантами, чтобы выбирать глазом, а не словами.

Развёрнут плагином studio (/setup). Нужна Pillow, numpy не нужна:
python -m pip install pillow

    python tools/look_sheet.py --ref docs/refs/деревья/ref-valheim-1.png \
        --var A=shots/a.png --var B=shots/b.png --crop листва=0.40,0.20,0.25,0.25 \
        --out docs/refs/деревья/sheet-2026-09-25.png --json docs/refs/деревья/sheet-2026-09-25.json
    python tools/look_sheet.py --only-ref --ref a.png --ref b.png --out sheet.png --json refs.json

Лист: ряд 1 — REF | A | B … одной высоты с крупными подписями (рамки — где
вырезки); ряд 2 — оттенки серого; ряд 3 — размытие («прищур»); ряд 4 —
вырезки в исходном разрешении (x,y,w,h — доли кадра 0..1); палитра из 6 цветов
с HEX и числа. Длинная сторона листа ≤ 2576 px. `--only-ref` — только образцы
(лист можно не строить: хватит `--json`).

JSON: у каждой картинки и вырезки — средняя яркость, контраст (ст. откл.
яркости), средняя насыщенность, гистограмма тона (12 корзин по 30°, только
цветные пиксели), палитра; отличия каждой картинки от первого REF. Числа —
грубая ориентировка по свету и цвету, не мера похожести: решает владелец
выбором по листу.

Коды возврата: 0 — готово; 2 — нет Pillow, нет файла или неверные аргументы.
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
}

BG = (38, 38, 38)
TEXT = (236, 236, 236)
DIM = (160, 160, 160)
REF_COLOR = (255, 200, 87)
VAR_COLOR = (130, 200, 255)
CROP_COLORS = ((255, 64, 160), (0, 220, 255), (170, 255, 60), (255, 150, 0))

M, G = 24, 16  # поле листа и промежуток между столбцами
LBL, SUB, RH, CAP, PS, SL, FOOT = 58, 28, 32, 26, 26, 23, 34
MIN_CW = 250  # столбец не уже: иначе не влезут числа
STAT_LINES = 5


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


def parse_crops(items):
    crops = []
    for n, item in enumerate(items, 1):
        name, _, box = item.rpartition("=")
        name = name.strip() or f"вырезка {n}"
        try:
            x, y, w, h = (float(v) for v in box.split(","))
        except ValueError:
            fail(2, f"неверные аргументы: вырезка «{item}» — нужно <имя>=x,y,w,h, доли 0..1")
        eps = 1e-6
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1
                and x + w <= 1 + eps and y + h <= 1 + eps):
            fail(2, f"неверные аргументы: вырезка «{item}» выходит за кадр — доли 0..1, x+w и y+h ≤ 1")
        crops.append((name, (x, y, w, h)))
    if len({name for name, _ in crops}) != len(crops):
        fail(2, "неверные аргументы: имена вырезок повторяются")
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


def layout(images, crops, height, crop_max):
    """Размеры листа при высоте ряда `height` и пределе высоты вырезки `crop_max`."""
    cw = max(MIN_CW, max(round(height * im.width / im.height) for im in images))
    rows = []
    for _, box in crops:
        tiles = []
        for im in images:
            left, top, right, bottom = crop_box(im, box)
            pw, ph = right - left, bottom - top
            scale = min(1.0, cw / pw, crop_max / ph)
            tiles.append(((left, top, right, bottom), scale, max(1, round(pw * scale)), max(1, round(ph * scale))))
        rows.append(tiles)
    width = 2 * M + len(images) * cw + (len(images) - 1) * G
    total = (M + LBL + SUB + height + 2 * (RH + height)
             + sum(RH + max(t[3] for t in tiles) + CAP for tiles in rows)
             + RH + 6 * PS + STAT_LINES * SL + FOOT + M)
    return cw, rows, width, total


def build_sheet(entries, crops):
    images = [entry["image"] for entry in entries]
    count = len(images)
    avail = LIMIT - 2 * M - (count - 1) * G
    height = min(720, max(im.height for im in images))
    height = max(80, min(height, int(avail / count / max(im.width / im.height for im in images))))
    crop_max = 640
    cw, rows, width, total = layout(images, crops, height, crop_max)
    # не влезает: сначала ужать вырезки, потом главные ряды, потом вырезки ещё
    for shrink_height, floor in ((False, 240), (True, 160), (False, 80)):
        while total > LIMIT and (height if shrink_height else crop_max) > floor:
            if shrink_height:
                height = int(height * 0.92)
            else:
                crop_max = int(crop_max * 0.85)
            cw, rows, width, total = layout(images, crops, height, crop_max)

    sheet = Image.new("RGB", (width, total), BG)
    draw = ImageDraw.Draw(sheet)
    big, small, bold_small = font(44, True), font(17), font(18, True)
    xs = [M + i * (cw + G) for i in range(count)]
    y = M

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
        for k, (_, box) in enumerate(crops):
            bx, by, bw, bh = box
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
        head = f"вырезка {k + 1} «{name}» — x,y,w,h {','.join(f'{v:g}' for v in box)}, исходное разрешение"
        draw.text((M + 22, y + 6), fit(draw, head, bold_small, width - 2 * M - 22), font=bold_small, fill=TEXT)
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
    parser.add_argument("--out", help="куда сохранить лист (png)")
    parser.add_argument("--json", help="куда сохранить числа (json)")
    parser.add_argument("--only-ref", action="store_true", help="лист и числа только образцов")
    args = parser.parse_args()

    if Image is None:
        fail(2, "нужна Pillow: python -m pip install pillow")

    refs = flat(args.ref)
    variants = parse_vars(flat(args.var))
    crops = parse_crops(flat(args.crop))
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
    for entry in entries:
        image = entry["image"]
        entry["stats"] = stats(image)
        entry["crops"] = {name: stats(image.crop(crop_box(image, box))) for name, box in crops}
    base = entries[0]
    for entry in entries[1:]:
        entry["diff"] = diff(entry["stats"], base["stats"])
        entry["diff"]["vs"] = base["label"]
        entry["diff"]["crops"] = {name: diff(entry["crops"][name], base["crops"][name]) for name, _ in crops}

    sheet_size = None
    if args.out:
        sheet = build_sheet(entries, crops)
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
        "stats_side_px": STATS_SIDE,
        "hue_bins_deg": [i * 30 for i in range(12)],
        "hue_bin_names": list(HUE_NAMES),
        "crops": [{"name": name, "box": list(box)} for name, box in crops],
        "legend": LEGEND,
        "images": [dict({"label": e["label"], "role": e["role"], "file": e["file"],
                         "size": list(e["image"].size)}, **e["stats"], crops=e["crops"])
                   for e in entries],
        "diff_from_ref": {e["label"]: e["diff"] for e in entries[1:]},
    }
    if args.json:
        folder = os.path.dirname(os.path.abspath(args.json))
        os.makedirs(folder, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)

    if args.out:
        print(f"лист: {args.out} ({sheet_size[0]}×{sheet_size[1]})")
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
    print(NOTE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
