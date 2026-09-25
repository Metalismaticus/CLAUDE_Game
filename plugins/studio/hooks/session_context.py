#!/usr/bin/env python3
"""SessionStart-хук плагина studio: напомнить новой сессии, что идёт партия.

После обрыва (лимит, закрытый чат, сжатие контекста) чат разработки мог
продолжить по памяти, а не по docs/BATCH.md. Если в таблице партии есть пункт
«в работе» или «ждёт очереди», хук отдаёт короткий additionalContext (не больше
6 строк): сколько пунктов, какие в работе и готовы, как продолжить. Новый чат
без истории партию сам не продолжает. Нет партии — ничего не выводит. Любая
ошибка — код 0 и тишина.
"""
import json
import os
import re
import sys


def find_batch(path):
    path = os.path.abspath(path)
    while True:
        batch = os.path.join(path, "docs", "BATCH.md")
        if os.path.isfile(batch):
            return batch
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def batch_rows(text):
    """(№, состояние) из таблиц со столбцом «Состояние»; образцы в <!-- --> не в счёт."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    rows, header = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-*:?", c) for c in cells) or "Состояние" not in header:
            continue
        column = header.index("Состояние")
        if column < len(cells):
            rows.append((cells[0].strip("*` "), cells[column].strip("*` ")))
    return rows


def plural(n):
    if n % 10 == 1 and n % 100 != 11:
        return "пункт"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "пункта"
    return "пунктов"


def context(rows):
    working, ready, queued = [], [], []
    for number, state in rows:
        s = state.lower().replace("ё", "е")
        if s.startswith("в работе"):
            lap = re.search(r"круг\s*(\d+\s*/\s*\d+)", s)
            working.append(number + (f" (круг {lap.group(1)})" if lap else ""))
        elif s.startswith("ждет очереди"):
            queued.append(number)
        elif s.startswith("готов к проверке"):
            ready.append(number)
    if not working and not queued:
        return None
    parts = [f"в работе: {', '.join(working) or 'нет'}", f"готовы: {', '.join(ready) or 'нет'}"]
    if queued:
        parts.append(f"ждут очереди: {', '.join(queued)}")
    return "\n".join([
        f"studio: идёт партия — {len(rows)} {plural(len(rows))} ({', '.join(parts)}).",
        "Чат разработки (где звали /start) продолжает по `docs/BATCH.md` и "
        "`git log --grep \"Пункт\"`, а не по памяти:",
        "вызвать /studio:start без аргумента — он сверит партию с git.",
        "Новый чат без истории партию сам не продолжает — ждёт слова владельца.",
        "Чат замысла партию не трогает и меняет только `.md`.",
    ])


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return 0
    batch = find_batch(event.get("cwd") or os.getcwd())
    if not batch:
        return 0
    with open(batch, encoding="utf-8", errors="replace") as f:
        text = context(batch_rows(f.read()))
    if text:
        # ASCII-JSON: не зависит от кодировки stdout.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        if os.environ.get("STUDIO_HOOK_SELFTEST"):
            raise  # selftest.py отличает поломку хука от «пропустить»
        sys.exit(0)
