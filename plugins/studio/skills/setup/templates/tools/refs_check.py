#!/usr/bin/env python3
"""Проверить образцы вида: картинки из паспортов на месте, черновики не держат пункты [вид].

Развёрнут плагином studio (/setup). Только стандартная библиотека. Зовут /need,
/board, /start (перед пунктом [вид]) и /setup (после разбора образцов).

    python tools/refs_check.py [--topic <тема>] [--root <папка проекта>]

Ищет в docs/refs/*.md (паспорта тем и INDEX.md; файлы «_…» — шаблоны, их
нет) пути к картинкам, которых нет на диске, — в ссылках, в `…` и в ячейках
таблиц; имя без папки ищется и в docs/refs/<тема>/. Ещё ищет паспорта в
состоянии «черновик», о теме которых в «Очереди» docs/ROADMAP.md есть пункт
[вид] (тема узнаётся по пути docs/refs/<тема>.md в пункте или его подробностях,
иначе по названию темы). `--topic` — только эта тема, и её черновик считается
недостачей сам по себе.

Заметки (на код не влияют): картинки в docs/refs/<тема>/, не записанные в
паспорт; пункты [вид], тема которых не узнана; пропавшие листы sheet-… и
принятые кадры accepted-… — это история, работе они не нужны.

Коды возврата: 0 — всё на месте; 1 — чего-то нет; 2 — неверный вызов.
"""
import argparse
import os
import re
import sys
from urllib.parse import unquote

