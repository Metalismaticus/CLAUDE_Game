#!/usr/bin/env python3
"""Проверить дорожную карту: каждая система замысла на своём месте, этапы без дыр и сроков.

Развёрнут плагином studio (/setup). Только стандартная библиотека. Зовут
/roadmap (до показа плана и после записи), /setup, /board и /retro.

    python -X utf8 tools/roadmap_check.py [--root <папка проекта>] [--plan <файл>]

Читает «Этапы» с «Покрытием замысла», «Очередь» и «Сделано» docs/ROADMAP.md
и разделы замысла: заголовки ## брифа docs/CONCEPT.md (до первой черты ---,
кроме «Первая версия» и «Порядок сборки» — они ссылаются на карту), все ##
каждого docs/*CONCEPT*.md и других файлов, названных в столбце «Раздел
замысла» («GAME_CONCEPT.md, Мир»). Файл замысла, о котором в DECISIONS.md
строка «<файл> — архив» или «<файл> — отдельный …», не сверяется. Пустые
разделы не в счёт. `--plan` — «Этапы» и «Покрытие» из черновика
(docs/ROADMAP-PLAN.md) до записи в карту; «Очередь» — из него же, если она
там есть.

Находки: строка «Покрытия» не ровно в одном месте (номер этапа, «Потом» или
«Не делаем») или такого этапа нет; раздел замысла без систем в карте, файл
замысла, не названный ни в одной строке; система требует систему более
позднего этапа, «Потом» или «Не делаем» (работающую — можно), этап зависит
от более позднего; у этапа нет «Что увидит игрок» или «Закрыт, когда»; в
«Этапах» дни или даты (кроме «сделан <дата>»; цитаты «…» и игровое время —
«3 игровых дня», «пережить 3 дня», «раз в 30 дней» — не в счёт); этапов
«идёт» больше одного.

Заметки (на код не влияют): пункт «Очереди» с [этап N], где этап N не
«идёт»; пункт без [этап N]; у этапа не указаны состояние или размер; видимых
этапов (кроме «сделан») больше 7; подраздел ### файла замысла (кроме брифа)
без систем; «Системы» этапа и «Покрытие» расходятся.

Последняя строка — итог «Покрытие X/Y; …»: X — систем на своём месте, Y —
строк «Покрытия» плюс разделов замысла без систем (в каждом не хватает хотя
бы одной). Пункты этапа «идёт» считаются по [этап N] в «Очереди» и «Сделано».

Коды возврата: 0 — всё верно; 1 — есть находки; 2 — нет docs/ROADMAP.md или
этапов в нём (старый проект — не ошибка: команды работают по-старому) или
неверный вызов. Скрипт предупреждает, а не блокирует.
"""
import argparse
import os
import re
import sys

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE = re.compile(r"^\s*-{3,}\s*$")
STAGE = re.compile(r"^Этап\s+(\d+)\b\s*[.:)]?\s*(.*)$", re.IGNORECASE)
SYSTEM = re.compile(r"(?<!\w)[СC]-(\d+)(?!\d)")
LABEL = re.compile(r"\[\s*этап\s+(\d+)\s*\]", re.IGNORECASE)
FILE = re.compile(r"[\w./\\-]+\.md\b", re.IGNORECASE)
FIELDS = ("Зачем", "Что увидит игрок", "Системы", "Зависит от", "Главный риск",
          "Вопросы к этапу", "Закрыт, когда")
FIELD = re.compile(r"^[\s>*_-]*(" + "|".join(re.escape(f) for f in FIELDS)
                   + r")[\s*_]*[:：][\s*_]*(.*)$", re.IGNORECASE)
META = re.compile(r"^(первая версия|порядок сборки|порядок работ)", re.IGNORECASE)
SIZE = re.compile(r"размер\s*[:：]?\s*(малый|средний|большой)", re.IGNORECASE)
MONTHS = (r"(?:январ[яье]|феврал[яье]|марта?|марте|апрел[яье]|ма[йяе]|июн[яье]|июл[яье]|августа?|августе"
          r"|сентябр[яье]|октябр[яье]|ноябр[яье]|декабр[яье])(?!\w)")
DATE = (r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}\.\d{1,2}\.\d{2,4}\b|\b\d{1,2}\s+" + MONTHS
        + r"|\b" + MONTHS + r"\s+\d{4}\b|\b(?:19|20)\d{2}\s*(?:г\.|год\w*)|\b\d\s*квартал\w*|\bQ[1-4]\b")
