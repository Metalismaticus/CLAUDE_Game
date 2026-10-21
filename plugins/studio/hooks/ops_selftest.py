#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops_selftest — песочница для инструментов операций студии.

Проверяет шаблонные tools/run_check.py, slot_pool.py, batch_check.py и
ops_report.py плагина studio на ИМИТИРОВАННЫХ командах (мелкие python-скрипты
вместо сборок), в отдельной песочнице с пробелами и кириллицей в путях —
рабочий проект не трогается:

 1. одинаковый target-dir из разных рабочих папок и из слота;
 2. пути с пробелами и кириллицей;
 3. Rust-настройки только Rust-стеку (Godot-проект не получает CARGO_TARGET_DIR);
 4. последовательные задачи переиспользуют ограниченное число слотов;
 5. два исполнителя не получают один слот (предел пула, отказ, а не третий слот);
 6. занятый/отложенный/грязный слот и его кэш не очищаются; clean-cache — только
    зарегистрированные неактивные;
 7. недостаток места не запускает проверки и не создаёт слот (код 3, причина);
 8. item / affected / full выбирают ожидаемые проверки (эскалация с причиной);
 9. партия внутри комментария, «Пусто»+живая партия, параллельность без
    волн, слоты — находки batch_check;
10. sync восстанавливает учёт после прерывания (с отчётом, не догадкой);
11. diagnostics называет лишние каталоги с размерами и ничего не удаляет;
12. телеметрия пишется, только белые поля; ops_report отличает суммарное
    время операций от фактического.

Запуск: python -X utf8 <плагин>/hooks/ops_selftest.py  (секунды; песочница —
<Temp>/studio-ops-selftest, после прогона остаётся для разбора; рабочий
проект не трогается). Код 0 — все сценарии зелёные, 1 — нет.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "skills", "setup", "templates", "tools")
BATCH_TEMPLATE = os.path.join(HERE, "..", "skills", "setup", "templates", "docs", "BATCH.md")
BASE = os.path.join(tempfile.gettempdir(), "studio-ops-selftest")
PY = sys.executable

PASS, FAIL = [], []


def run(cmd, cwd, env=None, timeout=180):
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, timeout=timeout,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def tool(root, name, *args, cwd=None):
    return run([PY, "-X", "utf8", os.path.join(root, "tools", name), *args],
               cwd or root)


def last_json(out):
    for line in reversed(out.strip().splitlines()):
        if line.startswith("Итог: {"):
            return json.loads(line[len("Итог: "):])
    return None


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("[ok] " if cond else "[FAIL] ") + name +
          (f" — {detail}" if detail and not cond else ""))


def sh(cmd, cwd):
    code, out, err = run(cmd, cwd)
    assert code == 0, f"{cmd} -> {code}: {err or out}"
    return out


def fresh_base():
    if os.path.isdir(BASE):
        def force(func, path, _exc):
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)
        if sys.version_info >= (3, 12):
            shutil.rmtree(BASE, onexc=force)
        else:
            shutil.rmtree(BASE, onerror=lambda f, p, _e: force(f, p, None))
    os.makedirs(BASE)


