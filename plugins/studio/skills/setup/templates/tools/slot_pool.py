#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""slot_pool — ОГРАНИЧЕННЫЙ ПЕРЕИСПОЛЬЗУЕМЫЙ ПУЛ СЛОТОВ студии.

Слот — РЕСУРС исполнения, а не номер пункта очереди: сколько пунктов ни шло
бы, слотов не больше предела (tools/check_plan.json, pool.max_slots), между
задачами они переиспользуются, сборочный кэш `<проект>.wt/targets/<слот>`
переживает пункты (его назначает и использует run_check.py).

Гарантии (docs/WHY.md плагина, «Почему пул слотов…»):

- один слот не выдаётся двум исполнителям: занятость пишется в
  `<проект>.wt/slots.json` под межпроцессной блокировкой; повторный acquire
  того же пункта возвращает ТОТ ЖЕ слот (круги 2–3 того же пункта);
- свободный слот с оставшимися изменениями НЕ используется молча: помечается
  dirty с причиной, берётся другой;
- занятые (busy) и отложенные (parked) слоты и их кэши НЕ очищаются;
- после прерывания `sync` восстанавливает учёт из `git worktree list`
  с ОТЧЁТОМ (не молча и не догадкой): найденное вне учёта — dirty с причиной;
- перед созданием слота — проверка свободного места (код 3 = остановка
  с причиной, ничего не создаётся);
- автоочистка — только зарегистрированных кэшей неактивных слотов
  (clean-cache), с проверкой нормализованного пути и принадлежности слоту;
  произвольные target-папки по всему диску не ищутся и не удаляются;
  диагностика лишних каталогов (diagnostics) — только пути и размеры.

Запуск: python -X utf8 tools/slot_pool.py <подкоманда> …   (корень — основная
папка проекта; --root можно опустить, он ищется вверх от текущей папки)
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.dont_write_bytecode = True  # инструменты не пачкают проект/слот кэшем байткода
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_ops as ops  # noqa: E402


def state_file(root):
    return ops.norm(os.path.join(ops.wt_dir(root), "slots.json"))


def load_state(root):
    try:
        with open(state_file(root), encoding="utf-8-sig") as f:  # BOM — не брак
            data = json.load(f)
        if not isinstance(data.get("slots"), dict):
            raise ValueError("нет словаря slots")
        return data
    except FileNotFoundError:
        return {"slots": {}}
    except ValueError as e:
        raise ops.OpError(ops.EXIT_BAD, f"{state_file(root)} испорчен: {e} — "
                                        "посмотрите руками и перепишите (sync не "
                                        "пересоздаёт учёт с нуля по догадке)")


def save_state(root, data):
    ops.atomic_write_json(state_file(root), data)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def git_slot_clean(slot_dir):
    code, _, out, err = ops.git(slot_dir, "status", "--porcelain")
    if code != 0:
        return False, f"git status не вышел (код {code}): {err or out}"
    if out.strip():
        first = out.strip().splitlines()[0].strip()
        return False, f"остались изменения: {first}"
    return True, ""


def worktree_slots(root):
    """{имя слота: путь} из git worktree list — фактическое состояние диска."""
    code, _, out, err = ops.git(root, "worktree", "list", "--porcelain")
    if code != 0:
        raise ops.OpError(ops.EXIT_BAD, f"git worktree list не вышел: {err or out}")
    base = ops.wt_dir(root)
    found = {}
    for block in out.split("\n\n"):
        m = re.search(r"(?m)^worktree (.+)$", block)
        if not m:
            continue
        path = ops.norm(m.group(1))
        if ops.same_dir(os.path.dirname(path), base) and ops.SLOT_RE.match(os.path.basename(path)):
            found[os.path.basename(path)] = path
    return found


def next_slot_name(root, state, wt_slots):
    nums = [int(ops.SLOT_RE.match(n).group(1))
            for n in list(state["slots"]) + list(wt_slots) if ops.SLOT_RE.match(n)]
    return "slot%d" % (max(nums, default=0) + 1)


def json_out(payload, only_json=False):
    print(json.dumps(payload, ensure_ascii=False))


