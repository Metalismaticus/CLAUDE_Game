#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_check — машинная проверка состояния партии docs/BATCH.md.

Проверяет то, что уже ломало живые прогоны (docs/WHY.md плагина):

- партия записана ВНУТРИ комментария-образца — протокол её не видит, над ним
  остаётся «Пусто.»;
- «Пусто.» соседствует с живой партией;
- номер пункта повторяется в таблице;
- партия параллельная, а параметры волн не записаны: строки «Волны (от
  `scout`): 1: [N, M] — …» в шапке нет, или пункт «в работе» не назван ни в
  одной волне, или волна называет пункт, которого в таблице нет;
- слоты противоречивы: один слот на двух пунктах, занятый слот пункта,
  которого в партии «в работе» нет (учёт пула `tools/slot_pool.py`).

Комментарий-образец ОТЛИЧАЕТСЯ от заполненной партии: его строки — шаблоны с
угловыми скобками (`<дата>`, `<хеш>`), таблица в нём пуста. Заполненный маркер
(реальная дата, строка таблицы) внутри комментария = партия в комментарии.

Некорректное состояние — конкретная диагностика со строками, код 1; молча
«восстанавливать» неоднозначное догадкой batch_check не делает.

Запуск: python -X utf8 tools/batch_check.py [--root <путь>]
Выход: 0 — в порядке (или партии нет); 1 — находки; 2 — проект не развёрнут.
"""
import argparse
import json
import os
import re
import sys

sys.dont_write_bytecode = True  # инструменты не пачкают проект/слот кэшем байткода
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_ops as ops  # noqa: E402

SNATA = re.compile(r"(?m)^(?:>?\s*)?Снята\s")
PISTO = re.compile(r"(?m)^Пусто\.?\s*$")
WAVES = re.compile(r"(?m)^Волны[^:]*:\s*(.+)$")
WAVE_ITEM = re.compile(r"(\d+)\s*:\s*\[([^\]]*)\]")
SLOT_MENTION = re.compile(r"слот\s*(slot\d+)")
STATES = ("в работе", "готов к проверке", "ждёт очереди", "не выполнен", "ждёт:")
INWORK = "в работе"


def strip_comments(text):
    """(живой текст, [(начало, конец, тело)] комментариев-образцов)."""
    comments = []
    for m in re.finditer(r"<!--", text):
        end = text.find("-->", m.start())
        if end < 0:
            break
        body = text[m.start():end + 3]
        if "Образец партии" in body or ("№" in body and "|" in body and "Волна" in body):
            comments.append((m.start(), end, body))
    live = list(text)
    for s, e, _body in comments:
        for i in range(s, min(e + 3, len(live))):
            live[i] = ""
    return "".join(live), comments


def line_no(text, pos):
    return text.count("\n", 0, pos) + 1


def filled_batch_markers(body):
    """Заполненные маркеры партии в блоке текста: (шаблон, совпадение)."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("Снята ") and "<" not in s:
            out.append(("шапка", s))
        if s.startswith("|") and "---" not in s and any(st in s for st in STATES):
            out.append(("строка таблицы", s))
    return out


def parse_rows(block):
    """[(№, состояние, сырая строка)] строк таблицы (состояние — 5-я колонка)."""
    rows = []
    for raw in block.splitlines():
        s = raw.strip()
        if not s.startswith("|") or "---" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("№", ""):
            continue
        rows.append((cells[0], cells[4], raw))
    return rows


def parse_waves(header):
    """{волна: [номера пунктов строками]} из «Волны (от `scout`): 1: [2, 3] — …»."""
    m = WAVES.search(header)
    if not m:
        return {}
    return {w: re.findall(r"\d+", items) for w, items in WAVE_ITEM.findall(m.group(1))}


