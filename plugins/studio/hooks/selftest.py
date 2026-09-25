#!/usr/bin/env python3
"""Самопроверка хуков studio: python -X utf8 <плагин>/hooks/selftest.py

Подаёт guard_git.py и session_context.py синтетические события — с
кириллической папкой проекта и путями с пробелами — и сверяет ответ; так же
прогоняет шаблонный tools/roadmap_check.py на пробных картах (коды 0/1/2).
Проект не трогает: всё создаётся во временной папке и удаляется. Печатает
таблицу и итог; код 0 — всё верно, 1 — нет. Зовут /board и /setup.
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
ROADMAP_CHECK = os.path.join(HERE, "..", "skills", "setup", "templates", "tools", "roadmap_check.py")

MAP_CONCEPT = """# Концепция

## Одной строкой

Выживание в северном лесу.

## Ядро

Рубить ели, торговать с деревней.

## Цель и провал

Пережить зиму.

## Чего не делаем

Мультиплеер.

---

# Что работает
"""
MAP = """# Дорожная карта

## Этапы

### Этап 1. Первая ночь [идёт] · размер: средний
Зачем: весело ли рубить
Что увидит игрок: зайти → срубить ель → пережить 3 дня
Системы: С-01, С-02
Зависит от: —
Главный риск: нет
Вопросы к этапу: нет
Закрыт, когда: владелец прошёл «Что увидит игрок» и сказал «да»

### Этап 2. Деревня [следом] · размер: большой
Зачем: живая ли деревня
Что увидит игрок: дойти до деревни → продать доски
Системы: С-03
Зависит от: Этап 1
Главный риск: нет
Вопросы к этапу: нет
Закрыт, когда: владелец прошёл «Что увидит игрок» и сказал «да»

### Покрытие замысла

| № | Система | Раздел замысла | Слова владельца | Требует | Этап | Состояние |
|---|---|---|---|---|---|---|
| С-01 | Рубка | Ядро, Одной строкой | «Рубить ели» | — | 1 | в работе |
| С-02 | Здоровье и смерть | Цель и провал | `[выведено из С-01]` | — | 1 | в работе |
| С-03 | Торговля | Ядро | «торговать с деревней» | С-01 | 2 | впереди |
| С-04 | Сеть | Чего не делаем | «Мультиплеер» | — | Не делаем | впереди |

## Очередь

