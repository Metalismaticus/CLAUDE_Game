#!/usr/bin/env python3
"""Самопроверка хуков studio: python -X utf8 <плагин>/hooks/selftest.py

Подаёт guard_git.py и session_context.py синтетические события — с
кириллической папкой проекта и путями с пробелами — и сверяет ответ. Проект не
трогает: всё создаётся во временной папке и удаляется. Печатает таблицу и
итог; код 0 — всё верно, 1 — нет. Зовут /board и /setup.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard_git.py")
CONTEXT = os.path.join(HERE, "session_context.py")

BATCH = """# Текущая партия

Снята `/start all` 2026-09-25 с «Очереди» на abc1234.

| № | Волна | Пункт очереди | Критерий готовности | Состояние | Как увидеть | Решено за вас | В документы при `/done` |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Трава [код] | … | готов к проверке | … | нет | … |
| 2 | 1 | Водопад [ui] | … | в работе · круг 2/3 | … | … | … |
| 3 | 2 | Деревья [код] | … | ждёт очереди | … | … | … |
"""
TEMPLATE = """# Текущая партия

Пусто.

<!-- Образец партии:
| № | Волна | Пункт очереди | Критерий готовности | Состояние | Как увидеть | Решено за вас | В документы при `/done` |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Пример | … | в работе · круг 1/3 | … | … | … |
-->
"""
DONE = BATCH.replace("в работе · круг 2/3", "готов к проверке").replace("ждёт очереди", "ждёт: какой цвет")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def run(script, payload, utf8=True):
    """Запустить хук как Claude Code: событие в UTF-8 на stdin."""
    if not isinstance(payload, bytes):
        payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["STUDIO_HOOK_SELFTEST"] = "1"  # сбой хука — код 1, а не молчаливый «пропуск»
    cmd = [sys.executable] + (["-X", "utf8"] if utf8 else []) + [script]
    p = subprocess.run(cmd, input=payload, capture_output=True, timeout=30, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def check_config(results):
    """hooks.json: оба хука подключены через python -X utf8, файлы на месте."""
    try:
        with open(os.path.join(HERE, "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        pre = [h["command"] for e in hooks["PreToolUse"] for h in e["hooks"]
               if "Bash" in e["matcher"] and "PowerShell" in e["matcher"]]
        start = [h["command"] for e in hooks["SessionStart"] for h in e["hooks"]
                 if all(s in e["matcher"] for s in ("startup", "resume", "clear", "compact"))]
        ok = (any("python -X utf8" in c and "hooks/guard_git.py" in c for c in pre)
              and any("python -X utf8" in c and "hooks/session_context.py" in c for c in start)
              and os.path.isfile(GUARD) and os.path.isfile(CONTEXT))
        got = "подключены" if ok else "не так"
    except Exception as e:  # noqa: BLE001 — любая поломка файла = провал
        ok, got = False, f"ошибка: {e}"
    results.append(("hooks.json", "оба хука, python -X utf8", "подключены", got, ok))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    tmp = tempfile.mkdtemp(prefix="studio-selftest-")
    try:
        project = os.path.join(tmp, "конструктор игр")
        copy = os.path.join(tmp, "конструктор игр.wt", "slot1")
        plain = os.path.join(tmp, "обычная папка")
        idle = os.path.join(tmp, "пустая партия")
        write(os.path.join(project, "docs", "BATCH.md"), BATCH)
        write(os.path.join(copy, "docs", "BATCH.md"), BATCH)
        write(os.path.join(idle, "docs", "BATCH.md"), TEMPLATE)
        write(os.path.join(tmp, "готово", "docs", "BATCH.md"), DONE)
        os.makedirs(os.path.join(project, "src", "мир"))
        os.makedirs(plain)
        results = []

        def guard(name, command, expect_block, cwd=project, tool="Bash", utf8=True):
            event = {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd,
                     "tool_input": {"command": command}}
            code, _, err = run(GUARD, event, utf8)
            want = 2 if expect_block else 0
            ok = code == want and (not expect_block or "studio:" in err)
            got = {2: "блок", 0: "пропуск"}.get(code, f"код {code}")
            results.append(("guard_git", name, "блок" if expect_block else "пропуск", got, ok))

        blocked = [
            ("git add -A", "git add -A"),
            ("git add --all", "git add --all"),
            ("git add .", "git add ."),
            ("git add -u", "git add -u"),
            ("git commit -am", 'git commit -am "Пункт 1: трава"'),
            ("git commit -a -m", 'git commit -a -m "x"'),
            ("git stash", "git stash"),
            ("git stash pop", "git stash pop"),
            ("git pull --rebase --autostash", "git pull --rebase --autostash"),
            ("git rebase --autostash", "git rebase --autostash main"),
            ("git clean -fd", "git clean -fd"),
            ("git reset --hard", "git reset --hard"),
            ("git reset --hard HEAD~1", "git reset --hard HEAD~1"),
            ("git checkout -- .", "git checkout -- ."),
            ("git checkout .", "git checkout ."),
            ("git restore .", "git restore ."),
            ("git restore --staged .", "git restore --staged ."),
            ("git push --force", "git push --force"),
            ("git push -f", "git push -f origin main"),
            ("git push --force-with-lease", "git push --force-with-lease"),
            ("git push +ветка", "git push origin +main"),
            ("-C с кириллицей и пробелом", f'git -C "{project}" stash'),
            ("после другой команды", "git status && git add -A"),
            ("PowerShell через ;", 'git add -A; git commit -m "x"'),
            ("внутри bash -c", 'bash -c "git stash"'),
            ("-C из копии в основную", f'git -C "{project}" reset --hard'),
            ("cwd — подпапка проекта", "git stash"),
            ("без -X utf8 (сбой 0.3.1)", "git add -A"),
        ]
        for name, command in blocked:
            cwd = os.path.join(project, "src", "мир") if "подпапка" in name else \
                copy if "из копии" in name else project
            tool = "PowerShell" if "PowerShell" in name else "Bash"
            guard(name, command, True, cwd=cwd, tool=tool, utf8="без -X" not in name)

        allowed = [
            ("git add поимённо", 'git add docs/ROADMAP.md "src/мир/старые деревья.gd"'),
            ("слова запрета в сообщении", 'git commit -m "Пункт 3: без git add -A и git stash"'),
            ("here-doc в сообщении", "git commit -F - <<'EOF'\nПункт 1: git reset --hard не нужен\nEOF"),
            ("сообщение через $(cat <<EOF)",
             "git commit -m \"$(cat <<'EOF'\nПункт 2: убран \"git stash\" (см. шаг)\nEOF\n)\""),
            ("git checkout -- <файл>", "git checkout -- docs/ROADMAP.md"),
            ("git checkout -B в копии", f'git -C "{copy}" checkout -B wave/p2 main'),
            ("reset --hard в копии через -C", f'git -C "{copy}" reset --hard'),
            ("clean -fd в копии через -C", f'git -C "{copy}" clean -fd'),
            ("reset --hard, cwd — копия", "git reset --hard"),
            ("cd в копию && clean", f'cd "{copy}" && git clean -fd'),
            ("git push", "git push"),
            ("git fetch && git merge --no-edit",
             "git fetch && git merge --no-edit origin/main"),
            ("git bisect reset", "git bisect reset"),
            ("git revert --no-edit", "git revert --no-edit abc1234"),
            ("git log --grep", 'git log --grep "Пункт"'),
            ("git worktree remove --force", f'git worktree remove --force "{copy}"'),
            ("не проект studio", "git add -A"),
            ("не Bash", "git add -A"),
        ]
        for name, command in allowed:
            cwd = copy if "cwd — копия" in name else plain if "не проект" in name else project
            tool = "Write" if name == "не Bash" else "Bash"
            guard(name, command, False, cwd=cwd, tool=tool)

        code, _, _ = run(GUARD, b"{not json")
        results.append(("guard_git", "битое событие", "пропуск", f"код {code}", code == 0))

        def context(name, cwd, expect, utf8=True, payload=None):
            code, out, _ = run(CONTEXT, payload or {"hook_event_name": "SessionStart",
                                                   "source": "compact", "cwd": cwd}, utf8)
            if expect:
                try:
                    data = json.loads(out)["hookSpecificOutput"]
                    text = data["additionalContext"]
                    ok = (code == 0 and data["hookEventName"] == "SessionStart"
                          and "идёт партия — 3 пункта" in text and "в работе: 2 (круг 2/3)" in text
                          and "готовы: 1" in text and len(text.splitlines()) <= 6)
                    got = "напоминание"
                except (ValueError, KeyError, TypeError):
                    ok, got = False, (out.strip()[:40] or "пусто")
            else:
                ok = code == 0 and not out.strip()
                got = "тишина" if ok else (out.strip()[:40] or f"код {code}")
            results.append(("session_context", name, "напоминание" if expect else "тишина", got, ok))

        context("идёт партия", project, True)
        context("идёт партия, без -X utf8", project, True, utf8=False)
        context("подпапка проекта", os.path.join(project, "src", "мир"), True)
        context("шаблон «Пусто.» с образцом", idle, False)
        context("всё готово к проверке", os.path.join(tmp, "готово"), False)
        context("не проект studio", plain, False)
        context("битое событие", plain, False, payload=b"{not json")

        check_config(results)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    widths = [max(len(r[k]) for r in results) for k in range(4)]
    head = ("Хук", "Случай", "Ждали", "Вышло")
    widths = [max(w, len(h)) for w, h in zip(widths, head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)) + "  Итог")
    for r in results:
        print("  ".join(r[k].ljust(widths[k]) for k in range(4)) + ("  ок" if r[4] else "  ОШИБКА"))
    bad = [f"{r[0]}: {r[1]}" for r in results if not r[4]]
    if bad:
        print(f"Итог: хуки НЕ работают — неверно {len(bad)} из {len(results)}: " + "; ".join(bad))
        return 1
    print(f"Итог: хуки работают — {len(results)}/{len(results)} верно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