def check(root):
    """None (проект не развёрнут) или список находок."""
    path = ops.norm(os.path.join(root, "docs", "BATCH.md"))
    if not os.path.isfile(path):
        return None
    text = open(path, encoding="utf-8").read()
    live, comments = strip_comments(text)
    found = []

    for s, e, body in comments:
        filled = filled_batch_markers(body)
        if filled:
            kinds = ", ".join(k for k, _v in filled)
            found.append(f"партия записана В КОММЕНТАРИИ-образце (строки "
                         f"{line_no(text, s)}–{line_no(text, e)}; {kinds}) — протокол "
                         f"её не видит; переписать в живую часть НАД комментарием, "
                         f"вместо «Пусто.»")

    live_batch = SNATA.search(live)
    live_empty = PISTO.search(live)
    if live_batch and live_empty:
        found.append(f"«Пусто.» рядом с живой партией («Снята …», строка "
                     f"{line_no(live, live_batch.start())}) — убрать «Пусто.»")
    if not live_batch:
        return found

    header, _, table = live.partition("| №")
    waves = parse_waves(header)
    in_wave = {n for items in waves.values() for n in items}

    rows = parse_rows(table or live)
    nums = [n for n, _st, _r in rows]
    for n in set(nums):
        if nums.count(n) > 1:
            found.append(f"номер пункта {n} повторяется в таблице")
    # волна называет пункт, которого нет в таблице
    for w, items in waves.items():
        for n in items:
            if n not in nums:
                found.append(f"волна {w} называет пункт {n}, которого в таблице нет")
    inwork = [(n, st, r) for n, st, r in rows if INWORK in st]
    parallel = len(waves) > 1 or len(inwork) > 1
    if parallel and not waves:
        found.append("пункты идут параллельно, а строки «Волны (от `scout`): …» "
                     "в шапке нет — записать состав волн")
    for n, _st, _r in inwork:
        if waves and n not in in_wave:
            found.append(f"пункт {n} «в работе», а ни одна волна его не называет — "
                         f"добавить в «Волны (от `scout`): …»")
    # слоты: двойное назначение, если строки упоминают слоты
    item_by_slot = {}
    for n, _st, raw in inwork:
        for s in SLOT_MENTION.findall(raw):
            if s in item_by_slot and item_by_slot[s] != n:
                found.append(f"слот {s} назначен двум пунктам: {item_by_slot[s]} и {n}")
            item_by_slot[s] = n
    # сверка с учётом пула слотов
    try:
        with open(ops.norm(os.path.join(ops.wt_dir(root), "slots.json")),
                  encoding="utf-8-sig") as f:
            pool = json.load(f).get("slots", {})
    except (OSError, ValueError):
        pool = {}
    for name, rec in pool.items():
        if rec.get("state") == "busy" and rec.get("item"):
            num = rec["item"].lstrip("pP")
            if not any(n == num for n, _st, _r in inwork):
                found.append(f"слот {name} занят пунктом {rec['item']}, которого "
                             f"в партии «в работе» нет — slot_pool.py release {name} "
                             f"или вернуть пункт в партию")
    return found


def main(argv=None):
    ops.utf8_stdio()
    ap = argparse.ArgumentParser(add_help=False, description="проверка партии")
    ap.add_argument("--root", help="корень проекта (по умолчанию — ищется вверх)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) and e.code else ops.EXIT_BAD
    try:
        root = ops.norm(args.root) if args.root else ops.find_root()
        if root is None or not os.path.isdir(root):
            raise ops.OpError(ops.EXIT_BAD, "корень проекта не найден: назовите --root")
        root = ops.main_root(root)
        found = check(root)
        if found is None:
            raise ops.OpError(ops.EXIT_BAD,
                              f"нет {ops.norm(os.path.join(root, 'docs', 'BATCH.md'))} — "
                              f"проект не развёрнут (/setup)")
    except ops.OpError as e:
        print(f"ошибка: {e.msg}", file=sys.stderr)
        return e.code

    if found:
        for f in found:
            print("находка:", f)
        payload = {"ok": False, "findings": found}
        code = ops.EXIT_RED
    else:
        payload = {"ok": True}
        print("Итог: партия в порядке")
        code = ops.EXIT_OK
    print(f"Итог: {json.dumps(payload, ensure_ascii=False)}")
    ops.log_event(root, "batch_check", ok=payload["ok"], findings=len(found))
    return code


if __name__ == "__main__":
    sys.exit(main())