DATES = re.compile(DATE, re.IGNORECASE)
SPANS = re.compile(
    r"\b\d+(?:[.,]\d+)?(?:\s*[–—-]\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:дн(?:я|ей|и)?\b|день\b|сут(?:ок|ки)?\b|недел\w*|нед\b\.?|месяц\w*|мес\b\.?|лет\b|года?\b)"
    r"|\b(?:за|через|около|примерно|на)\s+(?:пару\s+|несколько\s+|полтора\s+|полторы\s+)?"
    r"(?:дней|недел\w*|месяц\w*|полгода)",
    re.IGNORECASE)
DONE = re.compile(r"сделан\w*\s*[:—–-]?\s*(?:" + DATE + r")", re.IGNORECASE)
QUOTE = re.compile(r"«[^»]*»")
GAME_TIME = re.compile(r"игров\w*|игры|игре|пережить|прожить|выжить|продержаться|раз|кажд\w*", re.IGNORECASE)
RANGE = re.compile(r"(?<!\w)[СC]-(\d+)\s*(?:–|—|-|…|\.\.\.?)\s*[СC]-(\d+)(?!\d)")


def rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def clean(text):
    """Строки без комментариев и блоков кода (там лежат образцы формата)."""
    text = COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            lines.append("")
        else:
            lines.append("" if fenced else line)
    return lines


def heading(line):
    match = HEADING.match(line)
    return (len(match.group(1)), match.group(2).strip()) if match else (0, "")


def under(lines, index):
    """Строки под заголовком до следующего заголовка того же или старшего уровня."""
    level = heading(lines[index])[0]
    for end in range(index + 1, len(lines)):
        found = heading(lines[end])[0]
        if found and found <= level:
            return lines[index + 1:end]
    return lines[index + 1:]


def find(lines, pattern, levels=(2,)):
    for index, line in enumerate(lines):
        level, title = heading(line)
        if level in levels and re.match(pattern, title, re.IGNORECASE):
            return index
    return None


def norm(text):
    text = text.lower().replace("ё", "е").replace("_", " ")
    return " " + re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", text)).strip() + " "


def short(title):
    return re.split(r"\s+[—–-]\s+|[:(.,;]", title, maxsplit=1)[0]


def stage_word(state):
    return "идёт" if state == "идет" else state


def parse_stages(block):
    """Этапы из раздела «Этапы»: номер, имя, состояние, размер, поля."""
    stages, order = {}, []
    for index, line in enumerate(block):
        level, title = heading(line)
        match = STAGE.match(title) if level in (3, 4) else None
        if not match:
            continue
        number, rest = int(match.group(1)), match.group(2)
        state = ""
        for mark in re.findall(r"\[([^\]]*)\]", rest):
            word = mark.strip().lower()
            for known in ("идёт", "идет", "следом", "потом", "сделан"):
                if word.startswith(known):
                    state = stage_word(known)
        size = SIZE.search(rest)
        name = re.sub(r"\[[^\]]*\]", "", rest).split("·")[0].strip(" .—-")
        fields, current = {}, None
        for body in under(block, index):
            field = FIELD.match(body)
            if field:
                current = next(f for f in FIELDS if f.lower() == field.group(1).lower())
                fields[current] = field.group(2).strip()
            elif current and body.strip() and not heading(body)[0]:
                fields[current] = (fields[current] + " " + body.strip()).strip()
            elif not body.strip():
                current = None
        entry = {"n": number, "name": name, "state": state,
                 "size": size.group(1).lower() if size else "", "fields": fields}
        if number in stages:
            entry["duplicate"] = True
        stages.setdefault(number, entry)
        order.append(entry)
    return stages, order


