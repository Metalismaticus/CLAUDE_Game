#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_check — ЕДИНЫЙ механизм запуска проверок студии.

Протоколы (исполнитель, проверяющий, координатор) зовут проверки ТОЛЬКО через
него; руками CARGO_TARGET_DIR не выставляют и не меняют между командами.

Что он делает (и почему — docs/WHY.md плагина, «Почему пул слотов…»):

- принимает явный корень проекта или рабочего слота (--root; без него — ищет
  корень вверх от текущей папки), вычисляет абсолютные нормализованные пути;
  рабочая папка оболочки не влияет на результат;
- назначает ровно один сборочный каталог каждому слоту и основной папке:
  `<проект>.wt/targets/<слот>` — Rust-стеку (Cargo.toml / src-tauri) проставляет
  CARGO_TARGET_DIR; не-Rust (Godot и др.) — не трогает окружение стека;
- передаёт ОДНО И ТО ЖЕ окружение всем дочерним процессам проверки;
- режимы: item (одна по имени) / affected (явно связанные подсистемы по плану)
  / full (полный набор) / quick / long. Соответствие areas неизвестно — прогон
  идёт как full С ЯВНОЙ ПРИЧИНОЙ в результате, а не «группа», равная всему;
- перед тяжёлыми режимами (full/long) и любой сборкой Rust — проверка свободного
  места по резерву из tools/check_plan.json: нехватка = код 3 с причиной,
  БЕЗ запуска проверок и повторных циклов;
- сохраняет код возврата: 0 — зелено, иначе код первого провалившегося
  (124 — предел времени), 2 — неверный вызов/план, 3 — мало места;
- печатает компактный итог: строки по проверкам + последняя строка
  «Итог: {json}» для машинной проверки; команды без оболочки, списком
  аргументов — пробелы и кириллица в путях безопасны.

Запуск: python -X utf8 tools/run_check.py --root <путь> --mode <режим>
        [--check <имя>] [--areas <области через запятую>] [--reason <почему повторно>]