- **[можно] [код] [этап 1] Рубка ели.**
"""

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
CHOICE = DONE.replace("ждёт: какой цвет", "ждёт выбора · выбор 1/3")


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
        write(os.path.join(tmp, "выбор", "docs", "BATCH.md"), CHOICE)
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

        def context(name, cwd, expect, utf8=True, payload=None,
                    needles=("в работе: 2 (круг 2/3)", "готовы: 1")):
            code, out, _ = run(CONTEXT, payload or {"hook_event_name": "SessionStart",
                                                   "source": "compact", "cwd": cwd}, utf8)
            if expect:
                try:
                    data = json.loads(out)["hookSpecificOutput"]
                    text = data["additionalContext"]
                    ok = (code == 0 and data["hookEventName"] == "SessionStart"
                          and "идёт партия — 3 пункта" in text and all(n in text for n in needles)
                          and len(text.splitlines()) <= 6)
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
        context("остался выбор по листу", os.path.join(tmp, "выбор"), True,
                needles=("ждут выбора: 3", "готовы: 1, 2"))
        context("не проект studio", plain, False)
        context("битое событие", plain, False, payload=b"{not json")

        def version(name, label, expect):
            folder = os.path.join(tmp, "шаблон " + name)
            write(os.path.join(folder, "docs", "BATCH.md"), TEMPLATE)
            if label:
                write(os.path.join(folder, "CLAUDE.md"), f"<!-- Процесс: плагин studio, {label}. -->\n")
            code, out, _ = run(CONTEXT, {"hook_event_name": "SessionStart",
                                         "source": "startup", "cwd": folder})
            try:
                said = "/setup обновить" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
            except (ValueError, KeyError, TypeError):
                said = False
            ok = code == 0 and said == expect
            results.append(("session_context", f"шаблон проекта: {name}",
                            "напоминание" if expect else "тишина",
                            "напоминание" if said else (out.strip()[:40] or "тишина"), ok))

        version("старый v1", "шаблон v1", True)
        version("как у плагина", "шаблон v999", False)
        version("CLAUDE.md без метки", "без метки", True)
        version("нет CLAUDE.md", "", False)

        check_config(results)

        def roadmap(name, expect, needle, roadmap_text=MAP, extra=None):
            root = os.path.join(tmp, "проект игры", name)
            write(os.path.join(root, "docs", "CONCEPT.md"), MAP_CONCEPT)
            write(os.path.join(root, "docs", "ROADMAP.md"), roadmap_text)
            for rel, text in (extra or {}).items():
                write(os.path.join(root, rel), text)
            p = subprocess.run([sys.executable, "-X", "utf8", ROADMAP_CHECK, "--root", root],
                               capture_output=True, timeout=30)
            out = p.stdout.decode("utf-8", "replace")
            ok = p.returncode == expect and needle in out
            results.append(("roadmap_check", name, f"код {expect}", f"код {p.returncode}", ok))

        roadmap("верная карта", 0, "находок 0")
        roadmap("система без места", 1, "нет места", MAP.replace("| 2 | впереди |", "|  | впереди |"))
        roadmap("зависимость вперёд", 1, "зависимость вперёд",
                MAP.replace("| `[выведено из С-01]` | — |", "| `[выведено из С-01]` | С-03 |"))
        roadmap("этап без «Что увидит игрок»", 1, "нет «Что увидит игрок»",
                MAP.replace("Что увидит игрок: дойти до деревни → продать доски\n", ""))
        roadmap("«за 2 недели» в этапе", 1, "срок в «Этапах»",
                MAP.replace("Зачем: весело ли рубить", "Зачем: за 2 недели понять, весело ли рубить"))
        roadmap("два этапа «идёт»", 1, "больше одного", MAP.replace("[следом]", "[идёт]"))
        roadmap("незамеченный GAME_CONCEPT.md", 1, "ни одной системы",
                extra={"docs/GAME_CONCEPT.md": "# Замысел игры\n\n## Мир\n\nСевер и ели.\n"})
        roadmap("нет «Этапов»", 2, "этапов нет", "# Дорожная карта\n\n## Очередь\n\n- **[можно] Пункт.**\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    widths = [max(len(r[k]) for r in results) for k in range(4)]
    head = ("Хук", "Случай", "Ждали", "Вышло")
    widths = [max(w, len(h)) for w, h in zip(widths, head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)) + "  Итог")
    for r in results:
        print("  ".join(r[k].ljust(widths[k]) for k in range(4)) + ("  ок" if r[4] else "  ОШИБКА"))
    hooks = [r for r in results if r[0] != "roadmap_check"]
    maps = [r for r in results if r[0] == "roadmap_check"]
    bad = [f"{r[0]}: {r[1]}" for r in hooks if not r[4]]
    bad_maps = [r[1] for r in maps if not r[4]]
    verdict = (f"хуки НЕ работают — неверно {len(bad)} из {len(hooks)}: " + "; ".join(bad) if bad
               else f"хуки работают — {len(hooks)}/{len(hooks)} верно")
    verdict += (f"; проверка карты НЕ работает — неверно {len(bad_maps)} из {len(maps)}: " + "; ".join(bad_maps)
                if bad_maps else f"; проверка карты — {len(maps)}/{len(maps)} верно")
    print(f"Итог: {verdict}.")
    return 1 if bad or bad_maps else 0


if __name__ == "__main__":
    sys.exit(main())
