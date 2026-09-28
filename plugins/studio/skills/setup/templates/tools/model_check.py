#!/usr/bin/env python3
"""Проверить 3D-модель из файла до стенда и приёмки: .glb и .gltf (glTF 2.0).

Развёрнут плагином studio (/setup). Только стандартная библиотека. Зовут /add
и агент assets (заказ `model3d`) — до снимка на стенде; /start — перед
вариантом из файла (`-Model <путь>` стенда, `docs/TESTING.md`, «Стенд»).

    python tools/model_check.py assets/models/spruce_a.glb --budget 2000 --height 8 --texture-max 1024 --json check.json

Читает JSON и бинарный чанк .glb; у .gltf — буферы `data:` и файлы рядом.
Считает по сцене (узлы с трансформами, инстансы мешей): треугольники против
`--budget`; габариты в метрах — ширина × высота × глубина (X × Y × Z; glTF
хранит метры, Y вверх; `--height` — ожидаемая высота ±25 %); опорную точку —
низ по центру: низ ≈ 0, центр X и Z ≈ 0 с допуском 2 % размера, не меньше
2 см; поворот и масштаб в узлах сцены — должны быть применены в самой модели
(узел с поворотом или масштабом ≠ 1 — «вперёд» +Z и метры не те, что в
файле); текстуры внутри — у .glb только в бинарном чанке, у .gltf файлы рядом
на месте; размер PNG/JPEG против `--texture-max`. Само «лицо» модели (смотрит
ли в +Z) скрипт не видит — по снимку на стенде рядом с капсулой 1,8 м.

Вывод: JSON (на экран одной строкой или в --json <file>) и последней строкой
итог: «годен: …»; «брак: …» (всё найденное через «;»); «не умею: …».
Поворот или масштаб в узле — первой строкой брака, габарит, высота и опора
тогда не судятся: они верны только после применения трансформа.
Коды возврата: 0 — годен; 1 — брак; 2 — не умею: не glTF, нет файла, файл
обрезан (длина из заголовка glb или чанка больше файла, буфер короче
byteLength, bufferView геометрии, индексов или картинки за буфером), сжатие
Draco или квантование (`extensionsRequired`, accessor без bufferView —
пересохранить без сжатия), неверные аргументы.
"""
import argparse
import base64
import json
import os
import struct
import sys
from urllib.parse import unquote

CHUNK_JSON, CHUNK_BIN = 0x4E4F534A, 0x004E4942
COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
TRIANGLES, STRIP, FAN = 4, 5, 6
PIVOT_SHARE, PIVOT_MIN = 0.02, 0.02  # допуск опорной точки: 2 % размера, не меньше 2 см
HEIGHT_TOL = 0.25  # высота против заказа ±25 %
METRES = (0.01, 500.0)  # габарит вне — не метры
EPS = 1e-4
IDENTITY = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


class Bad(Exception):
    """Файл не разобрать — код 2."""