def max_slots(root, plan, override=None):
    if override is not None:
        return max(1, int(override))
    pool = plan.get("pool") or {}
    return max(1, int(pool.get("max_slots", 2)))


# ------------------------------------------------------------------ подкоманды

def cmd_acquire(root, plan, args):
    item = args.item
    if not re.match(r"^[A-Za-zА-Яа-яЁё0-9_-]+$", item):
        raise ops.OpError(ops.EXIT_BAD, f"--item «{item}»: только имя пункта (p3, trial …)")
    branch = args.branch
    base = args.base or "HEAD"  # текущая ветка проекта — их одна («Два чата»)
    limit = max_slots(root, plan, args.max_slots)
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        wt_slots = worktree_slots(root)
        # уже назначен — тот же слот (круги 2–3, повтор упавшего)
        for name, rec in state["slots"].items():
            if rec.get("state") == "busy" and rec.get("item") == item:
                json_out({"slot": name, "path": wt_slots.get(name) or
                          ops.slot_path(root, name), "branch": rec.get("branch"),
                          "state": "busy", "reused": True, "created": False,
                          "note": "уже назначен этому пункту", "item": item,
                          "target_dir": ops.norm(os.path.join(ops.wt_dir(root), "targets", name))},
                         args.json)
                ops.log_event(root, "slot_acquire", slot=name, item=item, reused=True)
                return ops.EXIT_OK
        taken, created, prep_ms, path, branch_used = None, False, 0, None, branch
        for name in sorted(wt_slots, key=lambda n: int(ops.SLOT_RE.match(n).group(1))):
            rec = state["slots"].get(name, {})
            if rec.get("state") in ("busy", "parked"):
                continue
            clean, why = git_slot_clean(wt_slots[name])
            if not clean:
                if rec.get("state") != "dirty" or rec.get("note") != why:
                    state["slots"][name] = {"state": "dirty", "note": why,
                                            "item": rec.get("item"),
                                            "branch": rec.get("branch"), "since": now()}
                print(f"слот {name} свободен, но не годится: {why}", file=sys.stderr)
                continue
            taken = name
            path = wt_slots[name]
            # Переиспользуемый слот переключить на ветку пункта: существующую
            # (круг 2+ после прерывания) или новую от головы основной папки.
            desired = branch or f"wave/{item}"
            have, _, _, _ = ops.git(path, "rev-parse", "--verify", "--quiet", desired + "^{commit}")
            if have == 0:
                switch = ("switch", desired)
            else:
                _, _, base_out, _ = ops.git(root, "rev-parse", "HEAD")
                base = base_out.strip() or args.base or "HEAD"
                switch = ("switch", "-c", desired, base)
            scode, _, sout, serr = ops.git(path, *switch, timeout_s=120)
            if scode != 0:
                json_out({"error": f"не переключить слот {name} на {desired}: {(serr or sout).strip()}"}, args.json)
                return ops.EXIT_RED
            branch_used = desired
            break
        if taken is None:
            total = len(set(list(wt_slots) + list(state["slots"])))
            if total >= limit:
                busy = {n: r.get("item") for n, r in state["slots"].items()
                        if r.get("state") == "busy"}
                dirty = [n for n, r in state["slots"].items() if r.get("state") == "dirty"]
                parked = [n for n, r in state["slots"].items() if r.get("state") == "parked"]
                json_out({"error": f"нет свободного слота: предел {limit}; заняты {busy or '—'}; "
                                   f"грязные {dirty or '—'}; отложенные {parked or '—'} — "
                                   f"дождитесь слияния (release) или поднимите pool.max_slots "
                                   f"в tools/check_plan.json",
                          "limit": limit, "busy": busy, "dirty": dirty, "parked": parked},
                         args.json)
                return ops.EXIT_RED
            ok, free, reserve, msg = ops.check_disk(ops.wt_dir(root), plan)
            if not ok:
                ops.log_event(root, "disk_stop", what="slot_create",
                              free_gb=round(free / 1e9, 1), reserve_gb=round(reserve / 1e9, 1))
                json_out({"error": msg, "limit": limit}, args.json)
                return ops.EXIT_DISK
            name = next_slot_name(root, state, wt_slots)
            path = ops.slot_path(root, name)
            branch_used = branch or f"wave/{item}"
            code, _, out, err = ops.git(root, "rev-parse", "--verify", "--quiet",
                                        branch_used + "^{commit}")
            import time
            t0 = time.monotonic()
            if code == 0:  # ветка есть (круг 2+) — подключить её
                gw = ["worktree", "add", path, branch_used]
            else:
                gw = ["worktree", "add", "-b", branch_used, path, base]
            gcode, _, gout, gerr = ops.git(root, *gw, timeout_s=300)
            prep_ms = int((time.monotonic() - t0) * 1000)
            if gcode != 0:
                json_out({"error": f"git worktree add не вышел (код {gcode}): "
                                   f"{(gerr or gout).strip()}"}, args.json)
                return ops.EXIT_RED
            taken, created = name, True
            wt_slots[taken] = path
            ops.log_event(root, "slot_prep", slot=taken, item=item, branch=branch_used,
                          prep_ms=prep_ms)
        state["slots"][taken] = {"state": "busy", "item": item, "branch": branch_used,
                                 "since": now()}
        save_state(root, state)
    ops.log_event(root, "slot_acquire", slot=taken, item=item,
                   branch=branch_used, created=created, reused=not created)
    json_out({"slot": taken, "path": path, "branch": branch_used, "state": "busy",
              "item": item, "created": created, "reused": not created,
              "prep_ms": prep_ms,
              "target_dir": ops.norm(os.path.join(ops.wt_dir(root), "targets", taken))},
             args.json)
    return ops.EXIT_OK


