#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ops_report — сводка телеметрии операций студии.

Читает `<проект>.wt/usage/studio-ops.jsonl` (пишут run_check.py и
slot_pool.py) и показывает эффект изменений:

- длительность подготовки слотов (slot_prep) и переиспользование
  (slot_acquire reused/created);
- проверки: сколько, какие режимы, длительность, эскалации affected→full,
  повторные запуски с причинами;
- используемый каталог сборки (target_dir);
- нехватки диска (disk_stop) и их причины;
- назначение/освобождение слотов.

`--tokens` — токены моделей из `usage/studio-usage.jsonl` (+ ротация `.1`):
сумма по агентам и по сессиям (sid) — сессии склеиваются с пунктами по
`history` задачи (`begin` — исполнители, `review` — проверяющие).

СУММАРНОЕ время операций ≠ ФАКТИЧЕСКОЕ время: при параллельном исполнении
операции идут одновременно — сводка показывает обе: сумму dur_ms по событиям
и размах (последнее − первое) по часам. Никаких секретов: в файле только
перечисленные поля.

Запуск: python -X utf8 tools/ops_report.py [--root <путь>] [--since <ISO-дата>] [--tokens]
"""
import argparse
import datetime
import json
import os
import sys

sys.dont_write_bytecode = True  # инструменты не пачкают проект/слот кэшем байткода
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_ops as ops  # noqa: E402


def usage_events(root, since=None):
    """Строки токенов из usage/studio-usage.jsonl и его ротации .1, по времени."""
    base = os.path.join(ops.wt_dir(root), "usage", "studio-usage.jsonl")
    rows = []
    for path in (base, f"{base}.1"):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if since and row.get("t", "") < since:
                    continue
                rows.append(row)
    rows.sort(key=lambda r: r.get("t", ""))
    return rows


def tokens_of(row):
    return ((row.get("in") or 0) + (row.get("out") or 0) + (row.get("cacheRead") or 0)
            + (row.get("cacheWrite") or 0) + (row.get("reasoning") or 0))


def token_lines(rows):
    if not rows:
        return False
    total = sum(tokens_of(r) for r in rows)
    cost = sum(r.get("cost") or 0 for r in rows)
    print(f"токены моделей: {total:,} · стоимость ${cost:.2f} · записей {len(rows)}")
    by_agent = {}
    for r in rows:
        key = r.get("agent") or "?"
        by_agent[key] = [by_agent.get(key, [0, 0.0])[0] + tokens_of(r),
                         by_agent.get(key, [0, 0.0])[1] + (r.get("cost") or 0)]
    print("по агентам: " + " · ".join(f"{a} {t:,} (${c:.2f})"
                                      for a, (t, c) in sorted(by_agent.items(), key=lambda kv: -kv[1][0])))
    by_sid = {}
    for r in rows:
        sid = r.get("sid") or "?"
        acc = by_sid.setdefault(sid, [0, 0.0, ""])
        acc[0] += tokens_of(r)
        acc[1] += r.get("cost") or 0
        acc[2] = r.get("agent") or acc[2]
    by_item = {}
    for r in rows:
        item = r.get("item")
        if item:
            by_item[item] = by_item.get(item, 0) + tokens_of(r)
    if by_item:
        print("по пунктам (строка `item` журнала — из задания координатора): "
              + " · ".join(f"{i} {t:,}" for i, t in sorted(by_item.items(), key=lambda kv: -kv[1])))
    print("по сессиям (топ-20; sid — сессия чата, aid — субагент; пункт без `item` — по времени "
          "«с <дата ЧЧ:ММ>» в docs/BATCH.md):")
    for sid, (t, c, agent) in sorted(by_sid.items(), key=lambda kv: -kv[1][0])[:20]:
        print(f"  {sid} · {agent} · {t:,} · ${c:.2f}")
    return True


def main(argv=None):
    ops.utf8_stdio()
    ap = argparse.ArgumentParser(add_help=False, description="сводка телеметрии операций")
    ap.add_argument("--root", help="корень проекта (по умолчанию — ищется вверх)")
    ap.add_argument("--since", help="события с этой ISO-даты (например, со «Снята …»)")
    ap.add_argument("--tokens", action="store_true",
                    help="сводка токенов моделей по агентам и сессиям (usage/studio-usage.jsonl + ротация)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) and e.code else ops.EXIT_BAD
    try:
        root = ops.norm(args.root) if args.root else ops.find_root()
        if root is None or not os.path.isdir(root):
            raise ops.OpError(ops.EXIT_BAD, "корень проекта не найден: назовите --root")
        root = ops.main_root(root)
        events = ops.read_events(root)
    except ops.OpError as e:
        print(f"ошибка: {e.msg}", file=sys.stderr)
        return e.code
    rows = usage_events(root, args.since) if args.tokens else []
    if not events:
        if token_lines(rows):
            return ops.EXIT_OK
        print(f"телеметрии нет: {ops.ops_log(root)}")
        return ops.EXIT_OK
    if args.since:
        events = [e for e in events if e.get("t", "") >= args.since]
    if not events:
        if token_lines(rows):
            return ops.EXIT_OK
        print("после фильтра --since событий нет")
        return ops.EXIT_OK

    def ts(e):
        try:
            return datetime.datetime.fromisoformat(e["t"])
        except (KeyError, ValueError):
            return None

    times = [ts(e) for e in events]
    times = [t for t in times if t]
    wall = (times[-1] - times[0]).total_seconds() if len(times) > 1 else 0.0
    dur_sum = sum(e.get("dur_ms") or e.get("prep_ms") or 0 for e in events) / 1000.0

    kinds = {}
    for e in events:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    print(f"событий: {len(events)} · с {events[0].get('t')} по {events[-1].get('t')}")
    print(f"суммарное время операций: {dur_sum:.0f} с · фактическое (размах): "
          f"{wall:.0f} с — при параллельной работе операции идут одновременно")
    print("по видам: " + ", ".join(f"{k} × {v}" for k, v in sorted(kinds.items())))

    acq = [e for e in events if e["kind"] == "slot_acquire"]
    if acq:
        reused = sum(1 for e in acq if e.get("reused"))
        created = sum(1 for e in acq if e.get("created"))
        print(f"слоты: назначений {len(acq)} · переиспользовано {reused} · создано {created}"
              + (f" (предел превышен бы — создай больше копий)" if created > 2 else ""))
    preps = [e for e in events if e["kind"] == "slot_prep"]
    if preps:
        ms = [e.get("prep_ms") or 0 for e in preps]
        print(f"подготовка слота: {len(preps)} раз · {min(ms)}–{max(ms)} мс (worktree add)")
    runs = [e for e in events if e["kind"] == "check_run"]
    if runs:
        by_mode = {}
        for e in runs:
            by_mode.setdefault(e.get("mode"), []).append(e)
        for mode, evs in sorted(by_mode.items(), key=lambda kv: str(kv[0])):
            ms = [e.get("dur_ms") or 0 for e in evs]
            esc = [e for e in evs if e.get("escalated")]
            reruns = [e for e in evs if e.get("rerun_reason")]
            line = (f"проверки {mode}: {len(evs)} прогонов · {sum(ms)} мс · "
                    f"сборка {evs[0].get('target_dir')}")
            if esc:
                line += f" · эскалаций на full: {len(esc)}"
            if reruns:
                line += f" · повторов: {len(reruns)} ({'; '.join(e.get('rerun_reason') for e in reruns)})"
            print(line)
    stops = [e for e in events if e["kind"] == "disk_stop"]
    if stops:
        for e in stops:
            print(f"остановка по диску: свободно {e.get('free_gb')} ГБ < резерв "
                  f"{e.get('reserve_gb')} ГБ ({e.get('what') or e.get('mode')})")
    bc = [e for e in events if e["kind"] == "batch_check"]
    if bc:
        bad = sum(1 for e in bc if not e.get("ok"))
        print(f"batch_check: {len(bc)} прогонов · красных {bad}")
    if args.tokens:
        token_lines(rows)
    return ops.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