def load(path):
    """(документ glTF, буферы байтами) из .glb или .gltf."""
    if not os.path.isfile(path):
        raise Bad(f"нет файла: {path}")
    with open(path, "rb") as handle:
        data = handle.read()
    folder = os.path.dirname(os.path.abspath(path))
    bins = []
    if data[:4] == b"glTF":
        if len(data) < 12:
            raise Bad("файл короче заголовка glb")
        _, version, length = struct.unpack_from("<III", data, 0)
        if version != 2:
            raise Bad(f"glb версии {version}, нужна 2")
        if length > len(data):
            raise Bad(f"файл обрезан: в заголовке {length} байт, на диске {len(data)}")
        offset, doc = 12, None
        while offset + 8 <= length:
            size, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if offset + size > len(data):
                raise Bad(f"файл обрезан: чанк {size} байт с позиции {offset}, на диске {len(data)}")
            chunk = data[offset:offset + size]
            offset += size
            if kind == CHUNK_JSON and doc is None:
                try:
                    doc = json.loads(chunk.decode("utf-8"))
                except ValueError as error:
                    raise Bad(f"JSON-чанк glb испорчен: {error}") from None
            elif kind == CHUNK_BIN:
                bins.append(chunk)
        if doc is None:
            raise Bad("в glb нет JSON-чанка")
    else:
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as error:
            raise Bad(f"не glb и не JSON glTF: {error}") from None
    if not isinstance(doc, dict) or "asset" not in doc:
        raise Bad("нет поля asset — не glTF")
    buffers = []
    for n, buf in enumerate(doc.get("buffers", [])):
        uri = buf.get("uri")
        if uri is None:
            buffers.append(bins[0] if bins and n == 0 else b"")
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            beside = os.path.join(folder, unquote(uri))
            if not os.path.isfile(beside):
                raise Bad(f"нет файла буфера {uri} рядом с моделью")
            with open(beside, "rb") as handle:
                buffers.append(handle.read())
    for n, (buf, blob) in enumerate(zip(doc.get("buffers", []), buffers)):
        if len(blob) < buf.get("byteLength", 0):
            raise Bad(f"буфер {n} короче объявленного: {len(blob)} байт из {buf['byteLength']} — файл обрезан")
    return doc, buffers, folder, bool(data[:4] == b"glTF")


def bytes_of(doc, buffers, view_index):
    """Байты bufferView; конец за буфером — файл обрезан."""
    view = doc["bufferViews"][view_index]
    start, length = view.get("byteOffset", 0), view["byteLength"]
    data = buffers[view["buffer"]]
    if start + length > len(data):
        raise Bad(f"bufferView {view_index} выходит за буфер ({start + length} > {len(data)}): файл обрезан")
    return data[start:start + length]


def accessor(doc, buffers, index, decode=True):
    """Значения accessor'а кортежами (decode=False — только проверка границ, возвращает count);
    разреженный без bufferView — нули; без bufferView и без sparse — сжатие, код 2."""
    acc = doc["accessors"][index]
    width = COUNT[acc["type"]]
    fmt, size = COMPONENT[acc["componentType"]]
    count = acc["count"]
    if "bufferView" not in acc:
        if "sparse" in acc:
            return count if not decode else [(0.0,) * width] * count
        raise Bad(f"accessor {index} без bufferView — сжатие Draco или квантование: пересохранить без сжатия")
    view = doc["bufferViews"][acc["bufferView"]]
    data = bytes_of(doc, buffers, acc["bufferView"])
    start = acc.get("byteOffset", 0)
    stride = view.get("byteStride") or width * size
    if count and start + (count - 1) * stride + width * size > len(data):
        raise Bad(f"accessor {index} выходит за буфер: файл обрезан")
    if not decode:
        return count
    return [struct.unpack_from("<" + fmt * width, data, start + i * stride) for i in range(count)]


def primitive_triangles(doc, prim):
    mode = prim.get("mode", TRIANGLES)
    source = prim["indices"] if "indices" in prim else prim.get("attributes", {}).get("POSITION")
    if source is None:
        return 0
    count = doc["accessors"][source]["count"]
    if mode == TRIANGLES:
        return count // 3
    if mode in (STRIP, FAN):
        return max(0, count - 2)
    return 0  # точки и линии


