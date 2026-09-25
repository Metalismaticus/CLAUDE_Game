#!/usr/bin/env python3
"""Проверить пришедший файл заказа до осмотра глазами: картинку или звук.

Развёрнут плагином studio (/setup). Зовут /add и агент assets — до осмотра
глазами; order_api.py — после заказа с прозрачным фоном. Картинкам нужна
Pillow (python -m pip install pillow); WAV читается стандартной библиотекой
(wave), у OGG — только заголовок.

    python tools/asset_check.py assets/ui/icon.png --canvas 512x512 --alpha required
    python tools/asset_check.py assets/tiles/grass.png --alpha none --pixel-art --json check.json
    python tools/asset_check.py assets/audio/sfx/jump_01.wav

Картинка: холст против заказа (--canvas WxH); альфа и доля прозрачного;
прозрачные ли углы; нарисованная «шахматка» (два серых тона квадратами от 8 px
до 1/16 холста там, где должна быть прозрачность); рамка содержимого и касание
края — «фон не вырезан» и «объект упёрся в край» — разные сообщения;
--pixel-art — число цветов и шаг сетки. --alpha: required — нужен прозрачный
фон, none — сплошной, any (по умолчанию) — всё равно (шахматка — заметка).
Звук: длительность, каналы, частота, пик и клиппинг, тишина в начале. OGG —
только длительность, каналы и частота; пик и тишину — на слух.

Вывод: JSON (на экран одной строкой или в --json <file>) и последней строкой
итог: «годен: …»; «поправить: …» — поправимое без перезаказа (холст больше
заказанного в тех же пропорциях, тишина в начале, цвета пиксель-арта от
генератора): поправить копию и проверить снова; «брак: …» (всё найденное
через «;»); «не умею: …».
Коды возврата: 0 — годен или поправить; 1 — брак; 2 — не умею: формат, нет
Pillow, нет файла, неверные аргументы.
"""
import argparse
import array
import json
import math
import os
import re
import sys
import wave

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = None
else:
    BOX = getattr(Image, "Resampling", Image).BOX

TRANSPARENT = 16       # альфа ниже — прозрачный пиксель
OPAQUE = 240           # альфа не ниже — непрозрачный
EDGE_ALPHA = 64        # альфа содержимого, которое «касается края»
SEE_THROUGH = 0.005    # доля прозрачного, с которой фон считается вырезанным
BG_TOL = 24            # разброс сплошного фона по каналу
CONTENT_TOL = 40       # отличие содержимого от фона по каналу
BORDER_SOLID = 0.6     # доля рамки одного цвета — фон сплошной
FILLS_EDGE = 0.8       # край занят на столько — картинка до краёв (плитка, кнопка)
CHECKER_PX = (6, 34)   # сторона квадрата «шахматки»: 8–32 px с допуском; верх растёт с холстом
PIXEL_MAX_COLORS = 256
GRID_FIT = 0.9         # доля смен цвета на линиях сетки, чтобы назвать шаг
SILENCE_DB = -50.0
LEAD_MAX_MS = 50
CLIP_LEVEL = 0.999
CLIP_RUN = 3           # подряд на максимуме — клиппинг, а не одиночный пик
CLIP_CAP = 100
TAIL_DB = -30.0
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tga", ".tif", ".tiff",
             ".psd", ".dds", ".ico", ".avif", ".svg", ".exr", ".hdr")
SIDES = ("сверху", "справа", "снизу", "слева")
CORNERS = ("слева сверху", "справа сверху", "справа снизу", "слева снизу")
NO_PILLOW = "нужна Pillow: python -m pip install pillow"


class Cannot(Exception):
    """Проверить не умею — код 2."""


class Args(argparse.ArgumentParser):
    def error(self, message):
        raise Cannot(f"неверные аргументы: {message}")


def pixels(image):
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return list(getter())


def pct(share):
    return f"{share * 100:.1f}".rstrip("0").rstrip(".") + "%"


def db(value, full):
    return round(20 * math.log10(value / full), 1) if value > 0 else None