def cmd_release(root, plan, args):
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        rec = state["slots"].get(args.slot)
        if rec is None:
            json_out({"error": f"слот {args.slot} не в учёте — sync"}, args.json)
            return ops.EXIT_BAD
        if rec.get("state") == "free":
            json_out({"slot": args.slot, "state": "free", "note": "уже свободен"}, args.json)
            return ops.EXIT_OK
        rec.update({"state": "free", "last_item": rec.get("item"), "item": None,
                    "since": now(), "note": args.note or ""})
        save_state(root, state)
    ops.log_event(root, "slot_release", slot=args.slot, item=rec.get("last_item"))
    json_out({"slot": args.slot, "state": "free", "last_item": rec.get("last_item"),
              "note": "кэш targets/%s сохранён для переиспользования" % args.slot}, args.json)
    return ops.EXIT_OK


def cmd_park(root, plan, args):
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        rec = state["slots"].get(args.slot)
        if rec is None:
            json_out({"error": f"слот {args.slot} не в учёте — sync"}, args.json)
            return ops.EXIT_BAD
        rec.update({"state": "parked", "note": args.reason, "since": now()})
        save_state(root, state)
    ops.log_event(root, "slot_park", slot=args.slot, item=rec.get("item"), reason=args.reason)
    json_out({"slot": args.slot, "state": "parked", "reason": args.reason,
              "note": "слот и его кэш НЕ очищаются"}, args.json)
    return ops.EXIT_OK


def cmd_unpark(root, plan, args):
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        rec = state["slots"].get(args.slot)
        if rec is None or rec.get("state") != "parked":
            json_out({"error": f"слот {args.slot} не отложен"}, args.json)
            return ops.EXIT_BAD
        rec.update({"state": "busy", "since": now()})
        save_state(root, state)
    ops.log_event(root, "slot_unpark", slot=args.slot, item=rec.get("item"))
    json_out({"slot": args.slot, "state": "busy", "item": rec.get("item")}, args.json)
    return ops.EXIT_OK


def slot_sort_key(n):
    m = ops.SLOT_RE.match(n)
    return (0, int(m.group(1))) if m else (1, 0)


