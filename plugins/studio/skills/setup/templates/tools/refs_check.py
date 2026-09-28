#!/usr/bin/env python3
"""Проверить образцы: файлы паспортов на месте, паспорта вида полны, черновики и непринятый свет не держат пункты.

Развёрнут плагином studio (/setup). Только стандартная библиотека. Зовут /need,
/board, /start (перед пунктом [вид] или [ощущение]) и /setup (после разбора
образцов).

    python tools/refs_check.py [--topic <тема>] [--root <папка проекта>]

Ищет в docs/refs/*.md (паспорта тем и INDEX.md; файлы «_…» — шаблоны, их
нет) пути к картинкам и звукам, которых нет на диске, — в ссылках, в `…` и в
ячейках таблиц; имя без папки ищется и в docs/refs/<тема>/. Ещё ищет паспорта
в состоянии «черновик», о теме которых в «Очереди» docs/ROADMAP.md есть пункт
[вид] или [ощущение] (тема узнаётся по пути docs/refs/<тема>.md в пункте или
его подробностях, иначе по названию темы). `--topic` — только эта тема, и её
черновик считается недостачей сам по себе. Папки «_…» (docs/refs/_concept/ —
концепт стиля, вид всей игры: картинка, ТЗ, вырезки тем, лист) — не темы:
паспорт им не нужен, пути к их файлам проверяются как все. Тип
доказательства (и «сказано в ТЗ») на проверку не влияет.

Паспорт `Вид работы: вид` неполон (шаблон v16), пока в нём нет: «Главное
впечатление:» — первого проверяемого утверждения (есть, но не первым
нумерованным пунктом — «не первое»); «Разрыв» — пунктов «у нас … / у образца
…» по нашему кадру той же темы (пункт, перенесённый на несколько строк,
склеивается); «Чем делаем: код | файл | инструмент — решение <дата>». Слово
«совпадает» или «в пределах решения» после «У нас» в «Составляющих» без
состояния «принят кадр» — паспорт заранее «совпадает», разрыва нет (в
описании образца — «тон совпадает с ближними» в столбце «Как у образца» —
не судится); «Образец для листа» целым концепт-кадром
(`_concept/target-<дата>-N`) — на листе нужна вырезка темы того же ракурса.
Наш кадр в шапке «Разрыва» — копия `docs/refs/<тема>/ours-<дата>.png`; путь
вне docs/refs/ (снимок стенда в папке, которую чистят) — история, не
недостача.

Тема света — паспорт вида, в названии которого свет, освещение, облик или
атмосфера (глобальный облик: свет, дымка, тон, палитра): пока его кадр не
«принят», пункт [вид] другой темы с [можно] — недостача: поставить [ждёт
света] (снимает /done приёмкой света). Темы света нет (2D, интерьер) — правило
не действует.

Заметки (на код не влияют): файлы в docs/refs/<тема>/ и docs/refs/_…/, не
записанные ни в паспорт, ни в INDEX.md; пункты [вид] и [ощущение], тема
которых не узнана; пропавшие листы sheet-…, принятые кадры accepted-…, наши
кадры ours-… и звуки вариантов variant-… — это история, работе они не нужны.

Коды возврата: 0 — всё на месте; 1 — чего-то нет; 2 — неверный вызов.
"""
import argparse
import os
import re
import sys
from urllib.parse import unquote

