#!/usr/bin/env python3
"""PreToolUse-хук плагина studio: не дать взять в коммит чужие файлы.

В папке проекта одновременно работают два чата, поэтому файлы в коммит
называются поимённо. Хук срабатывает только в проектах, развёрнутых /setup
(есть docs/BATCH.md), и только на `git add -A`, `git add --all`, `git add .`
и `git commit -a` — в том числе в копиях проекта через `git -C <путь>`.
Код 2 блокирует вызов, текст из stderr уходит модели. Любая собственная
ошибка хука — код 0: он не должен мешать работе.
"""
import json
import os
import re
import sys

GIT = r"""\bgit\s+(?:-C\s+(?:"[^"]*"|'[^']*'|\S+)\s+)?"""
ADD_ALL = re.compile(GIT + r"add\s+(?:\S+\s+)*?(?:-A|--all|\.)(?=\s|$|[;&|])")
COMMIT_ALL = re.compile(GIT + r"commit\s+(?:\S+\s+)*?(?:-[b-zB-Z]*a[a-zA-Z]*|--all)(?=\s|$|[;&|])")


def main():
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    cwd = event.get("cwd") or os.getcwd()
    if not os.path.isfile(os.path.join(cwd, "docs", "BATCH.md")):
        return 0
    if ADD_ALL.search(command) or COMMIT_ALL.search(command):
        print(
            "studio: в этой папке работают два чата — файлы в коммит называть "
            "поимённо (git add <файл> <файл>), без -A, --all, «.» и commit -a. "
            "См. CLAUDE.md, «Два чата».",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