def cmd_list(root, plan, args):
    state = load_state(root)
    wt_slots = worktree_slots(root)
    rows, busy_items = [], {}
    for name in sorted(set(list(state["slots"]) + list(wt_slots)), key=slot_sort_key):
        rec = state["slots"].get(name, {})
        st = rec.get("state") or "вне учёта (sync)"
        rows.append({"slot": name, "state": st, "item": rec.get("item"),
                     "branch": rec.get("branch"), "note": rec.get("note", ""),
                     "path": wt_slots.get(name) or ops.slot_path(root, name)})
        if st == "busy" and rec.get("item"):
            busy_items.setdefault(rec["item"], []).append(name)
    dups = {i: s for i, s in busy_items.items() if len(s) > 1}
    payload = {"slots": rows, "max_slots": max_slots(root, plan),
               "target_dir_pattern": ops.norm(os.path.join(ops.wt_dir(root), "targets", "<слот>"))}
    if dups:
        payload["conflicts"] = dups
    if not args.json:
        for r in rows:
            print(f"{r['slot']}: {r['state']}" +
                  (f" · {r['item']}" if r["item"] else "") +
                  (f" · {r['branch']}" if r["branch"] else "") +
                  (f" · {r['note']}" if r["note"] else ""))
        if dups:
            print("КОНФЛИКТ: один пункт на нескольких слотах:", dups, file=sys.stderr)
    json_out(payload, args.json)
    if dups:
        return ops.EXIT_RED
    return ops.EXIT_OK


def _findings(root, state, wt_slots):
    """Список несоответствий учёта и диска (для check и sync)."""
    found = []
    for name, rec in state["slots"].items():
        if rec.get("state") in ("busy", "parked") and name not in wt_slots:
            found.append(f"слот {name} числится {rec['state']} (пункт {rec.get('item')}), "
                         f"но копии на диске нет — пункт начнётся заново; убрать из учёта")
    for name in wt_slots:
        if name not in state["slots"]:
            found.append(f"слот {name} есть на диске, но вне учёта (прерывание?)")
    items = {}
    for name, rec in state["slots"].items():
        if rec.get("state") == "busy" and rec.get("item"):
            items.setdefault(rec["item"], []).append(name)
    for item, slots in items.items():
        if len(slots) > 1:
            found.append(f"пункт {item} числится на нескольких слотах: {', '.join(slots)}")
    return found


def cmd_check(root, plan, args):
    state = load_state(root)
    wt_slots = worktree_slots(root)
    found = _findings(root, state, wt_slots)
    for name, rec in state["slots"].items():
        if rec.get("state") == "dirty":
            found.append(f"слот {name} грязен: {rec.get('note')}")
    if found:
        for f in found:
            print("находка:", f)
        json_out({"ok": False, "findings": found}, args.json)
        return ops.EXIT_RED
    json_out({"ok": True, "slots": len(wt_slots)}, args.json)
    if not args.json:
        print("Итог: слоты в порядке")
    return ops.EXIT_OK


def cmd_sync(root, plan, args):
    """Восстановление учёта после прерывания — с отчётом, не догадкой.

    Найденное на диске вне учёта помечается dirty («состояние неизвестно») —
    такой слот не выдаётся автоматически: проверьте руками и сделайте release
    (чист) или park (оставить). Пропавшие копии — из учёта с пометкой.
    """
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        wt_slots = worktree_slots(root)
        report = []
        for name in wt_slots:
            if name not in state["slots"]:
                clean, why = git_slot_clean(wt_slots[name])
                if clean:
                    state["slots"][name] = {"state": "free", "since": now(),
                                            "note": "найден при sync, копия чистая"}
                    report.append(f"слот {name}: найден вне учёта, копия чистая — free")
                else:
                    state["slots"][name] = {"state": "dirty", "since": now(),
                                            "note": "найден при sync: " + why}
                    report.append(f"слот {name}: найден вне учёта, {why} — dirty, "
                                  f"в работу не выдаётся")
        for name in list(state["slots"]):
            if name not in wt_slots:
                rec = state["slots"].pop(name)
                report.append(f"слот {name}: копии нет, из учёта убран "
                              f"(был {rec.get('state')}, пункт {rec.get('item')})")
        save_state(root, state)
        found = _findings(root, state, wt_slots)
    for line in report:
        print("sync:", line)
    for f in found:
        print("находка:", f)
    json_out({"report": report, "findings": found}, args.json)
    return ops.EXIT_OK