"""
import argparse
import json
import os
import sys
import time

sys.dont_write_bytecode = True  # инструменты не пачкают проект/слот кэшем байткода
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_ops as ops  # noqa: E402

MODES = ("quick", "item", "affected", "full", "long")


def select_checks(plan, mode, check_name=None, areas=None):
    """(выбранные проверки, эскалация?, причина). Соответствие неизвестно — full."""
    checks = {c["name"]: c for c in plan["checks"]}
    if mode == "item":
        if not check_name:
            raise ops.OpError(ops.EXIT_BAD, "режим item требует --check <имя>")
        if check_name not in checks:
            raise ops.OpError(ops.EXIT_BAD,
                              f"нет проверки «{check_name}» в плане; есть: "
                              + ", ".join(sorted(checks)))
        return [checks[check_name]], False, ""
    if mode == "affected":
        if not areas:
            raise ops.OpError(ops.EXIT_BAD,
                              "режим affected требует --areas <области через запятую> "
                              "(какие подсистемы задел пункт)")
        given = [a.strip() for a in areas.split(",") if a.strip()]
        candidates = [c for c in plan["checks"] if "affected" in c["modes"]]
        matched = [c for c in candidates
                   if set(a.strip() for a in c.get("areas", [])) & set(given)]
        unmatched = [a for a in given
                     if not any(a in (x.strip() for x in c.get("areas", []))
                                for c in candidates)]
        if candidates and matched and not unmatched:
            return matched, False, ""
        why = (f"affected: нет соответствия в tools/check_plan.json "
               f"(области без проверок: {', '.join(unmatched) if unmatched else '—'}) "
               f"— прогон идёт как full, состав affected не доказан")
        full = [c for c in plan["checks"] if "full" in c["modes"]]
        if not full:
            raise ops.OpError(ops.EXIT_BAD, why + "; в плане нет ни одной проверки full")
        return full, True, why
    sel = [c for c in plan["checks"] if mode in c["modes"]]
    if not sel:
        raise ops.OpError(ops.EXIT_BAD,
                          f"в плане нет проверок режима {mode} — заполните "
                          "tools/check_plan.json (/setup обновить)")
    return sel, False, ""


def main(argv=None):
    ops.utf8_stdio()
    ap = argparse.ArgumentParser(add_help=False, description="единый запуск проверок")
    ap.add_argument("--root", help="корень проекта или слота (по умолчанию — ищется вверх от текущей папки)")
    ap.add_argument("--mode", choices=MODES)
    ap.add_argument("--check", help="имя проверки (режим item)")
    ap.add_argument("--areas", help="подсистемы через запятую (режим affected)")
    ap.add_argument("--reason", help="причина повторного запуска — попадает в телеметрию")
    ap.add_argument("--timeout", type=int, default=None, help="общий предел времени на проверку, с")
    ap.add_argument("--list", action="store_true", help="показать план и выйти")
    ap.add_argument("--json", action="store_true", help="печатать только json-итог")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) and e.code else ops.EXIT_BAD
    if not args.list and not args.mode:
        print("нужен --mode <режим>; план проверок: --list", file=sys.stderr)
        return ops.EXIT_BAD

    try:
        root = ops.norm(args.root) if args.root else ops.find_root()
        if args.root and not os.path.isdir(root):
            raise ops.OpError(ops.EXIT_BAD, f"--root: нет папки {root}")
        if root is None or not os.path.isdir(root):
            raise ops.OpError(ops.EXIT_BAD,
                              "корень проекта не найден: назовите --root <путь проекта>")
        plan = ops.load_plan(root)
        if args.list:
            for c in plan["checks"]:
                print(f"{c['name']} · режимы: {','.join(c['modes'])} · "
                      f"области: {','.join(c.get('areas', [])) or '—'}")
            return ops.EXIT_OK
        selected, escalated, why = select_checks(
            plan, args.mode, args.check, args.areas)
    except ops.OpError as e:
        print(f"Итог: {json.dumps({'ok': False, 'error': e.msg}, ensure_ascii=False)}")
        return e.code

    slot = ops.slot_name(root)
    tgt = ops.target_dir(root)
    rust = ops.is_rust_stack(root, plan)

    env = dict(os.environ)
    if rust:
        env["CARGO_TARGET_DIR"] = tgt
        env.setdefault("PYTHONIOENCODING", "utf-8")

    disk_ok, free, reserve, disk_msg = True, None, None, ""
    heavy = args.mode in ("full", "long") or (rust and selected)
    if heavy:
        disk_ok, free, reserve, disk_msg = ops.check_disk(
            tgt if os.path.exists(ops.wt_dir(root)) else root, plan)
        if not disk_ok:
            ops.log_event(root, "disk_stop", mode=args.mode, slot=slot,
                          free_gb=round(free / 1e9, 1), reserve_gb=round(reserve / 1e9, 1))
            result = {"ok": False, "mode": "stop", "requested": args.mode, "escalated": False,
                      "reason": disk_msg, "root": root, "slot": slot, "target_dir": tgt,
                      "checks": [], "exit": ops.EXIT_DISK,
                      "disk": {"free_gb": round(free / 1e9, 1), "reserve_gb": round(reserve / 1e9, 1)}}
            print(disk_msg)
            print(f"Итог: {json.dumps(result, ensure_ascii=False)}")
            return ops.EXIT_DISK

    results, first_fail_code = [], ops.EXIT_OK
    t0 = time.monotonic()
    for c in selected:
        timeout = args.timeout or int(c.get("timeout_s") or plan.get("default_timeout_s") or 600)
        code, ms, out_full, err_full = ops.run_cmd(
            c["cmd"], cwd=root, env=env, timeout_s=timeout)
        ok = code == 0
        if not ok and first_fail_code == ops.EXIT_OK:
            first_fail_code = code
        tail = (err_full or out_full).strip().splitlines()[-1] if (err_full or out_full).strip() else ""
        results.append({"name": c["name"], "code": code, "ms": ms, "ok": ok,
                        "tail": tail[:400]})
        if not args.json:
            mark = "ok" if ok else f"FAIL {code}"
            line = f"[{mark}] {c['name']} ({ms} мс)"
            if not ok and results[-1]["tail"]:
                line += f" — {results[-1]['tail']}"
            print(line)
    dur_ms = int((time.monotonic() - t0) * 1000)

    result = {
        "ok": first_fail_code == ops.EXIT_OK, "mode": args.mode,
        "requested": args.mode, "escalated": escalated, "reason": why,
        "root": root, "slot": slot, "target_dir": tgt, "cargo": rust,
        "checks": results, "exit": first_fail_code or ops.EXIT_OK,
        "dur_ms": dur_ms, "rerun_reason": args.reason or "",
        "disk": ({"free_gb": round(free / 1e9, 1), "reserve_gb": round(reserve / 1e9, 1)}
                 if free is not None else {}),
    }
    if escalated and not args.json:
        print(f"эскалация: {why}")
    if not args.json:
        green = sum(1 for r in results if r["ok"])
        print(f"Итог прогона ({args.mode}): зелёных {green}/{len(results)}"
              + (f" · сборка: {tgt}" if rust else ""))
    print(f"Итог: {json.dumps(result, ensure_ascii=False)}")
    ops.log_event(root, "check_run", mode=args.mode, requested=args.mode,
                  escalated=escalated, reason=(why or None), slot=slot,
                  target_dir=tgt, checks=[f"{r['name']}:{r['code']}" for r in results],
                  dur_ms=dur_ms, rerun_reason=(args.reason or None),
                  exit_code=first_fail_code or ops.EXIT_OK)
    return first_fail_code or ops.EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ops.OpError as e:
        print(e.msg, file=sys.stderr)
        sys.exit(e.code)