def hexcolor(rgb):
    return "#" + "".join(f"{int(round(c)):02x}" for c in rgb[:3])


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def sniff(path):
    with open(path, "rb") as handle:
        head = handle.read(16)
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[:4] == b"fLaC":
        return "flac"
    if head[4:8] == b"ftyp":
        return "m4a/mp4"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    return None


# ---------- картинки ----------

def max_diff(rgb, color):
    """Наибольшее по каналам отличие каждого пикселя от цвета — картинка L."""
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, tuple(int(c) for c in color)))
    red, green, blue = diff.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def threshold(gray, level):
    return gray.point(lambda v: 255 if v > level else 0)


def edge_cover(mask):
    width, height = mask.size
    strips = {"сверху": (0, 0, width, 1), "справа": (width - 1, 0, width, height),
              "снизу": (0, height - 1, width, height), "слева": (0, 0, 1, height)}
    cover = {}
    for side, box in strips.items():
        hist = mask.crop(box).histogram()
        count = sum(hist[1:])
        cover[side] = (count, count / max(1, sum(hist)))
    return cover


def border_color(rgb):
    width, height = rgb.size
    edge = []
    for box in ((0, 0, width, 1), (0, height - 1, width, height), (0, 0, 1, height),
                (width - 1, 0, width, height)):
        edge += pixels(rgb.crop(box))
    color = tuple(median([p[i] for p in edge]) for i in range(3))
    near = sum(1 for p in edge if max(abs(p[i] - color[i]) for i in range(3)) <= BG_TOL)
    return color if near >= BORDER_SOLID * len(edge) else None


def interior_runs(seq):
    """Длины серий 0/1, у которых обе соседние серии — другой серый тон."""
    runs, start = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[start]:
            runs.append((seq[start], i - start))
            start = i
    return [length for k, (value, length) in enumerate(runs[1:-1], 1)
            if value >= 0 and runs[k - 1][0] >= 0 and runs[k + 1][0] >= 0]