def _clean_target(root, slot, only_json=False, quiet=False):
    """Чистка ОДНОГО зарегистрированного кэша неактивного слота."""
    def say(payload):
        if not quiet:
            json_out(payload, only_json)
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        rec = state["slots"].get(slot)
        if rec and rec.get("state") in ("busy", "parked", "dirty"):
            say({"error": f"слот {slot} {rec['state']}"
                 + (f" ({rec.get('note', '')})" if rec.get("note") else "") +
                 " — кэш не трогаем; занятый/отложенный/грязный слот не чистится"})
            return ops.EXIT_RED
        tgt = ops.norm(os.path.join(ops.wt_dir(root), "targets", slot))
        reg = ops.is_registered_target(tgt, root)
        if not reg:
            say({"error": f"{tgt} — не зарегистрированный каталог кэша "
                          "(targets/<слот> основной .wt); не трогаем"})
            return ops.EXIT_BAD
        if not os.path.exists(tgt):
            say({"slot": slot, "target_dir": tgt, "cleaned": False,
                 "note": "кэша нет"})
            return ops.EXIT_OK
        size = ops.dir_size_bytes(tgt)
        import shutil
        shutil.rmtree(tgt)
    ops.log_event(root, "cache_clean", slot=slot, bytes=size)
    say({"slot": slot, "target_dir": tgt, "cleaned": True, "bytes": size,
         "size": ops.human_size(size)})
    return ops.EXIT_OK


def cmd_clean_cache(root, plan, args):
    if args.slot:
        return _clean_target(root, args.slot, args.json)
    # --free: все свободные (не занятые/отложенные/грязные)
    state = load_state(root)
    free = [n for n, r in state["slots"].items() if r.get("state") == "free"]
    if not free:
        json_out({"error": "свободных слотов нет — чистить нечего (занятые и "
                          "отложенные не трогаем)"}, args.json)
        return ops.EXIT_OK
    codes = []
    for n in sorted(free, key=lambda s: int(ops.SLOT_RE.match(s).group(1))):
        codes.append(_clean_target(root, n, args.json))
    return max(codes)


def cmd_remove(root, plan, args):
    with ops.Lock(ops.norm(os.path.join(ops.wt_dir(root), ".slots.lock"))):
        state = load_state(root)
        rec = state["slots"].get(args.slot)
        wt_slots = worktree_slots(root)
        if args.slot not in wt_slots:
            json_out({"error": f"слота {args.slot} нет на диске; проверьте sync"},
                     args.json)
            return ops.EXIT_BAD
        if rec and rec.get("state") in ("busy", "parked", "dirty"):
            json_out({"error": f"слот {args.slot} {rec['state']} — не убирается"
                               + (f" ({rec.get('note', '')})" if rec.get("note") else "")},
                     args.json)
            return ops.EXIT_RED
        code, _, out, err = ops.git(root, "worktree", "remove",
                                    wt_slots[args.slot])
        if code != 0:
            json_out({"error": f"git worktree remove не вышел: {(err or out).strip()}"}, args.json)
            return ops.EXIT_RED
        branch = rec.get("branch") if rec else None
        if branch:
            ops.git(root, "branch", "-d", branch)
        state["slots"].pop(args.slot, None)
        save_state(root, state)
    if not args.keep_cache:
        with_ret = _clean_target(root, args.slot, args.json, quiet=True)
        if with_ret != ops.EXIT_OK:
            return with_ret
    ops.log_event(root, "slot_remove", slot=args.slot, branch=branch,
                  cache_kept=bool(args.keep_cache))
    json_out({"slot": args.slot, "removed": True,
              "cache": "оставлен" if args.keep_cache else "убран"}, args.json)
    return ops.EXIT_OK


