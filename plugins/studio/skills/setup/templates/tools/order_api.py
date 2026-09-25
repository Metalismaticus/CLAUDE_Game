#!/usr/bin/env python3
"""Отправить заказ картинки в OpenAI Images API и сохранить результат.

Развёрнут плагином studio (/setup), зовётся командой /order при исполнителе `api`.
Только стандартная библиотека. Ключ берётся из переменной окружения
OPENAI_API_KEY — в файлы проекта и в аргументы его не писать.

    python tools/order_api.py --prompt-file p.txt --out assets/ui/icon.png \
        --size 1024x1024 --background transparent --ref assets/ui/a.png --ref assets/ui/b.png

Файл пишется во временный рядом и встаёт на место одним os.replace: оборванная
запись не оставляет полкартинки. Оплаченная картинка не теряется: цель
появилась, пока шёл заказ, — она ложится рядом как <имя>-new1.<ext>; цель
занята — остаётся во временном файле, путь в причине. При --background
transparent пришедшее проверяет tools/asset_check.py --alpha required, если
рядом есть Pillow; нет — предупреждение, проверит /add.

Вывод: строка «сохранено: …» (с итогом проверки прозрачности) и последней —
JSON-строка {"ok": …, "path": …, "model": …, "bytes": …}; bytes > 0 — файл
сохранён по path. Причина отказа — в stderr.

Коды возврата: 0 — файл сохранён; 1 — отказ или ошибка сервиса, либо файл
сохранён, но проверка прозрачности красная (строка «сохранено», ok: false);
2 — неверный вызов (нет ключа, нет промта, цель уже существует).
"""
import argparse
import base64
import importlib.util
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

API = "https://api.openai.com/v1/images"
SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
RESULT = {"ok": False, "path": None, "model": None, "bytes": 0}


def report():
    print(json.dumps(RESULT, ensure_ascii=False))


def fail(code, text):
    print(text, file=sys.stderr)
    report()
    sys.exit(code)


def save(path, data):
    """Временный файл рядом с целью и os.replace — на месте либо целое, либо ничего."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".part", dir=folder)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if os.path.exists(temp):
            os.remove(temp)
        raise
    try:
        os.replace(temp, path)
    except OSError as error:  # оплаченную картинку не терять
        raise OSError(f"{error}; картинка осталась во временном файле {temp}") from error


def spare_name(path):
    root, ext = os.path.splitext(path)
    number = 1
    while os.path.exists(f"{root}-new{number}{ext}"):
        number += 1
    return f"{root}-new{number}{ext}"


def check_alpha(path):
    """tools/asset_check.py --alpha required: (True | False | None — не проверено, строка)."""
    checker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asset_check.py")
    if not os.path.isfile(checker):
        return None, "прозрачность не проверена: нет tools/asset_check.py — проверит /add"
    if importlib.util.find_spec("PIL") is None:
        return None, "прозрачность не проверена: нужна Pillow (python -m pip install pillow) — проверит /add"
    try:
        done = subprocess.run([sys.executable, "-X", "utf8", checker, path, "--alpha", "required"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"прозрачность не проверена: {error} — проверит /add"
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    line = (lines[-1] if lines else "нет вывода").replace(f": {path} — ", ": ", 1)
    if done.returncode in (0, 1):
        return done.returncode == 0, f"проверка прозрачности — {line}"
    return None, f"прозрачность не проверена: {line} — проверит /add"


def multipart(fields, files):
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, path in files:
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{os.path.basename(path)}"\r\n'
        ).encode()
        body += f"Content-Type: {mime}\r\n\r\n".encode()
        with open(path, "rb") as handle:
            body += handle.read()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prompt-file", required=True, help="файл с полным промтом, UTF-8")
    parser.add_argument("--out", required=True, help="полный путь файла из заказа")
    parser.add_argument("--size", default="1024x1024", choices=SIZES)
    parser.add_argument("--background", default="auto", choices=("auto", "transparent", "opaque"))
    parser.add_argument("--quality", default="high", choices=("low", "medium", "high", "auto"))
    parser.add_argument("--ref", action="append", default=[], help="образец; можно несколько")
    parser.add_argument("--model", default=os.environ.get("ORDER_IMAGE_MODEL", "gpt-image-1"))
    parser.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    args = parser.parse_args()
    RESULT.update(path=args.out, model=args.model)

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        fail(2, "Нет OPENAI_API_KEY в окружении. Заказ остаётся долгом с исполнителем «вручную».")
    if os.path.exists(args.out) and not args.force:
        fail(2, f"Цель уже существует: {args.out}. Готовое не перезаписывается без --force.")
    if not os.path.isfile(args.prompt_file):
        fail(2, f"Нет файла промта: {args.prompt_file}")
    with open(args.prompt_file, encoding="utf-8") as handle:
        prompt = handle.read().strip()
    if not prompt:
        fail(2, "Промт пуст.")
    for ref in args.ref:
        if not os.path.isfile(ref):
            fail(2, f"Образец не найден: {ref}")

    fields = [
        ("model", args.model),
        ("prompt", prompt),
        ("size", args.size),
        ("background", args.background),
        ("quality", args.quality),
        ("n", "1"),
    ]
    if args.ref:
        body, content_type = multipart(fields, [("image[]", ref) for ref in args.ref])
        url = f"{API}/edits"
    else:
        payload = dict(fields)
        payload["n"] = 1
        body, content_type = json.dumps(payload).encode(), "application/json"
        url = f"{API}/generations"

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            pass
        fail(1, f"Сервис ответил {error.code}: {detail}")
    except urllib.error.URLError as error:
        fail(1, f"Нет связи с сервисом: {error.reason}")

    try:
        image = base64.b64decode(answer["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError):
        fail(1, "В ответе нет картинки.")

    if os.path.exists(args.out) and not args.force:
        spare = spare_name(args.out)
        try:
            save(spare, image)
        except OSError as error:
            fail(1, f"Цель появилась, пока шёл заказ, и запасной файл не сохранён: {error}")
        RESULT.update(path=spare, bytes=len(image))
        fail(2, f"Цель появилась, пока шёл заказ: {args.out}. Не перезаписываю; новая картинка — {spare}.")
    try:
        save(args.out, image)
    except OSError as error:
        fail(1, f"Не удалось сохранить {args.out}: {error}")
    RESULT.update(ok=True, bytes=len(image))
    line = f"сохранено: {args.out} ({len(image)} байт, модель {args.model}, образцов {len(args.ref)})"
    if args.background == "transparent":
        good, verdict = check_alpha(args.out)
        line += f"; {verdict}"
        if good is False:
            RESULT["ok"] = False
    print(line)
    report()
    sys.exit(0 if RESULT["ok"] else 1)


if __name__ == "__main__":
    main()