def split_row(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_coverage(lines):
    """Строки таблицы «Покрытие замысла»: номер, система, раздел, слова, требует, этап, состояние."""
    start = find(lines, r"покрытие замысла", levels=(2, 3, 4))
    if start is None:
        return None
    rows = [split_row(line) for line in under(lines, start) if line.strip().startswith("|")]
    keys = {"№": 0, "система": 1, "раздел": 2, "слова": 3, "требует": 4, "этап": 5, "состояние": 6}
    body = []
    for cells in rows:
        low = [re.sub(r"[`*_]", "", cell).strip().lower() for cell in cells]
        if "система" in low and any(cell.startswith("этап") for cell in low):
            for key in keys:
                for position, cell in enumerate(low):
                    if cell.startswith(key) or (key == "№" and cell in ("#", "n", "no", "номер")):
                        keys[key] = position
                        break
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        body.append(cells)
    result = []
    for cells in body:
        def cell(key):
            position = keys[key]
            return cells[position] if position < len(cells) else ""
        ident = SYSTEM.search(cell("№")) or SYSTEM.search(" ".join(cells[:2]))
        result.append({
            "id": int(ident.group(1)) if ident else None,
            "label": f"С-{ident.group(1)}" if ident else "строка без номера",
            "name": re.sub(r"[`*_]", "", cell("система")).strip(),
            "section": cell("раздел"),
            "requires": [int(m.group(1)) for m in SYSTEM.finditer(cell("требует"))],
            "place": cell("этап"),
            "works": re.match(r"работает", re.sub(r"[`*_\s]+", " ", cell("состояние")).strip().lower()) is not None,
            "free_at": (keys["слова"], keys["требует"]),
        })
    return result


def place(text, stages):
    """(вид, номер) места системы или (None, почему не так)."""
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]|[`*_]", " ", text).strip().lower().replace("ё", "е")
    value = re.sub(r"\s+", " ", value).strip(" .")
    if value in ("", "—", "–", "-", "?", "??", "нет"):
        return None, "нет места — нужен ровно один: номер этапа, «Потом» или «Не делаем»"
    match = re.fullmatch(r"(?:этап\s*)?(\d+)", value)
    if match:
        number = int(match.group(1))
        if number not in stages:
            return None, f"этап {number} — такого этапа в «Этапах» нет"
        return "stage", number
    if value == "потом":
        return "later", None
    if value == "не делаем":
        return "no", None
    return None, f"«{text.strip()}» — не ровно одно место: один этап, «Потом» или «Не делаем»"


def concept_titles(path, brief, depth=2):
    """Заголовки ## (или ###) замысла с непустым текстом; у брифа CONCEPT.md — до первой черты."""
    lines = clean(read(path))
    titles, seen, parent = [], False, ""
    for index, line in enumerate(lines):
        level, title = heading(line)
        if brief and seen and (RULE.match(line) or level == 1):
            break
        if level == 2:
            seen, parent = True, title
        if level != depth or (depth > 2 and META.match(parent)):
            continue
        body = []
        for text in under(lines, index):
            if RULE.match(text):
                break
            body.append(text)
        filled = re.sub(r"\{\{.*?\}\}", "", "\n".join(body), flags=re.DOTALL).strip()
        if filled and not META.match(title):
            titles.append(title)
    return titles


def covered(titles, cells):
    """Какие заголовки названы в ячейках; длинное совпадение вырезается раньше короткого."""
    variants = []
    for title in titles:
        variants.append((norm(title), title))
        part = norm(short(title.lower()))
        if part.strip() and len(part.strip()) >= 3 and part != norm(title):
            variants.append((part, title))
    variants.sort(key=lambda item: -len(item[0]))
    hit = set()
    for text in cells:
        text = norm(FILE.sub(" ", text))
        for variant, title in variants:
            if variant in text:
                hit.add(title)
                text = text.replace(variant, " ", 1)
    return hit


def resolve(root, name):
    name = name.replace("\\", "/")
    for candidate in (os.path.join(root, "docs", name), os.path.join(root, name)):
        if os.path.isfile(candidate):
            return candidate
    return None


def queue_items(lines, section):
    """Пункты раздела-списка («Очередь», «Сделано») с продолжениями строк."""
    start = find(lines, section)
    if start is None:
        return None
    items, current = [], None
    for line in under(lines, start):
        if re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line):
            current = [line.strip()]
            items.append(current)
        elif current is not None and line.strip() and line[:1].isspace():
            current.append(line.strip())
        else:
            current = None
    result = []
    for parts in items:
        text = " ".join(parts)
        name = re.search(r"\*\*(.+?)\*\*", text)
        name = re.sub(r"\[[^\]]*\]\s*", "", name.group(1) if name else re.sub(r"^\W+", "", text)).strip(" .")
        result.append({"text": text, "name": name[:60], "labels": [int(m) for m in LABEL.findall(text)]})
    return result


def spans(text):
    text = QUOTE.sub(" ", DONE.sub(" ", text))
    found = [m.group(0).strip() for m in DATES.finditer(text)]
    for match in SPANS.finditer(text):
        near = re.findall(r"\w+", text[:match.start()])[-2:] + re.findall(r"\w+", text[match.end():])[:2]
        if not any(GAME_TIME.fullmatch(word) for word in near):  # игровое время — не срок работы
            found.append(match.group(0).strip())
    return found


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="папка проекта; по умолчанию текущая")
    parser.add_argument("--plan", help="черновик карты (docs/ROADMAP-PLAN.md): проверить его «Этапы» до записи")
    args = parser.parse_args()

    root = os.path.abspath(args.root or os.getcwd())
    if not args.root and not os.path.isdir(os.path.join(root, "docs")):
        beside = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if os.path.isdir(os.path.join(beside, "docs")):
            root = beside
    if args.root and not os.path.isdir(root):
        print(f"нет папки проекта: {root}", file=sys.stderr)
        return 2
    roadmap = os.path.join(root, "docs", "ROADMAP.md")
    if args.plan:
        source = args.plan if os.path.isabs(args.plan) else os.path.join(root, args.plan)
        if not os.path.isfile(source):
            print(f"Нет черновика {args.plan} — проверять нечего.")
            return 2
    elif not os.path.isfile(roadmap):
        print("Нет docs/ROADMAP.md — этапов нет: проект не развёрнут (/setup).")
        return 2
    else:
        source = roadmap
    lines = clean(read(source))

    start = find(lines, r"этапы\b")
    stages, order = parse_stages(under(lines, start)) if start is not None else ({}, [])
    rows = parse_coverage(lines)
    if not order and not rows:
        where = rel(root, source)
        print(f"В {where} этапов нет — карта ведётся по-старому, без «Этапов»; "
              "разложить замысел на этапы — /roadmap в чате замысла.")
        return 2
    rows = rows or []

    findings, notes = [], []
    if not rows:
        findings.append("нет таблицы «Покрытие замысла»: в какой этап какая система — не записано")

    # Место каждой системы: ровно один этап, «Потом» или «Не делаем».
    by_id, placed = {}, 0
    for row in rows:
        kind, value = place(row["place"], stages)
        row["kind"], row["n"] = kind, value
        title = f"{row['label']} «{row['name']}»" if row["name"] else row["label"]
        row["title"] = title
        if kind:
            placed += 1
        else:
            findings.append(f"{title}: {value}")
        if row["id"] is not None:
            if row["id"] in by_id:
                findings.append(f"{row['label']}: две строки в «Покрытии» — система стоит в одном месте")
            by_id.setdefault(row["id"], row)

    # «Требует» и «Зависит от» не смотрят вперёд.
    for row in rows:
        if row["kind"] in (None, "no"):
            continue
        for need in row["requires"]:
            if need == row["id"]:
                continue
            other = by_id.get(need)
            where = f"этап {row['n']}" if row["kind"] == "stage" else "«Потом»"
            if other is None:
                findings.append(f"{row['title']} ({where}) требует С-{need:02d} — такой системы в «Покрытии» нет")
            elif other["works"] or other["kind"] is None:
                continue
            elif other["kind"] == "no":
                findings.append(f"{row['title']} ({where}) требует {other['title']} — она в «Не делаем»")
            elif row["kind"] == "stage" and other["kind"] == "later":
                findings.append(f"{row['title']} (этап {row['n']}) требует {other['title']} — она в «Потом»")
            elif row["kind"] == "stage" and other["kind"] == "stage" and other["n"] > row["n"]:
                findings.append(f"зависимость вперёд: {row['title']} (этап {row['n']}) "
                                f"требует {other['title']} (этап {other['n']})")
    for stage in order:
        depends = stage["fields"].get("Зависит от", "")
        numbers = set()
        for group in re.findall(r"этап\w*\s+(\d+(?:\s*(?:,|и|–|—|-)\s*\d+)*)", depends, re.IGNORECASE):
            numbers.update(int(n) for n in re.findall(r"\d+", group))
        for number in sorted(numbers):
            if number > stage["n"]:
                findings.append(f"Этап {stage['n']} зависит от Этапа {number} — этап позже")
            elif number not in stages:
                findings.append(f"Этап {stage['n']} зависит от Этапа {number} — такого этапа нет")

    # Поля этапа, сроки, «идёт» — не больше одного.
    for stage in order:
        label = f"Этап {stage['n']}" + (f" «{stage['name']}»" if stage["name"] else "")
        if stage.get("duplicate"):
            findings.append(f"{label}: номер этапа повторяется")
        for field in ("Что увидит игрок", "Закрыт, когда"):
            value = stage["fields"].get(field, "")
            if not re.sub(r"<[^>]*>|[\s.…—-]", "", value):
                findings.append(f"{label}: нет «{field}»")
        if not stage["state"]:
            notes.append(f"{label}: не указано состояние — [идёт], [следом], [потом] или [сделан <дата>]")
        if not stage["size"]:
            notes.append(f"{label}: не указан размер — малый, средний или большой")
    running = [stage for stage in order if stage["state"] == "идёт"]
    if len(running) > 1:
        findings.append("этапов «идёт» больше одного: " + ", ".join(f"Этап {s['n']}" for s in running)
                        + " — идёт только один, остальные «следом» или «потом»")
    visible = [stage for stage in order if stage["state"] != "сделан"]
    if len(visible) > 7:
        notes.append(f"видимых этапов (кроме «сделан») {len(visible)} — больше 7: дальние свернуть в строку "
                     "«Потом: …», их системы в «Покрытии» — «Потом»")

    # «Системы» этапа и «Покрытие» говорят одно.
    for stage in order:
        text = stage["fields"].get("Системы", "")
        named = {int(m.group(1)) for m in SYSTEM.finditer(text)}
        for first, last in RANGE.findall(text):
            named.update(range(int(first), int(last) + 1))
        stage["systems"] = named
        for ident in sorted(named):
            row = by_id.get(ident)
            if row is None:
                notes.append(f"Этап {stage['n']}: «Системы» называет С-{ident:02d} — её нет в «Покрытии»")
            elif row["kind"] == "stage" and row["n"] != stage["n"]:
                notes.append(f"Этап {stage['n']}: «Системы» называет {row['title']}, "
                             f"а в «Покрытии» она в этапе {row['n']}")
            elif row["kind"] in ("later", "no"):
                where = "«Потом»" if row["kind"] == "later" else "«Не делаем»"
                notes.append(f"Этап {stage['n']}: «Системы» называет {row['title']}, а в «Покрытии» она в {where}")
    for row in rows:
        stage = stages.get(row["n"]) if row["kind"] == "stage" else None
        if stage and stage["systems"] and not row["works"] and row["id"] not in stage["systems"]:
            notes.append(f"{row['title']}: в «Покрытии» этап {row['n']}, а в «Системы» этапа {row['n']} её нет")

    dated = []
    block = under(lines, start) if start is not None else []
    table_start = find(block, r"покрытие замысла", levels=(2, 3, 4))
    free = rows[0]["free_at"] if rows else ()
    current = "шапка «Этапов»"
    for index, line in enumerate(block):
        level, title = heading(line)
        if level:
            match = STAGE.match(title)
            current = f"Этап {match.group(1)}" if match else f"«{title}»"
        text = line
        if table_start is not None and index > table_start and line.strip().startswith("|"):
            cells = split_row(line)
            for position in free:  # слова владельца и ссылки «Требует» на решения с датой — не сроки
                if position < len(cells):
                    cells[position] = ""
            text = " | ".join(cells)
        for found in spans(text):
            dated.append(f"срок в «Этапах»: «{found}» ({current}) — пишется только размер: "
                         "малый, средний или большой")

    # Разделы замысла, у которых нет ни одной системы в карте.
    docs = os.path.join(root, "docs")
    concept = os.path.join(docs, "CONCEPT.md")
    decisions = os.path.join(docs, "DECISIONS.md")
    decided = read(decisions).lower().replace("`", "") if os.path.isfile(decisions) else ""
    files, missing = {}, set()
    if os.path.isfile(concept):
        files["concept.md"] = (concept, True)
    else:
        notes.append("нет docs/CONCEPT.md — разделы замысла не сверены")
    for entry in sorted(os.listdir(docs)) if os.path.isdir(docs) else []:
        key = entry.lower()
        if key == "concept.md" or "concept" not in key or not key.endswith(".md"):
            continue
        if re.search(re.escape(key) + r"\s*[—–:-]+\s*(?:архив|отдельн)", decided):
            continue  # в DECISIONS.md: архив или отдельный замысел
        files[key] = (os.path.join(docs, entry), False)
    named_files = set()
    for row in rows:
        for name in FILE.findall(row["section"]):
            key = os.path.basename(name.replace("\\", "/")).lower()
            named_files.add(key)
            if key in files or key == "concept.md":
                continue
            path = resolve(root, name)
            if path:
                files[key] = (path, False)
            elif key not in missing:
                missing.add(key)
                notes.append(f"«Раздел замысла» называет {name} — такого файла нет")
    uncovered, gaps = [], 0
    for key, (path, brief) in files.items():
        titles = concept_titles(path, brief)
        if not brief and key not in named_files:
            if titles:
                gaps += len(titles)
                uncovered.append(f"замысел {rel(root, path)}: ни одной системы в карте — в «Разделе замысла» "
                                 f"не назван ни разу (с именем файла: `{os.path.basename(path)}, <раздел>`)")
            continue
        cells = []
        for row in rows:
            named = [os.path.basename(n.replace("\\", "/")).lower() for n in FILE.findall(row["section"])]
            if key in named or (key == "concept.md" and not named):
                cells.append(row["section"])
        hit = covered(titles, cells)
        gaps += sum(1 for t in titles if t not in hit)
        uncovered += [f"раздел без систем в карте: {rel(root, path)}, «{t}»" for t in titles if t not in hit]
        if not brief:
            subs = concept_titles(path, brief, depth=3)
            hit = covered(subs, cells)
            notes += [f"подраздел без систем в карте: {rel(root, path)}, «{t}»" for t in subs if t not in hit]
    findings += uncovered + dated

    # Очередь: в ней только этап «идёт», у каждого пункта — [этап N].
    queue = queue_items(lines, r"очередь\b")
    if queue is None and not args.plan:
        notes.append("нет раздела «Очередь»")
    now = running[0] if running else None
    in_queue = 0
    for item in queue or []:
        if not item["labels"]:
            notes.append(f"пункт очереди без [этап N]: «{item['name']}»")
            continue
        for number in item["labels"]:
            stage = stages.get(number)
            if now and number == now["n"]:
                in_queue += 1
            elif stage is None:
                notes.append(f"«{item['name']}» — [этап {number}]: такого этапа нет")
            elif stage["state"] != "идёт":
                state = stage["state"] or "без состояния"
                notes.append(f"«{item['name']}» — [этап {number}], а этап {number} «{state}»: "
                             "в очереди — только этап «идёт»")
    done_items = queue_items(clean(read(roadmap)), r"сделано\b") if os.path.isfile(roadmap) else None
    done = sum(1 for item in done_items or [] if now and now["n"] in item["labels"])

    # Вывод.
    if args.plan:
        print(f"Черновик: {rel(root, source)}.")
    following = [s for s in order if s["state"] == "следом"]
    head = f"Этапы: {len(order)}"
    if now:
        head += (f" — идёт Этап {now['n']}" + (f" «{now['name']}»" if now["name"] else "")
                 + (f" (пунктов в очереди {in_queue}, сделано {done})" if queue is not None else ""))
    else:
        head += " — ни один не «идёт»"
    if following:
        head += f", следом Этап {following[0]['n']}"
    print(head + ".")
    kinds = {"stage": 0, "later": 0, "no": 0}
    for row in rows:
        if row["kind"]:
            kinds[row["kind"]] += 1
    works = sum(1 for row in rows if row["works"])
    print(f"Покрытие замысла: систем {len(rows)} — в этапах {kinds['stage']}, «Потом» {kinds['later']}, "
          f"«Не делаем» {kinds['no']}, без места {len(rows) - placed}; работает {works}"
          + (f"; разделов замысла без систем {gaps}" if gaps else "") + ".")
    if findings:
        print(f"Находки ({len(findings)}):")
        for line in findings:
            print(f"  {line}")
    if notes:
        print(f"Заметки ({len(notes)}):")
        for line in notes:
            print(f"  {line}")
    print(f"Покрытие {placed}/{len(rows) + gaps}; этапов {len(order)}, идёт — "
          + (f"Этап {now['n']}" if now else "нет") + f"; находок {len(findings)}, заметок {len(notes)}.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