def cmd_diagnostics(root, plan, args):
    """Лишние и забытые каталоги сборки — пути и размеры. НИЧЕГО не удаляет."""
    state = load_state(root)
    wt_slots = worktree_slots(root)
    wtd = ops.wt_dir(root)
    lines = []
    targets = ops.norm(os.path.join(wtd, "targets"))
    if os.path.isdir(targets):
        for name in sorted(os.listdir(targets)):
            p = ops.norm(os.path.join(targets, name))
            if not os.path.isdir(p):
                continue
            reg = ops.is_registered_target(p, root)
            rec = state["slots"].get(reg or "", {})
            mark = (f"зарегистрирован · слот {reg or '?'} · {rec.get('state', 'вне учёта')}"
                    if reg else "НЕ зарегистрирован — разобрать руками")
            lines.append((p, ops.dir_size_bytes(p), mark))
    for name, path in sorted(wt_slots.items()):
        for sub in ("target", os.path.join("src-tauri", "target")):
            p = ops.norm(os.path.join(path, sub))
            if os.path.isdir(p):
                lines.append((p, ops.dir_size_bytes(p),
                              "лишний: кэш ВНУТРИ слота (раньше CARGO_TARGET_DIR "
                              "назначался руками) — после release убрать руками"))
    nested = ops.norm(os.path.join(wtd, os.path.basename(wtd)))
    if os.path.isdir(nested):
        lines.append((nested, ops.dir_size_bytes(nested),
                      "ошибочно вложенная .wt — к worktree она не относится, "
                      "разобрать руками"))
    for p in ops.strays_temp_targets():
        lines.append((p, ops.dir_size_bytes(p),
                      "забыт в Temp (ручной CARGO_TARGET_DIR) — убрать руками"))
    for name in wt_slots:
        if name not in state["slots"]:
            lines.append((wt_slots[name], 0, "копия вне учёта — slot_pool.py sync"))
    ok, free, reserve, msg = ops.check_disk(wtd, plan)
    if lines:
        print("Каталоги сборки и слоты (НИЧЕГО не удалено):")
        for p, size, mark in lines:
            print(f"  {p} — {ops.human_size(size)} — {mark}")
    else:
        print("лишних каталогов не найдено")
    print(f"диск: свободно {ops.human_size(free)} · резерв {ops.human_size(reserve)} · "
          + ("ок" if ok else "МАЛО МЕСТА"))
    json_out({"lines": [{"path": p, "bytes": s, "mark": m} for p, s, m in lines],
              "disk_free_bytes": free, "disk_reserve_bytes": reserve,
              "disk_ok": ok, "deleted": 0}, args.json)
    return ops.EXIT_OK


def main(argv=None):
    ops.utf8_stdio()
    ap = argparse.ArgumentParser(add_help=False, description="пул слотов студии")
    ap.add_argument("--root", help="основная папка проекта (по умолчанию — ищется вверх)")
    ap.add_argument("--json", action="store_true", help="только json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--item", required=True, help="имя пункта (p3, trial…)")
    a.add_argument("--branch", help="ветка слота (wave/p3; нет — wave/<item>)")
    a.add_argument("--base", help="база для новой ветки (по умолчанию HEAD — "
                                   "текущая ветка проекта, их одна по «Двум чатам»)")
    a.add_argument("--max-slots", type=int, help="предел пула (по умолчанию pool.max_slots)")
    a.set_defaults(fn=cmd_acquire)
    a = sub.add_parser("release")
    a.add_argument("slot")
    a.add_argument("--note")
    a.set_defaults(fn=cmd_release)
    a = sub.add_parser("park")
    a.add_argument("slot")
    a.add_argument("--reason", required=True)
    a.set_defaults(fn=cmd_park)
    a = sub.add_parser("unpark")
    a.add_argument("slot")
    a.set_defaults(fn=cmd_unpark)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    a = sub.add_parser("clean-cache")
    group = a.add_mutually_exclusive_group(required=True)
    group.add_argument("slot", nargs="?")
    group.add_argument("--free", action="store_true", help="кэши всех свободных слотов")
    a.set_defaults(fn=cmd_clean_cache)
    a = sub.add_parser("diagnostics")
    a.set_defaults(fn=cmd_diagnostics)
    a = sub.add_parser("remove")
    a.add_argument("slot")
    a.add_argument("--keep-cache", action="store_true")
    a.set_defaults(fn=cmd_remove)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) and e.code else ops.EXIT_BAD

    try:
        root = ops.norm(args.root) if args.root else ops.find_root()
        if root is None or not os.path.isdir(root):
            raise ops.OpError(ops.EXIT_BAD, "корень проекта не найден: назовите --root")
        root = ops.main_root(root)  # учёт пульта — всегда у основной папки
        plan = ops.load_plan(root)
        return args.fn(root, plan, args)
    except ops.OpError as e:
        print(f"ошибка: {e.msg}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