def checker_patch(rgb, largest):
    """Нарисованная «шахматка» в куске: два серых тона квадратами одного размера."""
    width, height = rgb.size
    lum = [-1 if max(p) - min(p) > 16 else sum(p) // 3 for p in pixels(rgb)]
    grays = sorted(v for v in lum if v >= 0)
    if len(grays) < 0.75 * len(lum):
        return None
    low, high = grays[len(grays) // 20], grays[len(grays) * 19 // 20]
    if high - low < 8:
        return None
    if sum(1 for v in grays if v - low <= 12 or high - v <= 12) < 0.85 * len(grays):
        return None
    middle = (low + high) / 2
    classes = [-1 if v < 0 else int(v > middle) for v in lum]
    rows = [r for y in range(height) for r in interior_runs(classes[y * width:(y + 1) * width])]
    cols = [r for x in range(width) for r in interior_runs(classes[x::width])]
    if len(rows) < 4 or len(cols) < 4:
        return None
    size = median(rows + cols)
    if not CHECKER_PX[0] <= size <= largest:
        return None
    if abs(median(rows) - size) > 2 or abs(median(cols) - size) > 2:
        return None
    runs = rows + cols
    if sum(1 for r in runs if abs(r - size) <= 2) < 0.7 * len(runs):
        return None
    tones = []
    for cls in (0, 1):
        chosen = [p for p, c in zip(pixels(rgb), classes) if c == cls]
        tones.append(hexcolor([sum(p[i] for p in chosen) / len(chosen) for i in range(3)]))
    return {"square_px": size, "tones": tones}


def find_checker(rgba, alpha):
    width, height = rgba.size
    if min(width, height) < 48:
        return None
    largest = max(CHECKER_PX[1], min(width, height) // 16 + 2)
    side = max(24, min(max(160, 3 * largest), min(width, height) // 3))
    boxes = ((0, 0, side, side), (width - side, 0, width, side),
             (width - side, height - side, width, height), (0, height - side, side, height))
    found = []
    for name, box in zip(CORNERS, boxes):
        if sum(alpha.crop(box).histogram()[OPAQUE:]) < 0.9 * side * side:
            continue  # угол прозрачный: там рисовать нечего
        hit = checker_patch(rgba.crop(box).convert("RGB"), largest)
        if hit:
            found.append((name, hit))
    if not found:
        return None
    return {"corners": [name for name, _ in found],
            "square_px": median([hit["square_px"] for _, hit in found]),
            "tones": found[0][1]["tones"]}


def change_profile(flat, axis):
    """Сколько смен цвета на каждой границе столбцов (x) или строк (y)."""
    width, height = flat.size
    if axis == "x":
        a, b, size = flat.crop((1, 0, width, height)), flat.crop((0, 0, width - 1, height)), (width - 1, 1)
        scale = height
    else:
        a, b, size = flat.crop((0, 1, width, height)), flat.crop((0, 0, width, height - 1)), (1, height - 1)
        scale = width
    bands = ImageChops.difference(a, b).split()
    gray = bands[0]
    for band in bands[1:]:
        gray = ImageChops.lighter(gray, band)
    mask = threshold(gray, 0).convert("F")
    return [v * scale / 255 for v in pixels(mask.resize(size, BOX))]


def grid_step(flat):
    width, height = flat.size
    prof_x, prof_y = change_profile(flat, "x"), change_profile(flat, "y")
    total = sum(prof_x) + sum(prof_y)
    lines = sum(1 for v in prof_x + prof_y if v > 0.5)
    limit = min(64, min(width, height) // 4)
    if total < 1 or lines < 4 or limit < 2:
        return None, 0.0
    fits = {}
    for k in range(2, limit + 1):
        bx, by = [0.0] * k, [0.0] * k
        for i, v in enumerate(prof_x, 1):
            bx[i % k] += v
        for i, v in enumerate(prof_y, 1):
            by[i % k] += v
        fits[k] = (max(bx) + max(by)) / total
    step = max((k for k, fit in fits.items() if fit >= GRID_FIT), default=1)
    best = max((fit for k, fit in fits.items() if k >= 4), default=0.0)
    return step, (fits[step] if step > 1 else best)


def pixel_art(rgba, info, fixes, notes):
    width, height = rgba.size
    colors = rgba.getcolors(maxcolors=1 << 18)
    if colors is None:
        count, semi = None, None
    else:
        count = len({c[:3] for _, c in colors if c[3] >= 128})
        semi = sum(n for n, c in colors if TRANSPARENT <= c[3] < OPAQUE) / (width * height)
    solid = rgba.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    flat = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    flat.paste(rgba.convert("RGB"), mask=solid)
    step, fit = grid_step(flat)
    info["pixel_art"] = {"colors": count if count is not None else f">{1 << 18}",
                         "grid_step": step, "grid_fit": round(fit, 3),
                         "semi_transparent": round(semi, 4) if semi is not None else None}
    if count is None or count > PIXEL_MAX_COLORS:
        shown = count if count is not None else f"больше {1 << 18}"
        fixes.append(f"цветов {shown} — пиксель-арт от генератора: уменьшить до размера в игре "
                     "и свести к палитре паспорта, затем проверить снова без --canvas")
    if semi and semi > 0.01:
        notes.append(f"полупрозрачных пикселей {pct(semi)} — у пиксель-арта края обычно резкие")
    if step is None:
        notes.append("шаг сетки не определить: слишком мало смен цвета")
    elif step == 1 and fit >= 0.5:
        notes.append(f"сетка неровная: крупные пиксели разного размера (по сетке {pct(fit)} границ)")


def check_image(image, args, canvas, report):
    problems, notes, fixes = report["problems"], report["notes"], report["fixes"]
    width, height = image.size
    frames = getattr(image, "n_frames", 1) or 1
    info = {"width": width, "height": height, "mode": image.mode}
    report["image"] = info
    if frames > 1:
        info["frames"] = frames
        notes.append(f"анимация: {frames} кадров — проверен первый")
    try:
        rgba = image.convert("RGBA")
    except (ValueError, OSError) as error:
        raise Cannot(f"картинка в режиме {image.mode} ({error})")
    total = width * height

    if canvas:
        info["canvas_ordered"] = f"{canvas[0]}x{canvas[1]}"
        if (width, height) != canvas:
            text = f"холст {width}×{height}, а заказан {canvas[0]}×{canvas[1]}"
            if abs(width / height - canvas[0] / canvas[1]) < 0.01 and width > canvas[0]:
                fixes.append(f"{text}: пропорции те же — уменьшить до {canvas[0]}×{canvas[1]}")
            else:
                problems.append(f"{text} — перезаказать с нужным холстом")

    alpha = rgba.getchannel("A")
    hist = alpha.histogram()
    transparent = sum(hist[:TRANSPARENT]) / total
    semi = sum(hist[TRANSPARENT:OPAQUE]) / total
    side = max(2, min(width, height) // 64)
    corner_boxes = ((0, 0, side, side), (width - side, 0, width, side),
                    (width - side, height - side, width, height), (0, height - side, side, height))
    corners = [sum(alpha.crop(b).histogram()[:TRANSPARENT]) >= 0.9 * side * side for b in corner_boxes]
    solid = threshold(alpha, EDGE_ALPHA - 1)
    cover = edge_cover(solid)
    border_opaque = all(share >= 0.98 for _, share in cover.values()) and not any(corners)
    see_through = (transparent >= SEE_THROUGH or any(corners)) and not border_opaque
    info["alpha"] = {"channel": "A" in image.getbands() or "transparency" in image.info,
                     "transparent": round(transparent, 4), "semi": round(semi, 4),
                     "corners_transparent": dict(zip(CORNERS, corners))}

    checker = find_checker(rgba, alpha)
    if checker:
        info["checkerboard"] = checker
        where = "во всех углах" if len(checker["corners"]) == 4 else ", ".join(checker["corners"])
        text = (f"нарисована «шахматка» вместо прозрачности (квадраты ~{checker['square_px']} px; "
                f"{where}) — перезаказать: настоящий прозрачный фон или сплошной фон одного цвета")
        if args.alpha == "required":
            problems.append(text)
        else:
            notes.append(f"серая «шахматка» ({where}) — если это не рисунок, прозрачность нарисована")

    mask = edge_mask = None
    if see_through:
        info["background"] = "прозрачный"
        if args.alpha == "none":
            problems.append(f"есть прозрачность ({pct(transparent)}), а заказан сплошной фон — "
                            "перезаказать со сплошным фоном")
        mask, edge_mask = threshold(alpha, TRANSPARENT - 1), solid
    else:
        rgb = rgba.convert("RGB")
        if checker:
            tones = [tuple(int(t[i:i + 2], 16) for i in (1, 3, 5)) for t in checker["tones"]]
            info["background"] = "нарисованная шахматка"
            mask = threshold(ImageChops.darker(max_diff(rgb, tones[0]), max_diff(rgb, tones[1])), CONTENT_TOL)
        else:
            color = border_color(rgb)
            if color:
                info["background"] = f"сплошной {hexcolor(color)}"
                mask = threshold(max_diff(rgb, color), CONTENT_TOL)
            else:
                info["background"] = "картинка до краёв"
            if args.alpha == "required":
                problems.append("фон не вырезан — перезаказать с другим фоном")
        edge_mask = mask

    if mask is not None:
        box = mask.getbbox()
        if box is None and see_through:
            problems.append("картинка пустая: всё прозрачное")
        elif box is None:
            notes.append("картинка почти одного цвета: от цвета рамки ничего не отличается")
        else:
            left, top, right, bottom = box
            info["content_box"] = list(box)
            info["margins"] = {"сверху": top, "справа": width - right, "снизу": height - bottom, "слева": left}
            info["content_share"] = round((right - left) * (bottom - top) / total, 3)
            cover = edge_cover(edge_mask)
            touched = [s for s in SIDES if cover[s][0] >= 2]
            info["touches"] = touched
            if len(touched) == 4 and all(cover[s][1] >= FILLS_EDGE for s in SIDES):
                notes.append("содержимое до всех краёв — у плитки, кнопки, фона это норма; "
                             "у отдельного предмета — «упёрся в край»")
            elif touched:
                problems.append(f"объект упёрся в край ({', '.join(touched)}) — перезаказать: объект "
                                "целиком, с полями от краёв (или больший холст)")

    if args.pixel_art:
        pixel_art(rgba, info, fixes, notes)

    brief = f"{report['format']} {width}×{height}"
    if see_through:
        brief += f", прозрачного {pct(transparent)}"
    else:
        brief += f", фон: {info['background']}"
    art = info.get("pixel_art")
    if art:
        brief += f", цветов {art['colors']}, шаг сетки {art['grid_step'] or '?'}"
    return brief


# ---------- звук ----------

def read_riff(path, reason):
    """WAV, который не читает wave: плавающая точка и WAVE_FORMAT_EXTENSIBLE."""
    with open(path, "rb") as handle:
        blob = handle.read()
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(blob):
        name, size = blob[pos:pos + 4], int.from_bytes(blob[pos + 4:pos + 8], "little")
        body = blob[pos + 8:pos + 8 + size]
        if name == b"fmt ":
            fmt = body
        elif name == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if not fmt or data is None or len(fmt) < 16:
        raise Cannot(f"WAV без понятного заголовка ({reason})")
    code = int.from_bytes(fmt[0:2], "little")
    channels = int.from_bytes(fmt[2:4], "little")
    rate = int.from_bytes(fmt[4:8], "little")
    bits = int.from_bytes(fmt[14:16], "little")
    if code == 0xFFFE and len(fmt) >= 26:
        code = int.from_bytes(fmt[24:26], "little")
    if code == 3 and bits in (32, 64):
        return channels, bits // 8, rate, data, "float"
    if code == 1 and bits in (8, 16, 24, 32):
        return channels, bits // 8, rate, data, "int"
    raise Cannot(f"WAV в кодировке {code}, {bits} бит: перевести в PCM 16 бит или проверить на слух")


def to_samples(data, width, kind):
    usable = len(data) - len(data) % width
    data = data[:usable]
    if kind == "float":
        samples = array.array("f" if width == 4 else "d")
        samples.frombytes(data)
        full = 1.0
    elif width == 1:
        samples = array.array("b")
        samples.frombytes(data.translate(bytes((i - 128) & 0xFF for i in range(256))))
        full = 128
    elif width == 2:
        samples = array.array("h")
        samples.frombytes(data)
        full = 32768
    elif width == 3:
        wide = bytearray(len(data) // 3 * 4)
        wide[1::4], wide[2::4], wide[3::4] = data[0::3], data[1::3], data[2::3]
        samples = array.array("i")
        samples.frombytes(bytes(wide))
        full = 2 ** 31
    else:
        samples = array.array("i")
        samples.frombytes(data)
        full = 2 ** 31
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, full


def first_loud(samples, level):
    for start in range(0, len(samples), 8192):
        chunk = samples[start:start + 8192]
        if max(chunk) > level or -min(chunk) > level:
            for offset, value in enumerate(chunk):
                if value > level or -value > level:
                    return start + offset
    return None


def clip_runs(samples, channels, level):
    runs = clipped = 0
    for channel in range(channels):
        wave_ = samples[channel::channels]
        run = 0
        for start in range(0, len(wave_), 4096):
            chunk = wave_[start:start + 4096]
            if max(chunk) < level and -min(chunk) < level:
                runs += run >= CLIP_RUN
                run = 0
                continue
            for value in chunk:
                if value >= level or -value >= level:
                    run += 1
                    clipped += 1
                else:
                    runs += run >= CLIP_RUN
                    run = 0
            if runs >= CLIP_CAP:
                return runs, clipped, True
        runs += run >= CLIP_RUN
    return runs, clipped, False


def check_wav(path, report):
    try:
        with wave.open(path, "rb") as handle:
            channels, width = handle.getnchannels(), handle.getsampwidth()
            rate = handle.getframerate()
            data = handle.readframes(handle.getnframes())
        kind = "int"
    except wave.Error as error:
        channels, width, rate, data, kind = read_riff(path, error)
    except EOFError:
        raise Cannot("WAV обрезан — заголовок без данных")
    if not channels or not rate:
        raise Cannot("WAV без каналов или частоты")
    report["format"] = "WAV"
    samples, full = to_samples(data, width, kind)
    frames = len(samples) // channels
    info = {"duration_s": round(frames / rate, 3), "channels": channels, "rate": rate,
            "bits": width * 8, "sample": "плавающая точка" if kind == "float" else "PCM"}
    report["audio"] = info
    problems, notes = report["problems"], report["notes"]
    if not frames:
        problems.append("звук пустой: 0 кадров")
        return f"WAV {channels} кан., {rate} Гц, 0 с"
    peak = max(max(samples), -min(samples))
    info["peak_dbfs"] = db(peak, full)
    lead = first_loud(samples, full * 10 ** (SILENCE_DB / 20))
    if lead is None:
        info["leading_silence_ms"] = round(frames / rate * 1000)
        problems.append(f"звук пустой — одна тишина (тише {SILENCE_DB:g} dBFS)")
    else:
        lead_ms = round(lead // channels / rate * 1000)
        info["leading_silence_ms"] = lead_ms
        if lead_ms > LEAD_MAX_MS:
            report["fixes"].append(f"тишина в начале {lead_ms} мс — обрезать")
    runs, clipped, capped = clip_runs(samples, channels, full * CLIP_LEVEL)
    info["clipped_samples"] = clipped
    info["clip_runs"] = f"{runs}+" if capped else runs
    if runs:
        problems.append(f"клиппинг: {runs}{'+' if capped else ''} мест, где волна срезана на максимуме — "
                        "перезаказать без перегруза")
    elif info["peak_dbfs"] is not None and info["peak_dbfs"] >= -0.1:
        notes.append(f"пик у самого края ({info['peak_dbfs']} dBFS) — запаса нет")
    tail = samples[-max(channels, channels * rate // 100):]
    info["tail_dbfs"] = db(max(max(tail), -min(tail)), full)
    if info["tail_dbfs"] is not None and info["tail_dbfs"] > TAIL_DB:
        notes.append(f"конец обрывается на громкости {info['tail_dbfs']} dBFS — возможен щелчок "
                     "(у трека под повтор это норма)")
    return (f"WAV {channels} кан., {rate} Гц, {info['duration_s']} с, пик {info['peak_dbfs']} dBFS, "
            f"тишина в начале {info['leading_silence_ms']} мс")


def ogg_page(blob, pos):
    """Заголовок страницы Ogg: (гранула, серийный номер, начало тела, длины сегментов)."""
    if blob[pos:pos + 4] != b"OggS" or pos + 27 > len(blob):
        return None
    count = blob[pos + 26]
    lacing = blob[pos + 27:pos + 27 + count]
    return (int.from_bytes(blob[pos + 6:pos + 14], "little", signed=True), blob[pos + 14:pos + 18],
            pos + 27 + count, lacing)


def check_ogg(path, report):
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(65536)
        handle.seek(max(0, size - 131072))
        tail = handle.read()
    first = ogg_page(head, 0)
    if not first:
        raise Cannot("OGG с битым заголовком")
    _, serial, body, lacing = first
    length = 0
    for part in lacing:
        length += part
        if part < 255:
            break
    packet = head[body:body + length]
    if packet[:7] == b"\x01vorbis" and len(packet) >= 16:
        codec, channels = "Vorbis", packet[11]
        rate = int.from_bytes(packet[12:16], "little")
        clock, skip = rate, 0
    elif packet[:8] == b"OpusHead" and len(packet) >= 16:
        codec, channels = "Opus", packet[9]
        skip = int.from_bytes(packet[10:12], "little")
        rate = int.from_bytes(packet[12:16], "little") or 48000
        clock = 48000
    else:
        raise Cannot("OGG не Vorbis и не Opus: проверить на слух")
    report["format"] = f"OGG {codec}"
    granule, pos = None, len(tail)
    while granule is None:
        pos = tail.rfind(b"OggS", 0, pos)
        if pos < 0:
            break
        page = ogg_page(tail, pos)
        if page and page[1] == serial and page[0] >= 0:
            granule = page[0]
    info = {"channels": channels, "rate": rate, "codec": codec,
            "duration_s": round(max(0, granule - skip) / clock, 3) if granule is not None and clock else None}
    report["audio"] = info
    report["not_checked"] += ["пик и клиппинг", "тишина в начале"]
    if not channels or not rate:
        report["problems"].append("в заголовке нет каналов или частоты")
    if info["duration_s"] == 0:
        report["problems"].append("звук пустой: длительность 0")
    duration = f"{info['duration_s']} с" if info["duration_s"] is not None else "длительность не найдена"
    return f"OGG {codec} {channels} кан., {rate} Гц, {duration}"


# ---------- вызов ----------

def parse_canvas(text):
    match = re.fullmatch(r"\s*(\d+)\s*[xXхХ×*]\s*(\d+)\s*", text or "")
    if not match or not int(match.group(1)) or not int(match.group(2)):
        raise Cannot(f"неверные аргументы: --canvas {text!r} — нужно WxH, например 512x512")
    return int(match.group(1)), int(match.group(2))


def run(args, report):
    canvas = parse_canvas(args.canvas) if args.canvas else None
    path = args.file
    if not os.path.exists(path):
        raise Cannot("нет файла")
    if os.path.isdir(path):
        raise Cannot("это папка, а нужен файл")
    kind = sniff(path)
    ext = os.path.splitext(path)[1].lower()
    if kind in ("wav", "ogg"):
        report["kind"] = "audio"
        if canvas or args.alpha != "any" or args.pixel_art:
            report["notes"].append("--canvas, --alpha, --pixel-art — только для картинок, пропущены")
        return check_wav(path, report) if kind == "wav" else check_ogg(path, report)
    if kind:
        report["kind"] = "audio"
        raise Cannot(f"{kind.upper()}: скрипт читает WAV и заголовок OGG; "
                     "перевести в WAV/OGG или проверить на слух")
    report["kind"] = "image"
    if Image is None:
        if ext in IMAGE_EXT or not ext:
            raise Cannot(NO_PILLOW)
        raise Cannot(f"{ext}: не картинка, не WAV и не OGG")
    try:
        image = Image.open(path)
        image.load()
    except Image.DecompressionBombError:
        raise Cannot("картинка слишком большая для проверки")
    except (OSError, ValueError, SyntaxError) as error:
        raise Cannot(f"{ext or 'файл'}: Pillow не открывает ({error.__class__.__name__})")
    report["format"] = image.format or ext.lstrip(".").upper()
    return check_image(image, args, canvas, report)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = Args(description=__doc__.split("\n")[0])
    parser.add_argument("file", help="пришедший файл")
    parser.add_argument("--canvas", help="холст из заказа, WxH")
    parser.add_argument("--alpha", default="any", choices=("required", "none", "any"),
                        help="required — нужен прозрачный фон; none — сплошной; any — всё равно")
    parser.add_argument("--pixel-art", action="store_true", help="число цветов и шаг сетки")
    parser.add_argument("--json", help="записать JSON в файл, на экран — только итог")
    report = {"file": None, "kind": None, "format": None, "code": None, "verdict": None,
              "problems": [], "fixes": [], "notes": [], "not_checked": []}
    json_path = None
    try:
        args = parser.parse_args()
        report["file"], json_path = args.file, args.json
        brief = run(args, report)
        code = 1 if report["problems"] else 0
    except Cannot as reason:
        code, brief = 2, str(reason)
    name = report["file"] or "?"
    if code == 2:
        line = f"{NO_PILLOW} — {name} не проверен" if brief == NO_PILLOW else f"не умею: {name} — {brief}"
    elif code == 1:
        line = f"брак: {name} — " + "; ".join(report["problems"])
    elif report["fixes"]:
        line = f"поправить: {name} — " + "; ".join(report["fixes"]) + f" ({brief})"
    else:
        line = f"годен: {name} — {brief}"
        if report["not_checked"]:
            line += "; скриптом не проверено: " + ", ".join(report["not_checked"]) + " — послушать"
    if code != 2 and report["notes"]:
        line += "; заметки: " + "; ".join(report["notes"])
    report["code"] = code
    report["verdict"] = ("поправить" if report["fixes"] else "годен", "брак", "не умею")[code]
    report["summary"] = line
    text = json.dumps(report, ensure_ascii=False)
    if json_path:
        try:
            folder = os.path.dirname(json_path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            print(text)
            print(f"не умею: JSON не записан в {json_path} ({error})")
            sys.exit(2)
    else:
        print(text)
    print(line)
    sys.exit(code)


if __name__ == "__main__":
    main()
