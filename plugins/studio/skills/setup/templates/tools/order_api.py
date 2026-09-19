#!/usr/bin/env python3
"""Отправить заказ картинки в OpenAI Images API и сохранить результат.

Развёрнут плагином studio (/setup), зовётся командой /order при исполнителе `api`.
Только стандартная библиотека. Ключ берётся из переменной окружения
OPENAI_API_KEY — в файлы проекта и в аргументы его не писать.

    python tools/order_api.py --prompt-file p.txt --out assets/ui/icon.png \
        --size 1024x1024 --background transparent --ref assets/ui/a.png --ref assets/ui/b.png

Коды возврата: 0 — файл сохранён; 1 — отказ или ошибка сервиса; 2 — неверный
вызов (нет ключа, нет промта, цель уже существует).
"""
import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid

API = "https://api.openai.com/v1/images"
SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")


def fail(code, text):
    print(text, file=sys.stderr)
    sys.exit(code)


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

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        fail(2, "Нет OPENAI_API_KEY в окружении. Заказ остаётся долгом с исполнителем «вручную».")
    if os.path.exists(args.out) and not args.force:
        fail(2, f"Цель уже существует: {args.out}. Готовое не перезаписывается без --force.")
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

    folder = os.path.dirname(args.out)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(args.out, "wb") as handle:
        handle.write(image)
    print(f"сохранено: {args.out} ({len(image)} байт, модель {args.model}, образцов {len(args.ref)})")


if __name__ == "__main__":
    main()