IMAGE = r"(?:png|jpe?g|webp|gif|bmp|tga)"
LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))[^)]*\)")
TICKED = re.compile(r"`([^`\n]+\." + IMAGE + r")`", re.IGNORECASE)
BARE = re.compile(r"(?<![\w./\\-])([\w./\\-]*[-/\\][\w./\\-]*\." + IMAGE + r")(?![\w-])", re.IGNORECASE)
CELL = re.compile(r"`?([^`|<>]+\." + IMAGE + r")`?", re.IGNORECASE)
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s|)>\]`]+", re.IGNORECASE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
STATE = re.compile(r"^[\s>*_-]*Состояние\s*[:：][\s*_]*(.+)$", re.MULTILINE)
TITLE = re.compile(r"^#\s*Образец\s*[:：]\s*(.+)$", re.MULTILINE)
PLACEHOLDER = re.compile(r"[<>{}*?…|]")
ENDINGS = "аеёиоуыэюяьй"


def rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def image_refs(text):
    """Строки-пути к картинкам в тексте паспорта, без адресов в сети и заготовок."""
    text = COMMENT.sub(" ", text)
    found = []
    for match in LINK.finditer(text):
        target = unquote((match.group(1) or match.group(2) or "").strip())
        if re.search(r"\." + IMAGE + r"$", target, re.IGNORECASE):
            found.append(target)
    found += [m.group(1).strip() for m in TICKED.finditer(text)]
    rest = []
    for line in LINK.sub(" ", TICKED.sub(" ", text)).splitlines():
        if line.lstrip().startswith("|"):  # ячейка таблицы целиком — имя файла, даже с пробелами
            cells = line.strip().strip("|").split("|")
            for n, cell in enumerate(cells):
                match = CELL.fullmatch(cell.strip())
                if match and "://" not in cell:
                    found.append(match.group(1).strip())
                    cells[n] = " "
            line = "|".join(cells)
        rest.append(line)
    found += [m.group(1) for m in BARE.finditer(URL.sub(" ", "\n".join(rest)))]
    result = []
    for ref in found:
        if URL.match(ref) or PLACEHOLDER.search(ref) or ref in result:
            continue
        result.append(ref)
    return result


def resolve(root, md_path, ref):
    """Первый существующий вариант пути: от проекта, от паспорта, из папки темы."""
    ref = ref.replace("\\", "/")
    if ref.startswith("./"):
        ref = ref[2:]
    folder = os.path.dirname(md_path)
    stem = os.path.splitext(os.path.basename(md_path))[0]
    beside = os.path.join(folder, stem, ref)  # имя без папки в паспорте темы — из её папки
    candidates = [os.path.join(root, ref), os.path.join(folder, ref)]
    if "/" not in ref and stem.lower() != "index":
        candidates.append(beside)
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.normcase(os.path.abspath(candidate)), True
    if ref.startswith("docs/"):
        guess = candidates[0]
    elif "/" in ref or stem.lower() == "index":
        guess = candidates[1]
    else:
        guess = beside
    return os.path.abspath(guess), False


def stems(name):
    """Основы слов названия темы: «деревья» → «дерев», «небо и свет» → «неб», «свет»."""
    result = []
    for word in re.findall(r"\w+", name.lower()):
        if len(word) < 3:
            continue
        stem = word
        for _ in range(2):
            if len(stem) > 3 and stem[-1] in ENDINGS:
                stem = stem[:-1]
        result.append(stem)
    return result


def mentions(text, passport):
    low = text.lower().replace("\\", "/")
    stem = passport["stem"].lower()
    if f"refs/{stem}.md" in low or f"refs/{stem}/" in low:
        return True
    for name in (passport["title"], passport["stem"].replace("-", " ").replace("_", " ")):
        words = stems(name)
        if words and all(re.search(r"(?<!\w)" + re.escape(w) + r"\w{0,4}(?!\w)", low) for w in words):
            return True
    return False


def queue_items(root):
    """Пункты [вид] из «Очереди» ROADMAP.md вместе с их подробностями."""
    path = os.path.join(root, "docs", "ROADMAP.md")
    if not os.path.isfile(path):
        return []
    text = COMMENT.sub(" ", read(path))
    details = {}
    for block in re.split(r"(?m)^(?=#{2,4} )", text):
        head = block.split("\n", 1)[0]
        if head.startswith("####") and "[вид]" in head:
            title = re.sub(r"\[[^\]]*\]", "", head.lstrip("#")).strip(" .").lower()
            if title:
                details[title] = block
    queue = re.search(r"(?ms)^## Очередь\s*$(.*?)(?=^## )", text + "\n## конец\n")
    items, current = [], None
    for line in (queue.group(1) if queue else "").splitlines():
        if re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line):
            current = [line.strip()]
            items.append(current)
        elif current is not None and line.strip() and line[:1].isspace():
            current.append(line.strip())
        else:
            current = None
    result = []
    for lines in items:
        item = " ".join(lines)
        if "[вид]" not in item:
            continue
        low = item.lower()
        extra = [block for title, block in details.items() if title in low]
        name = re.search(r"\*\*(.+?)\*\*", item)
        name = re.sub(r"\[[^\]]*\]\s*", "", name.group(1) if name else item).strip(" .")
        marks = " ".join(m for m in re.findall(r"\[[^\]]+\](?!\()", item) if m != "[вид]")
        result.append({"name": name[:70], "marks": marks, "text": "\n".join([item] + extra)})
    return result


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--topic", help="только эта тема: имя паспорта без .md или его название")
    parser.add_argument("--root", help="папка проекта; по умолчанию текущая")
    args = parser.parse_args()

    root = os.path.abspath(args.root or os.getcwd())
    if not args.root and not os.path.isdir(os.path.join(root, "docs")):
        beside = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isdir(os.path.join(beside, "docs")):
            root = beside
    if args.root and not os.path.isdir(root):
        print(f"нет папки проекта: {root}", file=sys.stderr)
        return 2
    refs = os.path.join(root, "docs", "refs")

    files = sorted(f for f in os.listdir(refs) if f.lower().endswith(".md") and not f.startswith("_")) \
        if os.path.isdir(refs) else []
    passports = []
    for name in files:
        path = os.path.join(refs, name)
        text = read(path)
        stem = os.path.splitext(name)[0]
        state = STATE.search(text)
        state = state.group(1).strip().strip("*_` ") if state else ""
        title = TITLE.search(text)
        passports.append({
            "path": path, "stem": stem, "text": text, "index": stem.lower() == "index",
            "title": title.group(1).strip() if title else stem,
            "state": state or "не указано",
            "draft": not state or "|" in state or state.lower().startswith("черновик"),
        })

    if args.topic:
        want = args.topic.strip().replace("\\", "/").split("/")[-1]
        want = want[:-3] if want.lower().endswith(".md") else want
        chosen = [p for p in passports if not p["index"]
                  and want.lower() in (p["stem"].lower(), p["title"].lower())]
        if not chosen:
            print(f"Нет паспорта темы «{args.topic}»: docs/refs/{want}.md — образец ещё не разобран.")
            print("Итог: не хватает 1 — паспорта.")
            return 1
        passports = chosen

    missing, lost, referenced = [], [], set()
    count = 0
    for passport in passports:
        for ref in image_refs(passport["text"]):
            full, ok = resolve(root, passport["path"], ref)
            count += 1
            if ok:
                referenced.add(full)
            elif os.path.basename(full).lower().startswith(("sheet-", "accepted-")):
                lost.append(f"нет на диске (история, работе не мешает): {rel(root, full)}")
            else:
                missing.append((rel(root, passport["path"]), rel(root, full)))

    items = queue_items(root)
    drafts, linked = [], set()
    for passport in passports:
        if passport["index"]:
            continue
        hits = [item for item in items if mentions(item["text"], passport)]
        linked.update(id(item) for item in hits)
        if passport["draft"] and (hits or args.topic):
            where = rel(root, passport["path"])
            if not hits:
                drafts.append(f"{where} — черновик: владелец ещё не подтвердил, что в образце главное")
            for item in hits:
                line = f"{where} — «{item['name']}» {item['marks']}".rstrip()
                if "ждёт" not in item["marks"]:
                    line += " — пункт можно брать, а образец не подтверждён: поставить [ждёт образца]"
                drafts.append(line)

    notes = lost
    for passport in passports:
        folder = os.path.join(refs, passport["stem"])
        if passport["index"] or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            full = os.path.abspath(os.path.join(folder, name))
            if (re.search(r"\." + IMAGE + r"$", name, re.IGNORECASE)
                    and not name.lower().startswith(("sheet-", "accepted-"))
                    and os.path.normcase(full) not in referenced):
                notes.append(f"лежит, но не записано в паспорт: {rel(root, full)}")
    if not args.topic:
        for item in items:
            if id(item) not in linked:
                notes.append(f"пункт [вид] «{item['name']}» — тема не узнана: впишите в пункт путь docs/refs/<тема>.md")

    real = [p for p in passports if not p["index"]]
    print(f"Образцы: паспортов {len(real)}, картинок в записях {count}"
          + (f", тема «{real[0]['title']}» — {real[0]['state']}" if args.topic and real else "") + ".")
    if not files and not args.topic:
        print("Папки docs/refs/ с паспортами нет — образцов ещё не разбирали.")
    if missing:
        print(f"Нет на диске ({len(missing)}) — положите файл или поправьте путь в паспорте:")
        for where, what in missing:
            print(f"  {where} → {what}")
    if drafts:
        print(f"Черновик — нужно подтверждение владельца ({len(drafts)}):")
        for line in drafts:
            print(f"  {line}")
    if notes:
        print("Заметки:")
        for line in notes:
            print(f"  {line}")
    if missing or drafts:
        parts = [f"{word} {n}" for word, n in (("картинок", len(missing)), ("подтверждений", len(drafts))) if n]
        print(f"Итог: не хватает {len(missing) + len(drafts)} — {', '.join(parts)}.")
        return 1
    print("Итог: всё на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