def node_matrix(node):
    if "matrix" in node:
        m = node["matrix"]  # glTF хранит по столбцам
        return [[float(m[c * 4 + r]) for c in range(4)] for r in range(4)]
    t = node.get("translation", [0, 0, 0])
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    rot = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
           [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
           [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[rot[r][c] * s[c] for c in range(3)] + [float(t[r])] for r in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def turned(node):
    """Поворот или масштаб в узле — трансформ не применён в модели."""
    m = node_matrix(node)
    return any(abs(m[r][c] - IDENTITY[r][c]) > EPS for r in range(3) for c in range(3))


def mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def apply(m, p):
    return tuple(m[r][0] * p[0] + m[r][1] * p[1] + m[r][2] * p[2] + m[r][3] for r in range(3))


def instances(doc):
    """(индекс меша, мировая матрица, имя узла, повёрнут ли путь) по сцене; без узлов — каждый меш как есть."""
    nodes = doc.get("nodes", [])
    scenes = doc.get("scenes", [])
    if scenes:
        roots = scenes[doc.get("scene", 0)].get("nodes", [])
    else:
        children = {c for node in nodes for c in node.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    found, seen = [], set()

    def walk(index, parent, rotated):
        if index in seen:
            return
        seen.add(index)
        node = nodes[index]
        world = mul(parent, node_matrix(node))
        rotated = rotated or turned(node)
        if "mesh" in node:
            found.append((node["mesh"], world, node.get("name") or f"узел {index}", rotated))
        for child in node.get("children", []):
            walk(child, world, rotated)

    for root in roots:
        walk(root, IDENTITY, False)
    if not found and not nodes:
        found = [(i, IDENTITY, f"меш {i}", False) for i in range(len(doc.get("meshes", [])))]
    return found


def image_size(data):
    """(ширина, высота) PNG или JPEG по заголовку; иначе None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            size = struct.unpack(">H", data[offset + 2:offset + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
                return width, height
            offset += 2 + size
    return None


def images(doc, buffers, folder, glb):
    """Текстуры: имя, внутри ли файла, размер; строки брака отдельно."""
    result, findings = [], []
    for n, image in enumerate(doc.get("images", [])):
        name = image.get("name") or image.get("uri") or f"картинка {n}"
        entry = {"name": name, "inside": False, "size": None}
        data = b""
        if "bufferView" in image:
            entry["inside"] = True
            data = bytes_of(doc, buffers, image["bufferView"])
        elif image.get("uri", "").startswith("data:"):
            entry["inside"] = True
            data = base64.b64decode(image["uri"].split(",", 1)[1])
        elif "uri" in image:
            beside = os.path.join(folder, unquote(image["uri"]))
            if glb:
                findings.append(f"текстура снаружи .glb: {image['uri']} — нужен один файл с текстурами внутри")
            elif not os.path.isfile(beside):
                findings.append(f"нет файла текстуры {image['uri']} рядом с моделью")
            else:
                entry["inside"] = True  # у .gltf «внутри» — файл рядом на месте
                with open(beside, "rb") as handle:
                    data = handle.read(64 * 1024)
        size = image_size(data) if data else None
        entry["size"] = list(size) if size else None
        result.append(entry)
    return result, findings


def check(path, budget, height, texture_max):
    doc, buffers, folder, glb = load(path)
    packed = [e for e in doc.get("extensionsRequired", []) if e in ("KHR_draco_mesh_compression", "KHR_mesh_quantization")]
    if packed:
        raise Bad(f"сжатие Draco или квантование ({', '.join(packed)}): пересохранить без сжатия — glTF без extensionsRequired")
    findings, notes = [], []
    found = instances(doc)
    triangles, low, high, turned_nodes = 0, [None] * 3, [None] * 3, []
    for mesh_index, world, name, rotated in found:
        mesh = doc["meshes"][mesh_index]
        if rotated and name not in turned_nodes:
            turned_nodes.append(name)
        for prim in mesh.get("primitives", []):
            triangles += primitive_triangles(doc, prim)
            if "indices" in prim:
                accessor(doc, buffers, prim["indices"], decode=False)  # индексы за буфером — файл обрезан
            position = prim.get("attributes", {}).get("POSITION")
            if position is None:
                continue
            for point in accessor(doc, buffers, position):
                x, y, z = apply(world, point)
                for k, v in enumerate((x, y, z)):
                    low[k] = v if low[k] is None or v < low[k] else low[k]
                    high[k] = v if high[k] is None or v > high[k] else high[k]
    if low[0] is None:
        raise Bad("в сцене нет геометрии (мешей с POSITION)")
    size = [high[k] - low[k] for k in range(3)]
    tol = max(PIVOT_MIN, PIVOT_SHARE * max(size))
    bottom, centre = low[1], ((low[0] + high[0]) / 2, (low[2] + high[2]) / 2)
    if turned_nodes:  # причина — первой; габарит, высота и опора — её следствия, их не судить
        findings.append("поворот или масштаб в узлах сцены (" + ", ".join(turned_nodes[:3])
                        + "): применить в модели — иначе «вперёд» +Z и метры не те, что в файле; "
                        "габарит, высота и опора — после применения трансформа")
    if budget is not None and triangles > budget:
        findings.append(f"треугольников {triangles} при бюджете {budget}")
    if not turned_nodes:
        if not (METRES[0] <= max(size) <= METRES[1]):
            findings.append(f"габарит {max(size):.4g} — не метры? glTF хранит метры, 1 единица = 1 м")
        if height is not None and not (height * (1 - HEIGHT_TOL) <= size[1] <= height * (1 + HEIGHT_TOL)):
            findings.append(f"высота {size[1]:.2f} м при заказанных {height:g} м (допуск ±25 %)")
        if abs(bottom) > tol:
            findings.append(f"опорная точка не внизу: низ модели на y = {bottom:.3f} м, нужен 0")
        if abs(centre[0]) > tol or abs(centre[1]) > tol:
            findings.append(f"опорная точка не по центру: центр x,z = {centre[0]:.3f},{centre[1]:.3f} м, нужен 0,0")
    pictures, picture_findings = images(doc, buffers, folder, glb)
    findings += picture_findings
    if texture_max is not None:
        for entry in pictures:
            if entry["size"] and max(entry["size"]) > texture_max:
                findings.append(f"текстура {entry['name']} {entry['size'][0]}×{entry['size'][1]} px больше {texture_max}")
    if not pictures:
        notes.append("текстур нет: цвет вершин или материал без текстуры — сверить с заказом")
    notes.append("«вперёд» +Z и лицо модели — по снимку на стенде рядом с капсулой 1,8 м")
    report = {
        "file": path, "format": "glb" if glb else "gltf", "triangles": triangles, "budget": budget,
        "size_m": [round(v, 4) for v in size], "bottom_y": round(bottom, 4),
        "center_xz": [round(v, 4) for v in centre], "instances": len(found),
        "meshes": len(doc.get("meshes", [])), "images": pictures, "findings": findings, "notes": notes,
    }
    if findings:
        report["verdict"] = "брак: " + "; ".join(findings)
    else:
        inside = sum(1 for p in pictures if p["inside"])
        report["verdict"] = (f"годен: треугольников {triangles}, {size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f} м "
                             f"(ширина × высота × глубина), опора внизу по центру, текстур внутри {inside}")
    return report


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", help="модель .glb или .gltf")
    parser.add_argument("--budget", type=int, help="предел треугольников из docs/orders/model3d.md")
    parser.add_argument("--height", type=float, help="ожидаемая высота в метрах (±25 %%)")
    parser.add_argument("--texture-max", type=int, help="предел стороны текстуры, px")
    parser.add_argument("--json", help="куда записать JSON; без него — на экран одной строкой")
    args = parser.parse_args()
    try:
        report = check(args.file, args.budget, args.height, args.texture_max)
    except Bad as error:
        report = {"file": args.file, "verdict": f"не умею: {error}"}
    except (KeyError, IndexError, TypeError, ValueError, struct.error) as error:
        report = {"file": args.file, "verdict": f"не умею: glTF не по схеме ({type(error).__name__}: {error})"}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(report, ensure_ascii=False))
    print(report["verdict"])
    verdict = report["verdict"]
    return 0 if verdict.startswith("годен") else 1 if verdict.startswith("брак") else 2


if __name__ == "__main__":
    sys.exit(main())