def make_project(name, rust, cargo_enabled):
    """Проект с git, tools/ из шаблонов плагина и ИМИТАЦИЕЙ проверок."""
    root = os.path.join(BASE, name)          # пробелы и кириллица — в имени
    os.makedirs(os.path.join(root, "tools"))
    os.makedirs(os.path.join(root, "docs"))
    for f in ("studio_ops.py", "run_check.py", "slot_pool.py",
              "batch_check.py", "ops_report.py"):
        shutil.copy(os.path.join(TOOLS, f), os.path.join(root, "tools", f))
    shutil.copy(BATCH_TEMPLATE, os.path.join(root, "docs", "BATCH.md"))
    with open(os.path.join(root, "tools", "fake_check.py"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(
            "import os, sys\n"
            "name, code = sys.argv[1], int(sys.argv[2])\n"
            "tdir = os.environ.get('CARGO_TARGET_DIR')\n"
            "print('[%s] CARGO_TARGET_DIR=%s' % (name, tdir or '<нет>'))\n"
            "if tdir:\n"
            "    os.makedirs(tdir, exist_ok=True)\n"
            "    open(os.path.join(tdir, 'marker-%s.txt' % name), 'w',"
            " encoding='utf-8').write(tdir)\n"
            "sys.exit(code)\n")
    if rust:
        open(os.path.join(root, "Cargo.toml"), "w", encoding="utf-8").write(
            "[package]\nname = 'x'\n")
    else:
        open(os.path.join(root, "project.godot"), "w", encoding="utf-8").write(
            "; godot\nconfig_version=5\n")
    fc = [PY, "-X", "utf8", "tools/fake_check.py"]
    checks = [
        {"name": "быстрая", "modes": ["quick"], "cmd": fc + ["быстрая", "0"]},
        {"name": "пункт-сохранение", "modes": ["item", "affected", "full"],
         "areas": ["сохранение"], "cmd": fc + ["пункт-сохранение", "0"]},
        {"name": "ui-кадры", "modes": ["affected", "full"], "areas": ["ui"],
         "cmd": fc + ["ui-кадры", "0"]},
        {"name": "полная-интеграция", "modes": ["full"],
         "cmd": fc + ["полная-интеграция", "0"]},
        {"name": "долгая", "modes": ["long"], "cmd": fc + ["долгая", "0"]},
    ] if rust else [
        {"name": "быстрая", "modes": ["quick"], "cmd": fc + ["быстрая", "0"]},
        {"name": "пункт-сцена", "modes": ["item", "affected", "full"],
         "areas": ["сцена"], "cmd": fc + ["пункт-сцена", "0"]},
        {"name": "полная-интеграция", "modes": ["full"],
         "cmd": fc + ["полная-интеграция", "0"]},
    ]
    plan = {"disk_reserve_gb": 0, "pool": {"max_slots": 2},
            "cargo": {"enabled": cargo_enabled}, "default_timeout_s": 60,
            "checks": checks}
    with open(os.path.join(root, "tools", "check_plan.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    sh(["git", "init", "-q"], root)
    sh(["git", "config", "user.email", "t@t"], root)
    sh(["git", "config", "user.name", "t"], root)
    sh(["git", "add", "."], root)
    sh(["git", "commit", "-q", "-m", "init"], root)
    return root


def wt(root):
    return os.path.join(os.path.dirname(root), os.path.basename(root) + ".wt")


def set_reserve(root, gb):
    p = os.path.join(root, "tools", "check_plan.json")
    plan = json.load(open(p, encoding="utf-8"))
    plan["disk_reserve_gb"] = gb
    json.dump(plan, open(p, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)


def write_batch(root, text):
    with open(os.path.join(root, "docs", "BATCH.md"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(text)


TEMPLATE = open(BATCH_TEMPLATE, encoding="utf-8").read()
LIVE = TEMPLATE.replace(
    "Пусто.\n",
    "Снята `/start all` 2026-10-06 с «Очереди» на abc123.\n"
    "Пишут: 1 · тяжёлых проверок: 1\n"
    "Последовательно: общий файл проверок — scout запретил волны\n"
    "\n"
    "| № | Волна | Пункт очереди | Критерий готовности | Состояние |\n"
    "|---|---|---|---|---|\n"
    "| 1 | — | Сохранение | критерий | в работе · круг 1/3 |\n"
    "\n", 1)


def main():
    fresh_base()
    game = make_project("Проект Игры rust", rust=True, cargo_enabled=True)
    godot = make_project("Проект Годот", rust=False, cargo_enabled=False)

    # --- 1–2: target-dir одинаков из разных папок; пробелы/кириллица -----
    code, out, _ = tool(game, "run_check.py", "--mode", "item",
                        "--check", "пункт-сохранение")
    r1 = last_json(out)
    ok("run_check item зелёный", code == 0 and bool(r1 and r1["ok"]), out)
    ok("пути с кириллицей/пробелами работают",
       bool(r1) and "Проект Игры rust" in r1["root"], str(r1 and r1["root"]))
    code, out, _ = run([PY, "-X", "utf8", os.path.join(game, "tools", "run_check.py"),
                        "--root", game, "--mode", "item", "--check", "пункт-сохранение"],
                       cwd=BASE)  # чужая рабочая папка
    r2 = last_json(out)
    ok("target-dir не зависит от рабочей папки",
       bool(r1 and r2) and r1["target_dir"] == r2["target_dir"]
       and r2["target_dir"].endswith("targets" + os.sep + "main"),
       str(r2 and r2["target_dir"]))
    marker = os.path.join(r2["target_dir"], "marker-пункт-сохранение.txt")
    ok("CARGO_TARGET_DIR реально передан (маркер в targets/main)",
       os.path.isfile(marker) and open(marker, encoding="utf-8").read()
       == r2["target_dir"])

    # --- 3: Godot-стек без CARGO_TARGET_DIR ------------------------------
    code, out, _ = tool(godot, "run_check.py", "--mode", "item", "--check", "пункт-сцена")
    r3 = last_json(out)
    ok("godot: run_check работает", code == 0 and r3["ok"], out)
    ok("godot: cargo=False — CARGO_TARGET_DIR НЕ назначен", r3["cargo"] is False)
    code, out, _ = run([PY, "-X", "utf8",
                        os.path.join(godot, "tools", "fake_check.py"), "проба", "0"],
                       cwd=godot)
    ok("godot: ребёнок без CARGO_TARGET_DIR", "<нет>" in out, out)

    # --- 4–5: пул слотов --------------------------------------------------
    j = lambda o: json.loads(o.strip().splitlines()[-1])
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p1")
    ok("acquire p1 создаёт slot1", code == 0 and j(out)["slot"] == "slot1"
       and j(out)["created"], out)
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p2")
    ok("acquire p2 параллельно даёт ДРУГОЙ слот slot2",
       code == 0 and j(out)["slot"] == "slot2", out)
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p3")
    ok("acquire p3 при занятых — отказ (предел 2), НЕ третий слот",
       code == 1 and "нет свободного слота" in out, out)
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p1")
    ok("повтор acquire p1 — ТОТ ЖЕ слот (круги 2–3), не новому исполнителю",
       code == 0 and j(out)["slot"] == "slot1" and j(out)["reused"], out)
    tool(game, "slot_pool.py", "release", "slot2")
    tool(game, "slot_pool.py", "release", "slot1")
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p4")
    a4 = j(out)
    ok("после release следующий пункт берёт ПЕРЕИСПОЛЬЗОВАННЫЙ slot1",
       code == 0 and a4["slot"] == "slot1" and a4["reused"]
       and not a4["created"], out)

    # грязный слот: причина зафиксирована, в работу не выдан
    open(os.path.join(wt(game), "slot1", "хвост.txt"), "w",
         encoding="utf-8").write("остатки чужой работы")
    tool(game, "slot_pool.py", "release", "slot1")
    code, out, _ = tool(game, "slot_pool.py", "acquire", "--item", "p5")
    ok("грязный слот НЕ выдан — взят другой (slot2)", code == 0
       and j(out)["slot"] == "slot2", out)
    code, out, _ = tool(game, "slot_pool.py", "list")
    s1 = [s for s in j(out)["slots"] if s["slot"] == "slot1"][0]
    ok("slot1 помечен dirty с причиной (не молча)", s1["state"] == "dirty"
       and "остались изменения" in s1["note"], str(s1))

    # --- 6: занятый/отложенный слот и кэш не чистятся ---------------------
    os.makedirs(os.path.join(wt(game), "targets", "slot2"))
    cache2 = os.path.join(wt(game), "targets", "slot2", "cache.bin")
    open(cache2, "wb").write(b"x" * 100)
    code, out, _ = tool(game, "slot_pool.py", "clean-cache", "slot2")
    ok("clean-cache ЗАНЯТОГО слота — отказ", code == 1 and "не трогаем" in out, out)
    tool(game, "slot_pool.py", "park", "slot2", "--reason", "ждёт ответа владельца")
    code, out, _ = tool(game, "slot_pool.py", "clean-cache", "slot2")
    ok("clean-cache ОТЛОЖЕННОГО слота — отказ", code == 1 and "не трогаем" in out, out)
    ok("кэш слота жив после отказов", os.path.isfile(cache2))
    code, out, _ = tool(game, "slot_pool.py", "clean-cache", "--free")
    ok("clean-cache --free без свободных слотов ничего не чистит",
       code == 0 and os.path.isfile(cache2), out)
    # освобождение и чистка — только свободного и зарегистрированного
    tool(game, "slot_pool.py", "unpark", "slot2")
    tool(game, "slot_pool.py", "release", "slot2")
    os.makedirs(os.path.join(wt(game), "targets", "slot1"))
    cache1 = os.path.join(wt(game), "targets", "slot1", "cache.bin")
    open(cache1, "wb").write(b"y" * 100)
    code, out, _ = tool(game, "slot_pool.py", "clean-cache", "slot2")
    ok("clean-cache свободного слота чистит зарегистрированный кэш",
       code == 0 and not os.path.exists(os.path.join(wt(game), "targets", "slot2")), out)
    ok("clean-cache НЕ трогает грязный slot1", os.path.isfile(cache1))
    fake_foreign = os.path.join(wt(game), "targets", "произвольная-папка")
    os.makedirs(fake_foreign)
    code, out, _ = tool(game, "slot_pool.py", "clean-cache", "произвольная-папка")
    ok("clean-cache отказывает НЕслоту (защита пути)", code != 0
       and os.path.isdir(fake_foreign), out)

    # run_check из слота — свой target-dir (cwd внутри слота)
    slot2 = os.path.join(wt(game), "slot2")
    code, out, _ = run([PY, "-X", "utf8", os.path.join(slot2, "tools", "run_check.py"),
                        "--mode", "quick"], cwd=slot2)
    rs = last_json(out)
    ok("run_check из СЛОТА (cwd внутри, без --root): targets/slot2",
       code == 0 and rs["slot"] == "slot2"
       and rs["target_dir"].endswith("targets" + os.sep + "slot2"),
       str(rs and rs["target_dir"]))

    # --- 8: режимы item/affected/full ------------------------------------
    code, out, _ = tool(game, "run_check.py", "--mode", "item", "--check", "ui-кадры")
    ri = last_json(out)
    ok("item: только названная проверка", code == 0
       and [c["name"] for c in ri["checks"]] == ["ui-кадры"], out)
    code, out, _ = tool(game, "run_check.py", "--mode", "affected", "--areas", "ui")
    ra = last_json(out)
    ok("affected (известная область): только ui-кадры, НЕ весь набор",
       code == 0 and [c["name"] for c in ra["checks"]] == ["ui-кадры"]
       and not ra["escalated"], out)
    code, out, _ = tool(game, "run_check.py", "--mode", "affected",
                        "--areas", "ui,неизвестная-область")
    rf = last_json(out)
    ok("affected (неизвестная область): эскалация на full С ПРИЧИНОЙ",
       code == 0 and rf["escalated"] and "нет соответствия" in rf["reason"]
       and {c["name"] for c in rf["checks"]} ==
       {"пункт-сохранение", "ui-кадры", "полная-интеграция"}, out)
    code, out, _ = tool(game, "run_check.py", "--mode", "full", "--reason", "итог партии")
    rfull = last_json(out)
    ok("full: полный набор (3), причина повтора в телеметрии", code == 0
       and len(rfull["checks"]) == 3, out)
    code, out, _ = tool(game, "run_check.py", "--mode", "quick")
    ok("quick: только быстрая",
       last_json(out)["checks"][0]["name"] == "быстрая", out)

    # красная проверка сохраняет код возврата
    planp = os.path.join(game, "tools", "check_plan.json")
    plan = json.load(open(planp, encoding="utf-8"))
    plan["checks"].append({"name": "красная", "modes": ["full"],
                           "cmd": [PY, "-X", "utf8", "tools/fake_check.py",
                                   "красная", "7"]})
    json.dump(plan, open(planp, "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    code, out, _ = tool(game, "run_check.py", "--mode", "item", "--check", "красная")
    ok("красная: код возврата ребёнка сохранён (7)", code == 7
       and last_json(out)["checks"][0]["code"] == 7, f"code={code}")

    # --- 7: нехватка диска — на отдельном проекте (пул ещё пуст) ----------
    set_reserve(godot, 10_000_000)      # заведомо невыполнимый резерв
    code, out, _ = tool(godot, "run_check.py", "--mode", "full")
    ok("нехватка места: код 3, проверки НЕ запускались",
       code == 3 and "мало места" in out and last_json(out)["checks"] == [], out)
    code, out, _ = tool(godot, "slot_pool.py", "acquire", "--item", "p1")
    ok("нехватка места: слот НЕ создаётся (код 3, причина)",
       code == 3 and "мало места" in out
       and not os.path.exists(os.path.join(wt(godot), "slot1")), out)
    set_reserve(godot, 0)

    # --- 9: batch_check ---------------------------------------------------
    code, out, _ = tool(game, "batch_check.py")
    ok("batch_check: Пусто (партии нет) — зелёный", code == 0, out)

    inside = TEMPLATE.replace("Пусто.\n", "", 1).replace(
        "Снята `/start …` <дата> с «Очереди» на <хеш>.",
        "Снята `/start all` 2026-10-06 с «Очереди» на abc123.")
    write_batch(game, inside)
    code, out, _ = tool(game, "batch_check.py")
    ok("партия ВНУТРИ комментария — находка (код 1)",
       code == 1 and "В КОММЕНТАРИИ" in out, out)

    write_batch(game, LIVE)            # Пусто убрано, «Снята» — живая
    code, out, _ = tool(game, "batch_check.py")
    ok("живая партия с Пишут+Последовательно+слоты — зелёный", code == 0, out)

    no_seq = LIVE.replace(
        "| 1 | — | Сохранение | критерий | в работе · круг 1/3 |",
        "| 1 | — | Сохранение | критерий | в работе · круг 1/3 |\n"
        "| 2 | — | Интерфейс | критерий | в работе · круг 1/3 |")
    write_batch(game, no_seq)
    code, out, _ = tool(game, "batch_check.py")
    ok("параллельная партия без строки «Волны» — находка",
       code == 1 and "Волны" in out, out)

    pusto_live = LIVE.replace("Снята `/start all` 2026-10-06",
                              "Пусто.\nСнята `/start all` 2026-10-06")
    write_batch(game, pusto_live)
    code, out, _ = tool(game, "batch_check.py")
    ok("«Пусто» + живая партия — находка", code == 1
       and "«Пусто.» рядом" in out, out)

    conflict = LIVE.replace(
        "Пишут: 1 · тяжёлых проверок: 1\n"
        "Последовательно: общий файл проверок — scout запретил волны",
        "Пишут: 2 · тяжёлых проверок: 1").replace(
        "| 1 | — | Сохранение | критерий | в работе · круг 1/3 |",
        "| 1 | — | Сохранение | критерий | в работе · слот slot1 |\n"
        "| 2 | — | Интерфейс | критерий | в работе · слот slot1 |")
    write_batch(game, conflict)
    code, out, _ = tool(game, "batch_check.py")
    ok("один слот на двух пунктов — находка", code == 1
       and "двум пунктам" in out, out)

    # занятый слот пункта, которого нет в партии
    tool(game, "slot_pool.py", "acquire", "--item", "p5")
    write_batch(game, LIVE)
    code, out, _ = tool(game, "batch_check.py")
    ok("занятый слот пункта вне партии — находка", code == 1
       and "в партии «в работе» нет" in out, out)

    # --- 10: sync — восстановление после прерывания -----------------------
    sh(["git", "worktree", "add", "-q", "-b", "wave/p77",
        os.path.join(wt(game), "slot3"), "HEAD"], game)
    code, out, _ = tool(game, "slot_pool.py", "sync")
    ok("sync находит копию вне учёта — с отчётом, не молча",
       code == 0 and "slot3" in out
       and ("найден вне учёта" in out or "найден при sync" in out), out)

    # --- 11: diagnostics не удаляет чужое --------------------------------
    os.makedirs(os.path.join(wt(game), "slot3", "target", "debug"))
    open(os.path.join(wt(game), "slot3", "target", "debug", "junk.bin"),
         "wb").write(b"z" * 1000)
    os.makedirs(os.path.join(wt(game), "Проект Игры rust.wt"))
    # там же, где ищет diagnostics: tempfile.gettempdir() (на Linux TEMP не задан)
    stray_temp = os.path.join(tempfile.gettempdir(), "target-slot77")
    os.makedirs(stray_temp)
    open(os.path.join(stray_temp, "junk.bin"), "wb").write(b"q" * 500)
    code, out, _ = tool(game, "slot_pool.py", "diagnostics")
    ok("diagnostics: лишние каталоги с размерами названы",
       code == 0 and ("slot3" + os.sep + "target") in out
       and "target-slot77" in out and "вложенная" in out, out)
    ok("diagnostics: НИЧЕГО не удалено",
       os.path.isdir(os.path.join(wt(game), "slot3", "target"))
       and os.path.isdir(stray_temp)
       and os.path.isdir(os.path.join(wt(game), "Проект Игры rust.wt")))
    shutil.rmtree(stray_temp)

    # --- 12: телеметрия ---------------------------------------------------
    logp = os.path.join(wt(game), "usage", "studio-ops.jsonl")
    ok("телеметрия пишется", os.path.isfile(logp))
    events = []
    for proj in (game, godot):   # disk_stop ушёл в журнал godot-проекта
        p = os.path.join(wt(proj), "usage", "studio-ops.jsonl")
        if os.path.isfile(p):
            events += [json.loads(line) for line in open(p, encoding="utf-8")
                       if line.strip()]
    if events:
        kinds = {e["kind"] for e in events}
        ok("телеметрия: все виды событий на месте",
           {"slot_acquire", "slot_release", "slot_park", "slot_prep",
            "check_run", "disk_stop", "batch_check", "cache_clean"} <= kinds,
           str(kinds))
        no_env = all("environ" not in str(e) and "PATH" not in str(e)
                     for e in events)
        ok("телеметрия: без окружения и секретов", no_env)
    code, out, _ = tool(game, "ops_report.py")
    ok("ops_report: суммарное vs фактическое время различаются",
       code == 0 and "суммарное время" in out and "фактическое" in out, out)

    print()
    print(f"Итог: {len(PASS)} зелёных / {len(FAIL)} провалов")
    for f in FAIL:
        print("провал:", f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
