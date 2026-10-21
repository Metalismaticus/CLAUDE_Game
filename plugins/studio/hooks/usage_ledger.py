#!/usr/bin/env python3
"""Журнал токенов: сколько стоил каждый агент и чат — для `/retro` и `ops_report.py --tokens`.

Хук Claude Code на события SubagentStop (остановился субагент) и Stop
(закончил ход основной чат). Только в проекте, развёрнутом `/setup` (есть
`docs/BATCH.md` в cwd или выше; из копии `../<проект>.wt/slotK` — в основной
папке). Читает расшифровку сессии (JSONL, путь приходит в событии:
`agent_transcript_path` у субагента, `transcript_path` у чата), складывает
`usage` ответов модели, которых ещё не считал, и дописывает одну строку в
`../<папка проекта>.wt/usage/studio-usage.jsonl`:

    {"t": "<ISO>", "event": "SubagentStop", "agent": "executor", "item": "p3",
     "sid": "<session_id>", "aid": "<agent_id>", "model": "...",
     "in": N, "out": N, "cacheRead": N, "cacheWrite": N, "cost": 0}

`agent` — тип субагента из события (`agent_type`; нет — `subagent`), у
основного чата — `chat`; `item` — `p<N>` из первой строки задания
координатора («Пункт N партии…»), иначе нет. `cost` всегда 0: цены моделей
хук не знает, сравнивать версии плагина — по токенам. Что уже посчитано,
помнит `../<папка проекта>.wt/usage/usage-seen.json` (смещение в файле и
последние id ответов — у ответа несколько строк расшифровки с одним id).
Старые строки журнала — ротация в `studio-usage.jsonl.1` при 5 МБ.

Никаких секретов: текст сообщений не читается дальше первой строки задания,
в журнал идут только перечисленные поля. Любая ошибка — молча, код 0: учёт
не ломает работу. Проверка — `hooks/selftest.py` (пробная расшифровка).
"""
import json
import os
import re
import sys

LIMIT = 5 * 1024 * 1024
KEEP_IDS = 64
ITEM = re.compile(r"(?:Пункт|Item)\s+(\d+)")


def find_root(start):
    p = os.path.normpath(os.path.abspath(start))
    while True:
        if os.path.isfile(os.path.join(p, "docs", "BATCH.md")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def main_root(root):
    parts = root.replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-1]):
        if part.endswith(".wt"):
            return os.path.normpath("/".join(parts[:i] + [part[:-3]]))
    return root


def usage_dir(root):
    root = os.path.normpath(root)
    return os.path.join(os.path.dirname(root), os.path.basename(root) + ".wt", "usage")


def first_item(line):
    """`p<N>` из первой строки-задания пользователя (координатора) в расшифровке."""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if row.get("type") != "user":
        return None
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    if not isinstance(content, str):
        return None
    m = ITEM.search(content[:400])
    return f"p{m.group(1)}" if m else None


def collect(path, state):
    """(суммы, новое состояние) по строкам расшифровки после прежнего смещения."""
    entry = state.get(path) or {"offset": 0, "ids": []}
    seen = list(entry.get("ids") or [])
    sums = {"in": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0}
    model, item, n = None, entry.get("item"), 0
    size = os.path.getsize(path)
    offset = entry.get("offset", 0)
    if offset > size:  # файл переписан — считать заново
        offset, seen = 0, []
    with open(path, "rb") as f:
        f.seek(offset)
        first = offset == 0
        while True:
            raw = f.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):  # недописанная строка — в следующий раз
                break
            offset += len(raw)
            line = raw.decode("utf-8", errors="replace")
            if first and item is None:
                item = first_item(line) or item
            first = False
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") != "assistant":
                continue
            msg = row.get("message") or {}
            usage = msg.get("usage") or {}
            mid = msg.get("id") or row.get("uuid")
            if not usage or not mid or mid in seen:
                continue
            seen.append(mid)
            n += 1
            model = msg.get("model") or model
            sums["in"] += int(usage.get("input_tokens") or 0)
            sums["out"] += int(usage.get("output_tokens") or 0)
            sums["cacheRead"] += int(usage.get("cache_read_input_tokens") or 0)
            sums["cacheWrite"] += int(usage.get("cache_creation_input_tokens") or 0)
    state[path] = {"offset": offset, "ids": seen[-KEEP_IDS:], "item": item}
    return sums, model, item, n


def append_row(folder, row):
    os.makedirs(folder, exist_ok=True)
    log = os.path.join(folder, "studio-usage.jsonl")
    try:
        if os.path.getsize(log) > LIMIT:
            os.replace(log, log + ".1")
    except OSError:
        pass
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(event):
    kind = event.get("hook_event_name") or ""
    path = event.get("agent_transcript_path") if kind == "SubagentStop" else event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return
    root = find_root(event.get("cwd") or os.getcwd())
    if root is None:
        return
    folder = usage_dir(main_root(root))
    seen_path = os.path.join(folder, "usage-seen.json")
    try:
        with open(seen_path, encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    sums, model, item, n = collect(os.path.normpath(path), state)
    if n:
        import datetime
        row = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "event": kind,
               "agent": (event.get("agent_type") or "subagent") if kind == "SubagentStop" else "chat",
               "sid": event.get("session_id"), "model": model, "cost": 0, **sums}
        if kind == "SubagentStop":
            row["aid"] = event.get("agent_id")
        if item:
            row["item"] = item
        append_row(folder, row)
    os.makedirs(folder, exist_ok=True)
    tmp = seen_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, seen_path)


def main():
    try:
        event = json.load(sys.stdin)
        if isinstance(event, dict):
            run(event)
    except Exception:  # noqa: BLE001 — учёт не ломает работу
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