MEDIA = r"(?:png|jpe?g|webp|gif|bmp|tga|wav|ogg|mp3|flac)"  # картинки и звуки образцов
ROUTES = ("[вид]", "[ощущение]")
HISTORY = ("sheet-", "accepted-", "variant-", "ours-")
LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))[^)]*\)")
TICKED = re.compile(r"`([^`\n]+\." + MEDIA + r")`", re.IGNORECASE)
BARE = re.compile(r"(?<![\w./\\-])([\w./\\-]*[-/\\][\w./\\-]*\." + MEDIA + r")(?![\w-])", re.IGNORECASE)
CELL = re.compile(r"`?([^`|<>]+\." + MEDIA + r")`?", re.IGNORECASE)
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s|)>\]`]+", re.IGNORECASE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
STATE = re.compile(r"^[\s>*_-]*Состояние\s*[:：][\s*_]*(.+)$", re.MULTILINE)
KIND = re.compile(r"^[\s>*_-]*Вид работы\s*[:：][\s*_]*(.+)$", re.MULTILINE)
HOW = re.compile(r"^[\s>*_-]*Чем делаем\s*[:：][\s*_]*(.+)$", re.MULTILINE)
TITLE = re.compile(r"^#\s*Образец\s*[:：]\s*(.+)$", re.MULTILINE)
IMPRESSION = re.compile(r"Главное впечатление\s*[:：]\s*(.*)")
MATCHES = re.compile(r"(?<!не )(?<!не\s)(совпада\w*|в пределах решени\w*)", re.IGNORECASE)
CONCEPT_FRAME = re.compile(r"^target-\d{4}-\d{1,2}-\d{1,2}-\d+\.\w+$", re.IGNORECASE)
LIGHT_WORDS = ("свет", "освещ", "light", "облик", "атмосфер")
HOW_WORDS = ("код", "файл", "инструмент")
PLACEHOLDER = re.compile(r"[<>{}*?…|]")
UNFILLED = re.compile(r"[<>{}]")
ENDINGS = "аеёиоуыэюяьй"


def rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def read(path):
    with open(path, encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def media_refs(text):
    """Строки-пути к картинкам и звукам в тексте паспорта, без адресов в сети и заготовок."""
    text = COMMENT.sub(" ", text)
    found = []
    for match in LINK.finditer(text):
        target = unquote((match.group(1) or match.group(2) or "").strip())
        if re.search(r"\." + MEDIA + r"$", target, re.IGNORECASE):
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
    """Пункты [вид] и [ощущение] из «Очереди» ROADMAP.md вместе с их подробностями."""
    path = os.path.join(root, "docs", "ROADMAP.md")
    if not os.path.isfile(path):
        return []
    text = COMMENT.sub(" ", read(path))
    details = {}
    for block in re.split(r"(?m)^(?=#{2,4} )", text):
        head = block.split("\n", 1)[0]
        if head.startswith("####") and any(route in head for route in ROUTES):
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
        route = next((r for r in ROUTES if r in item), None)
        if not route:
            continue
        low = item.lower()
        extra = [block for title, block in details.items() if title in low]
        name = re.search(r"\*\*(.+?)\*\*", item)
        name = re.sub(r"\[[^\]]*\]\s*", "", name.group(1) if name else item).strip(" .")
        marks = " ".join(m for m in re.findall(r"\[[^\]]+\](?!\()", item) if m not in ROUTES)
        result.append({"name": name[:70], "route": route, "marks": marks, "text": "\n".join([item] + extra)})
    return result


def section(text, title):
    """Текст раздела «## <title>» до следующего «## »; нет раздела — пусто."""
    match = re.search(r"(?ms)^##\s*" + re.escape(title) + r"\b.*?$(.*?)(?=^##\s|\Z)", text)
    return match.group(1) if match else ""


def joined_items(block):
    """Пункты раздела: строка с маркером — новый пункт, перенос (любая непустая строка за ним) — его продолжение."""
    items, current = [], None
    for line in block.splitlines():
        if re.match(r"\s*[-*+]\s+", line):
            current = [line.strip()]
            items.append(current)
        elif not line.strip():
            current = None
        elif current is not None:
            current.append(line.strip())
        else:
            current = [line.strip()]
            items.append(current)
    return [" ".join(item) for item in items]


def is_light(passport):
    """Тема света: глобальный облик — свет, дымка, тон, палитра."""
    words = re.findall(r"\w+", (passport["title"] + " " + passport["stem"].replace("-", " ")).lower())
    return passport["kind"] == "вид" and any(w.startswith(LIGHT_WORDS) for w in words)


def passport_gaps(passport):
    """Чего нет в паспорте вида по шаблону v16 — строки для человека."""
    text = COMMENT.sub(" ", passport["text"])
    accepted = passport["state"].lower().startswith("принят")
    found = []
    claims = section(text, "Проверяемые утверждения")
    said = IMPRESSION.search(claims or text)
    if not said or not said.group(1).strip() or UNFILLED.search(said.group(1)):
        found.append("нет «Главного впечатления»")
    else:
        first = re.search(r"(?m)^\s*(?:\d+[.)]|[-*+])\s+(.*)$", claims)  # первый пункт раздела
        if first and not IMPRESSION.search(first.group(1)):
            found.append("«Главное впечатление» не первое утверждение")
    items = joined_items(section(text, "Разрыв"))
    lines = [item for item in items
             if "у нас" in item.lower() and "у образца" in item.lower() and not UNFILLED.search(item)]
    if not lines and not accepted:
        found.append("нет «Разрыва»")
    for line in section(text, "Составляющие").splitlines():
        ours = line.lower().find("у нас")  # судится только наша строка, не описание образца
        word = MATCHES.search(line[ours:]) if ours >= 0 else None
        if word and not accepted:
            found.append(f"«{word.group(1)}» в «Составляющих» без «принят кадр»")
            break
    column = None
    for line in section(text, "Кадры").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if column is None:
            column = next((n for n, cell in enumerate(cells) if "образец для листа" in cell.lower()), None)
            continue
        if column is None or column >= len(cells) or all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        for match in re.finditer(r"[\w./\\-]+\." + MEDIA, cells[column], re.IGNORECASE):
            name = os.path.basename(match.group(0).replace("\\", "/"))
            if CONCEPT_FRAME.match(name):
                frame = cells[0].strip("` ") or "?"
                found.append(f"«Образец для листа» у кадра `{frame}` — целый концепт-кадр {name}")
    how = HOW.search(text)
    value = how.group(1).strip().strip("*_` ").lower() if how else ""
    if not how:
        found.append("нет «Чем делаем»")
    elif "|" in value or not value.startswith(HOW_WORDS):
        found.append("«Чем делаем» не выбрано")
    return found


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
        kind = KIND.search(text)
        kind = kind.group(1).strip().strip("*_` ").lower() if kind else ""
        passports.append({
            "path": path, "stem": stem, "text": text, "index": stem.lower() == "index",
            "title": title.group(1).strip() if title else stem,
            "state": state or "не указано",
            "draft": not state or "|" in state or state.lower().startswith("черновик"),
            "kind": "" if "|" in kind else kind.split()[0] if kind else "",
        })
    light = next((p for p in passports if not p["index"] and is_light(p)), None)

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
        gap_refs = set(media_refs(section(COMMENT.sub(" ", passport["text"]), "Разрыв")))
        for ref in media_refs(passport["text"]):
            full, ok = resolve(root, passport["path"], ref)
            count += 1
            outside = ref in gap_refs and not re.match(r"(?:\./)?docs/refs/", ref.replace("\\", "/"))
            if ok:
                referenced.add(full)
            elif os.path.basename(full).lower().startswith(HISTORY):
                lost.append(f"нет на диске (история, работе не мешает): {rel(root, full)}")
            elif outside:  # наш кадр «Разрыва» снят в папку, которую чистят: текст разрыва важнее снимка
                lost.append(f"нет на диске (наш кадр «Разрыва» вне docs/refs/ — история, работе не мешает): {ref}")
            else:
                missing.append((rel(root, passport["path"]), rel(root, full)))

    items = queue_items(root)
    drafts, linked, gaps = [], set(), []
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
        if passport["kind"] == "вид":
            short = passport_gaps(passport)
            if short:
                gaps.append(f"{rel(root, passport['path'])} — {'; '.join(short)}")

    waits = []
    if light and not light["state"].lower().startswith("принят"):
        for item in items:
            if item["route"] != "[вид]" or "[можно]" not in item["marks"] or mentions(item["text"], light):
                continue
            if args.topic and not any(mentions(item["text"], p) for p in passports):
                continue
            waits.append(f"«{item['name']}» {item['marks']} — поставить [ждёт света]")

    notes = lost

    def loose(folder, note):
        """Картинки и звуки папки, которых нет ни в одной записи (история — не в счёт)."""
        for name in sorted(os.listdir(folder)):
            full = os.path.abspath(os.path.join(folder, name))
            if (re.search(r"\." + MEDIA + r"$", name, re.IGNORECASE)
                    and not name.lower().startswith(HISTORY)
                    and os.path.normcase(full) not in referenced):
                notes.append(f"{note}: {rel(root, full)}")

    for passport in passports:
        folder = os.path.join(refs, passport["stem"])
        if not passport["index"] and os.path.isdir(folder):
            loose(folder, "лежит, но не записано в паспорт")
    # папки «_…» (_concept/ — концепт стиля) — не темы: паспорт им не нужен
    extra = [sub for sub in (sorted(os.listdir(refs)) if os.path.isdir(refs) else [])
             if sub.startswith("_") and os.path.isdir(os.path.join(refs, sub))]
    if not args.topic:
        for sub in extra:
            loose(os.path.join(refs, sub), "лежит, но не записано ни в паспорт, ни в INDEX.md")
        for item in items:
            if id(item) not in linked:
                notes.append(f"пункт {item['route']} «{item['name']}» — тема не узнана: впишите в пункт путь docs/refs/<тема>.md")

    real = [p for p in passports if not p["index"]]
    print(f"Образцы: паспортов {len(real)}, файлов в записях {count}"
          + (f", тема «{real[0]['title']}» — {real[0]['state']}" if args.topic and real else "")
          + (f"; свет — {rel(root, light['path'])}: {light['state']}" if light else "; темы света нет") + ".")
    if not real and not args.topic:  # INDEX.md есть всегда после /setup — считать паспорта тем
        if extra:
            print(f"Паспортов нет — в docs/refs/ только {', '.join(s + '/' for s in extra)}: по темам ещё не разложено.")
        elif not files:
            print("Папки docs/refs/ с паспортами нет — образцов ещё не разбирали.")
    if missing:
        print(f"Нет на диске ({len(missing)}) — положите файл или поправьте путь в паспорте:")
        for where, what in missing:
            print(f"  {where} → {what}")
    if drafts:
        print(f"Черновик — нужно подтверждение владельца ({len(drafts)}):")
        for line in drafts:
            print(f"  {line}")
    if gaps:
        print(f"Паспорт вида неполон ({len(gaps)}) — дописывает `reference` через /idea по шаблону docs/refs/_topic.md: "
              "«Главное впечатление:» — первое утверждение, каким образец видится целиком; «Разрыв» — «у нас … / у "
              "образца …» по нашему кадру темы; «Чем делаем: код | файл | инструмент — решение <дата>»; слово "
              "«совпадает» / «в пределах решения» без «принят кадр» — разрыва нет, писать «У нас: приём: <имя> · <где>» "
              "или «не решено — проверить вариантом …»; «Образец для листа» — вырезка темы того же ракурса "
              "(target-<тема>-N.png, кадр-цель target-N, ref-, owner-), не целый концепт-кадр:")
        for line in gaps:
            print(f"  {line}")
    if waits:
        print(f"Ждёт света ({len(waits)}) — кадр темы света не принят, [вид] других тем не берутся "
              "(снимает /done приёмкой света):")
        for line in waits:
            print(f"  {line}")
    if notes:
        print("Заметки:")
        for line in notes:
            print(f"  {line}")
    short = [(word, len(group)) for word, group in (("файлов", missing), ("подтверждений", drafts),
                                                    ("в паспортах", gaps), ("ждёт света", waits)) if group]
    if short:
        print(f"Итог: не хватает {sum(n for _, n in short)} — {', '.join(f'{w} {n}' for w, n in short)}.")
        return 1
    print("Итог: всё на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
