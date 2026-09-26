#!/usr/bin/env python3
"""Самопроверка хуков studio: python -X utf8 <плагин>/hooks/selftest.py

Подаёт guard_git.py и session_context.py синтетические события — с
кириллической папкой проекта и путями с пробелами — и сверяет ответ; так же
прогоняет шаблонные tools/roadmap_check.py на пробных картах и
tools/code_check.py на пробном репозитории (коды 0/1/2) и сверяет, что
refs_check.py и roadmap_check.py видят в одной «Очереди» одни и те же пункты
(копии разбора — по файлу на скрипт, стережёт их эта проверка); look_sheet.py
--sanity — на пробных кадрах (серый, чёрный, копия с шумом; без Pillow —
пропуск с пометкой). Проект не
трогает: всё создаётся во временной папке и удаляется. Печатает таблицу и
итог; код 0 — всё верно, 1 — нет. Зовут /setup — полностью и /board — с
--quick: без пробных репозиториев code_check (секунды вместо ~40 с).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "guard_git.py")
CONTEXT = os.path.join(HERE, "session_context.py")
TOOLS = os.path.join(HERE, "..", "skills", "setup", "templates", "tools")
ROADMAP_CHECK = os.path.join(TOOLS, "roadmap_check.py")
CODE_CHECK = os.path.join(TOOLS, "code_check.py")
LOOK_SHEET = os.path.join(TOOLS, "look_sheet.py")

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


QUEUE = """# Дорожная карта

## Очередь

Порядок работы — сверху вниз.

- **[можно] [этап 1] [вид] Водопад: полотно по скале.** См. «Подробности», 46. Образец — `docs/refs/waterfall.md`.
- **[можно] [этап 1] [код] Стенд кадров водопада.** См. «Подробности», 45.
  Продолжение пункта: кадры для [ощущение] прыжка.
* **[ждёт образца] [этап 1] [ощущение] Прыжок: высота и отдача.**
1. **[можно] [этап 1] [вид] Трава: пучки по колено.**
<!-- - **[можно] [вид] Образец в комментарии.** -->
- **[можно] [этап 1] [баг] Ели на песке.** См. `BUGS.md`.

## Дальше

### Подробности ближайших пунктов
"""


def module(name):
    """Скрипт tools/ модулем — без __pycache__ в папке плагина."""
    spec = importlib.util.spec_from_file_location("selftest_" + name, os.path.join(TOOLS, name + ".py"))
    loaded, was = importlib.util.module_from_spec(spec), sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(loaded)
    finally:
        sys.dont_write_bytecode = was
    return loaded


def check_parity(results, tmp):
    """refs_check.py и roadmap_check.py разбирают «Очередь» своими копиями — пункты должны совпасть."""
    root = os.path.join(tmp, "паритет очереди")
    write(os.path.join(root, "docs", "ROADMAP.md"), QUEUE)
    try:
        refs, roadmap = module("refs_check"), module("roadmap_check")
        seen = [(i["name"], i["text"].split("\n")[0]) for i in refs.queue_items(root)]
        lines = roadmap.clean(roadmap.read(os.path.join(root, "docs", "ROADMAP.md")))
        mine = [(i["name"], i["text"]) for i in roadmap.queue_items(lines, r"очередь\b")
                if "[вид]" in i["text"] or "[ощущение]" in i["text"]]
        ok, got = seen == mine and len(seen) == 4, f"{len(seen)} и {len(mine)}" + ("" if seen == mine else ", разные")
    except Exception as e:  # noqa: BLE001 — любая поломка разбора = провал
        ok, got = False, f"ошибка: {e}"
    results.append(("паритет очереди", "refs_check и roadmap_check", "4 и 4", got, ok))


def gd_vars(prefix, start, count):
    return "".join(f"var {prefix}_{n}: int = {n}\n" for n in range(start, start + count))


LONG = "func mix(seed: int) -> int:\n\tvar total: int = seed\n" + "".join(
    f"\ttotal += {n} * {n + 7}\n" for n in range(107)) + "\treturn total\n"
TWIN = "extends RefCounted\n\n" + LONG.replace("+=", "-=")  # вторая mix() в базе: одноимённые переезжают разом
STEPS = "".join(f"\tvar step_{k}: int = seed * {k + 3} + {k * 11}\n" for k in range(10))
UTIL = "extends RefCounted\n\nfunc spread(seed: int) -> int:\n" + STEPS + "\treturn step_9\n"
PACE = "".join(f"\tvar pace_{k}: int = seed + {k * 5}\n" for k in range(10))  # кусок в двух функциях — повтор базы
WALK = "\nfunc walk(seed: int) -> int:\n" + PACE + "\treturn pace_9\n"
RUN = "\nfunc run(seed: int) -> int:\n" + PACE + "\treturn pace_8\n"
BIG = "extends RefCounted\n" + gd_vars("big", 0, 1100)
LOOSE = "func blend(a, b):\n\tvar mid = a + b\n\treturn mid\n"


def helper(name, k):
    return f"func {name}(seed: int) -> int:\n" + "".join(f"\tvar {name}_{i}: int = seed * {k + i}\n"
                                                         for i in range(5)) + f"\treturn {name}_4\n\n"


TRIO = [helper("alpha", 3), helper("beta", 5), helper("gamma", 7)]  # соседние помощники в двух копиях
SQUEEZE = "".join(f"\ttotal -= {k} * 3\n" for k in range(12))  # тело помощника, которое уборка вольёт в mix()
MORE = "\nfunc more(total: int) -> int:\n" + SQUEEZE + "\treturn total\n"
RUNNER = 'extends RefCounted\n\nconst SuiteBase := preload("res://tests/lib/suite.gd")\n'
SUITE = "extends RefCounted\n\nconst SEEDS := [1, 2]\nvar items: Array = []\n"  # предок: константа и поле без типа
LOOPS = ('extends "res://tests/lib/suite.gd"\n\nfunc test_seeds() -> int:\n\tvar n: int = 0\n\tfor seed in SEEDS:\n'
         "\t\tn += seed\n\tfor item in items:\n\t\tn += 1\n\treturn n\n")
STORE = "extends RefCounted\n\nfunc add(items: Array[int]) -> int:\n\treturn items.size()\n"  # одноимённый параметр с типом
HEIR = "extends BaseCase\n\nfunc run() -> int:\n\tvar n: int = 0\n\tfor item in items:\n\t\tn += item\n\treturn n\n"
ANCESTOR = "extends RefCounted\nclass_name BaseCase\n\nvar items: Array[int] = []\n"
PLAN = "public partial class Plan : Godot.Node\n{\n%s}\n"
WEIGHT = "    public float TerraceWeight(float x)\n    {\n        return x;\n    }\n"
REGION = 'extends RefCounted\n\nfunc weight(cs: Object) -> float:\n\treturn cs.call("TerraceWeight", 1.0)\n'
COLUMN = "    public float SampleColumn(float x)\n    {\n        float t = x;\n" + "".join(
    f"        t += {n} * {n + 7};\n" for n in range(110)) + "        return t;\n    }\n"
OVERLOAD = "    public float SampleColumn(float x, float y)\n    {\n        return SampleColumn(x + y);\n    }\n\n"
FAR = "func test_far(world: Node) -> void:\n\tvar ground := {}([world._horizon])\n\tassert(ground > 0)\n"
DOLLAR = "function $(id) {\n  const node = document.getElementById(id);\n  if (!node) throw new Error(id);\n" \
    "  return node;\n}\n"
PRELOADS = "extends RefCounted\n" + "".join(f'const Part{k} := preload("res://scripts/part_{k}.gd")\n'
                                        for k in range(9))
IMPORTS = "import {\n" + "".join(f"  name{k},\n" for k in range(9)) + "} from './lib.js';\n"
KEEP = {"scripts/keep.gd": "extends RefCounted\n"}


def commit_all(root, message):
    for args in (["add", "-A"], ["-c", "user.name=studio selftest", "-c", "user.email=selftest@studio.invalid",
                                 "commit", "-qm", message]):
        subprocess.run(["git", "-C", root] + args, capture_output=True, timeout=30)


def own_repo(tmp, name, files):
    """Свой пробный репозиторий с закоммиченной базой: (папка, хеш снятия)."""
    root = os.path.join(tmp, "проект кода", name)
    for rel, text in files.items():
        write(os.path.join(root, rel), text)
    subprocess.run(["git", "init", "-q", root], capture_output=True, timeout=30)
    commit_all(root, "Process: start")
    code_run(root, "--baseline")
    commit_all(root, "Process: code baseline")
    return root, subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                                timeout=30).stdout.decode().strip()


def code_repo(tmp):
    """Пробный репозиторий: в базе файл 1101 строка, две mix() по 110 строк, 4 объявления без типа, две копии TRIO,
    кусок PACE в двух функциях."""
    root = os.path.join(tmp, "проект кода", "база")
    trio = "extends RefCounted\n\n" + "".join(TRIO)
    for rel, text in (("project.godot", "config_version=5\n"), ("scripts/big.gd", BIG),
                      ("scripts/one.gd", trio), ("scripts/two.gd", trio),
                      ("scripts/long.gd", "extends RefCounted\n\n" + LONG), ("scripts/twin.gd", TWIN),
                      ("scripts/util.gd", UTIL), ("scripts/steps.gd", "extends RefCounted\n" + WALK + RUN),
                      ("scripts/loose.gd", "extends RefCounted\n\n" + LOOSE),
                      ("scripts/old util.gd", "extends RefCounted\n\nfunc keep(seed: int) -> int:\n\treturn seed + 1\n")):
        write(os.path.join(root, rel), text)
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", root] + args, capture_output=True, timeout=30)
    return root


def code_run(root, *args):
    p = subprocess.run([sys.executable, "-X", "utf8", CODE_CHECK, "--root", root] + list(args),
                       capture_output=True, timeout=60)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def check_code(results, tmp):
    """Шаблонный code_check.py: коды 0/1/2 на пробных правках против базы.

    pre — правка, закоммиченная пунктом партии до снятия хеша; done — пункт закоммичен, затем как /done
    (done раз): --tighten и коммит приёмки, затем прогон args; absent — строки, которой в выводе быть не должно.
    """
    master = code_repo(tmp)

    def edit(root, change):
        for rel, how in (change or {}).items():
            path = os.path.join(root, rel)
            text = how(open(path, encoding="utf-8").read() if os.path.isfile(path) else "")
            os.remove(path) if text is None else write(path, text)

    def case(name, expect, needle="", change=None, args=("--changed", "{tree}"), copy=True, done=False, pre=None,
             absent=None):
        root = os.path.join(tmp, "проект кода", f"случай {len(results)}") if copy else master
        if copy:
            shutil.copytree(master, root)
        if pre:
            edit(root, pre)
            commit_all(root, "Item 1: earlier in the batch")
        tree = subprocess.run(["git", "-C", root, "write-tree"], capture_output=True, timeout=30).stdout.decode().strip()
        edit(root, change)
        for _ in range(int(done)):
            commit_all(root, "Item 2: selftest")
            code_run(root, "--tighten")
            commit_all(root, "Accept: selftest")
        code, out = code_run(root, *[a.format(tree=tree) for a in args])
        ok = code == expect and needle in out and not (absent and absent in out)
        results.append(("code_check", name, f"код {expect}", f"код {code}", ok))

    case("нет базы", 2, "базы нет", args=())
    case("записать базу", 0, "База записана", args=("--baseline",), copy=False)
    commit_all(master, "Process: code baseline")
    code_cases(case)
    check_hot(results, tmp)
    check_joined(results, tmp)
    check_scope(results, tmp)


def check_hot(results, tmp):
    """Горячий файл — под нынешним именем после переноса, уборки («для игрока без изменений») не в счёт, пункт с
    двумя коммитами — один; номера в каждой партии свои — Item 1–6 двух партий — 12 пунктов."""
    root, _ = own_repo(tmp, "горячий", {"a.gd": "extends RefCounted\n"})
    for k, message in enumerate(["Item {}: step"] * 6 + ["Item {}: Move (no change for players)"] + ["Item {}: step"] * 4
                                + ["Пункт {}: уборка — для игрока без изменений"] * 3 + ["Item 5: fix"]):
        if k == 6:
            os.makedirs(os.path.join(root, "sub"))
            subprocess.run(["git", "-C", root, "mv", "a.gd", "sub/a.gd"], capture_output=True, timeout=30)
        path = os.path.join(root, "sub" if k >= 6 else "", "a.gd")
        write(path, open(path, encoding="utf-8").read() + f"var v{k}: int = {k}\n")
        commit_all(root, message.format(k + 1))
    code, out = code_run(root, "--only", "горячий")
    results.append(("code_check", "горячий файл после переноса, без уборок, по пунктам", "код 0",
                    f"код {code}", code == 0 and "sub/a.gd — горячий файл: задело пунктов 10 из" in out))
    root, _ = own_repo(tmp, "горячий, две партии", {"a.gd": "extends RefCounted\n"})
    for k, message in enumerate([f"Item {n}: step" for n in range(1, 7)] + ["Accept: first batch"]
                                + [f"Item {n}: step" for n in range(1, 7)]):
        write(os.path.join(root, "a.gd"), open(os.path.join(root, "a.gd"), encoding="utf-8").read() + f"var v{k}: int\n")
        commit_all(root, message)
    code, out = code_run(root, "--only", "горячий")
    results.append(("code_check", "горячий файл: Item 1–6 в двух партиях", "код 0", f"код {code}",
                    code == 0 and "a.gd — горячий файл: задело пунктов 12 из 12" in out))


def check_joined(results, tmp):
    """Уборка вынесла помощник из середины третьей копии: стык соседей стал третьим местом повтора из базы —
    --changed чист, весь проект до /done чист (повтор из базы, задетый переносом, — заметка), --tighten поднимает
    число мест, весь проект после /done чист."""
    duo = "extends RefCounted\n\n" + TRIO[0] + TRIO[2]
    root, snap = own_repo(tmp, "стык", {"a.gd": duo, "b.gd": duo, "c.gd": "extends RefCounted\n\n" + "".join(TRIO)})
    write(os.path.join(root, "c.gd"), duo)
    write(os.path.join(root, "lib.gd"), "extends RefCounted\n\n" + TRIO[1])
    commit_all(root, "Item 1: Helpers in one place (no change for players)")
    changed, _ = code_run(root, "--changed", snap)
    before, _ = code_run(root)
    _, raised = code_run(root, "--tighten")
    commit_all(root, "Accept: selftest")
    full, _ = code_run(root)
    results.append(("code_check", "стык после выноса помощника: --changed, весь проект до и после /done",
                    "коды 0, 0 и 0", f"коды {changed}, {before} и {full}",
                    changed == before == full == 0 and "мест 2 → 3" in raised))


def check_scope(results, tmp):
    """Своими репозиториями: счёт файла — от него и его предков (extends), а не от чужих строк; правка другого файла
    видна --changed; перегрузка выше длинного метода, перенос с правкой имени, заголовок помощника, подключения,
    метка «generated» в новом рукописном файле. done — после /done весь проект с тем же кодом, что --changed;
    2 — после /done пункт откатили: итог вырос — база не снижена и по файлам."""
    lib = "extends RefCounted\n\nfunc far(x: Array) -> int:\n\treturn x.size()\n"
    empty = "extends RefCounted\n"
    for name, expect, needle, done, files, change in (
            ("удалены псевдоним и одноимённый параметр, /done", 0, "находок 0", True,
             {"tests/lib/suite.gd": SUITE, "tests/runner.gd": RUNNER + "const SEEDS := SuiteBase.SEEDS\n",
              "tests/water.gd": LOOPS, "scripts/store.gd": STORE},
             {"tests/runner.gd": RUNNER, "scripts/store.gd": empty}),
            ("у предка снят тип — циклы наследника", 1, "в старых строках", False,
             {"tests/base.gd": ANCESTOR, "tests/suite.gd": HEIR},
             {"tests/base.gd": ANCESTOR.replace("Array[int]", "Array")}),
            ("переименован метод C#, .call по старому имени", 1, "«TerraceWeight»", False,
             {"scripts/Plan.cs": PLAN % WEIGHT, "scripts/region.gd": REGION},
             {"scripts/Plan.cs": PLAN % WEIGHT.replace("TerraceWeight(", "TerraceWeightAt(")}),
            ("перегрузка выше длинного метода из базы, /done", 0, "находок 0", True,
             {"scripts/Plan.cs": PLAN % COLUMN}, {"scripts/Plan.cs": PLAN % (OVERLOAD + COLUMN)}),
            ("перенос с _закрытым и переименованным помощником", 0, "старое обращение", False,
             {"tests/runner.gd": lib.replace("far(", "_far(") + "\n" + FAR.format("_far")},
             {"tests/runner.gd": empty, "tests/lib/suite.gd": lib,
              "tests/suites/far.gd": 'extends "res://tests/lib/suite.gd"\n\n' + FAR.format("far")}),
            ("у копии помощника из базы правлен заголовок", 0, "задет заголовок", False,
             {"src/main.js": DOLLAR, "src/download.js": DOLLAR}, {"src/main.js": "export " + DOLLAR}),
            ("подключения: preload и многострочный import", 0, "находок 0", False, KEEP,
             {"scripts/a.gd": PRELOADS, "scripts/b.gd": PRELOADS, "web/a.js": IMPORTS, "web/b.js": IMPORTS}),
            ("новый рукописный файл с «generated by» в шапке", 0, "мимо замера", False, KEEP,
             {"scripts/mesh.gd": "## Сетка: высоты generated by region_plan.gd\n" + empty + "\nvar x = 1\n"}),
            ("итог Н5 вырос, /done, откат пункта", 0, "находок 0", 2,
             {"scripts/a.gd": empty + "\nvar x = 1\nvar y = 2\n", "scripts/b.gd": empty},
             {"scripts/a.gd": empty + "\nvar x: int = 1\nvar y = 2\n",
              "scripts/b.gd": empty + "\nvar p = 1\nvar q = 2\n"})):
        root, snap = own_repo(tmp, f"охват {len(results)}", files)
        for rel, text in change.items():
            write(os.path.join(root, rel), text)
        code, out = code_run(root, "--changed", snap)
        if done:
            commit_all(root, "Item 1: selftest")
            item = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, timeout=30).stdout
            code_run(root, "--tighten")
            commit_all(root, "Accept: selftest")
            if done == 2:  # пункт откатили после /done — база не должна остаться ниже прежнего
                subprocess.run(["git", "-C", root, "-c", "user.name=studio selftest", "-c", "user.email=s@studio.invalid",
                                "revert", "--no-edit", item.decode().strip()], capture_output=True, timeout=30)
            full, out = code_run(root)
        ok = (done == 2 or code == expect) and needle in out and (not done or full == expect)
        got = f"после /done и отката {full}" if done == 2 else \
            f"код {code}" + (f", после /done {full}" if done else "")
        results.append(("code_check", name + " — весь проект" * bool(done), f"код {expect}", got, ok))


def code_cases(case):
    """Случаи против записанной базы: размеры, повторы, переносы; остальное — rule_cases."""
    case("база на месте — весь проект", 0, "находок 0", args=())
    case("строки сверх нормы для /board", 0, "строк сверх нормы 121 —", args=("--summary",), absent="при записи")
    broken = {"tools/code_baseline.json": lambda t: "<<<<<<< HEAD\n" + t}
    case("база испорчена", 2, "испорчена", broken, args=())
    case("база испорчена — --baseline не заменяет", 2, "испорчена", broken, args=("--baseline",))
    case("«do not edit» в своём файле — меряется", 1, "без типа",
         {"scripts/table.gd": lambda _: "# Таблица шагов — do not edit вручную\nextends RefCounted\n\nvar x = 1\n"})
    case("«generated by» в шапке своего файла — меряется", 1, "без типа",
         {"scripts/terrain.gd": lambda _: "## Terrain generated by layered noise.\nextends RefCounted\n\nvar x = 1\n"})
    case("новый файл 1001 строка", 1, "каждая меньше порога",
         {"scripts/huge.gd": lambda _: "extends RefCounted\n" + gd_vars("huge", 0, 1000)})
    case("файл из базы +15 строк", 0, "находок 0", {"scripts/big.gd": lambda t: t + gd_vars("big", 1100, 15)})
    case("файл из базы +25 строк", 1, "файл длиннее", {"scripts/big.gd": lambda t: t + gd_vars("big", 1100, 25)})
    picture = 'const PICTURE := "' + "A" * 6000 + '"\n'
    case("файл из базы +25 и строка 6000 знаков — весь проект", 1, "файл длиннее",
         {"scripts/big.gd": lambda t: t + picture + gd_vars("big", 1100, 25)}, args=())
    case("без запаса — только вызов нового", 0, "только вызов нового",
         {"scripts/big.gd": lambda t: t + gd_vars("big", 1119, 2)},
         pre={"scripts/big.gd": lambda t: t + gd_vars("big", 1100, 19)})
    case("уборка в партии — место не отдаётся", 1, "файл длиннее",
         {"scripts/big.gd": lambda t: t + gd_vars("big", 1100, 25)},
         pre={"scripts/big.gd": lambda _: "extends RefCounted\n" + gd_vars("big", 0, 1049)})
    head = "\tvar total: int = seed\n"
    case("уборка функции в партии — место не отдаётся", 1, "функция mix длиннее",
         {"scripts/long.gd": lambda t: t.replace(head, head + "".join(f"\ttotal -= {n}\n" for n in range(15)))},
         pre={"scripts/long.gd": lambda t: t.replace("".join(f"\ttotal += {n} * {n + 7}\n" for n in range(20)), "")})
    other = "extends RefCounted\n\nfunc mix(seed: int) -> int:\n\tvar total: int = seed\n" + "".join(
        f"\ttotal -= {n} + {n * 3}\n" for n in range(113)) + "\treturn total\n"
    case("новая функция с именем удалённой из базы", 1, "функция mix длиннее",
         {"scripts/long.gd": lambda _: None, "scripts/other.gd": lambda _: other})
    case("функция с кириллицей 108 строк", 1, "функция шаг длиннее",
         {"scripts/шаги.gd": lambda _: "extends RefCounted\n\n" + LONG.replace("func mix", "func шаг")})
    trio = {f"scripts/{name}.gd": lambda t: t.replace(TRIO[1], "") for name in ("one", "two")}
    trio["scripts/lib.gd"] = lambda _: "extends RefCounted\n\n" + TRIO[1]
    case("помощник из середины двух копий — в общий файл", 0, "находок 0", trio)
    case("помощник из середины, /done — весь проект", 0, "находок 0", trio, args=(), done=True)
    case("помощник из середины, /done — новое с хеша до него", 0, "находок 0", trio, done=True)
    copy = {"scripts/copy.gd": lambda _: "extends RefCounted\n\nfunc copy_of(seed: int) -> int:\n"
            + "".join(STEPS.splitlines(True)[:8]) + "\treturn step_7\n"}
    case("повтор 8 строк", 1, "повтор 8 строк", copy)
    case("повтор 8 строк, два /done, база снижалась — весь проект", 1, "повтор 8 строк",
         dict(copy, **{"scripts/big.gd": lambda t: t.replace("var big_5: int = 5\n", "")}), args=(), done=2)
    edited = PACE.replace("seed + 0\n", "seed + 1\n")  # повтор базы переехал с правкой в обоих местах
    carried = {"scripts/steps.gd": lambda _: None, "scripts/walk.gd": lambda _: "extends RefCounted\n" + WALK.replace(
        PACE, edited), "scripts/run.gd": lambda _: "extends RefCounted\n" + RUN.replace(PACE, edited)}
    case("повтор из базы переехал с правкой", 0, "задет переносом", carried)
    case("повтор из базы переехал с правкой, /done — весь проект", 0, "находок 0", carried, args=(), done=True)
    half = "".join(f"\tvar pace_{k}: int = seed {'-' if k < 4 else '+'} {k * 5}\n" for k in range(10))  # 4 из 10 строк
    halved = {"scripts/steps.gd": lambda _: None, "scripts/walk.gd": lambda _: "extends RefCounted\n" + WALK.replace(
        PACE, half), "scripts/run.gd": lambda _: "extends RefCounted\n" + RUN.replace(PACE, half)}
    case("повтор из базы переехал, правлено 4 строки из 10", 0, "задет переносом", halved)
    case("повтор переехал, правлено 4 из 10, /done — весь проект", 0, "находок 0", halved, args=(), done=True)
    move = {"scripts/long.gd": lambda t: t.replace(LONG, ""), "scripts/moved.gd": lambda _: "extends RefCounted\n\n" + LONG}
    case("перенос функции в новый файл", 0, "находок 0", move)
    case("перенос функции — весь проект", 0, "находок 0", move, args=())
    case("перенос функции в файл на 990 строк", 1, "перенесённое — отдельными файлами",
         {"scripts/long.gd": lambda t: t.replace(LONG, ""), "scripts/near.gd": lambda t: t + "\n" + LONG},
         pre={"scripts/near.gd": lambda _: "extends RefCounted\n" + gd_vars("near", 0, 989)})
    case("помощник влит в длинную функцию из базы", 1, "перенесённое — своей функцией",
         {"scripts/long.gd": lambda t: t.replace("\ttotal = more(total)\n", SQUEEZE).replace(MORE, "")},
         pre={"scripts/long.gd": lambda t: t.replace("\treturn total\n", "\ttotal = more(total)\n\treturn total\n", 1)
              + MORE})
    loose = {"scripts/loose.gd": lambda t: t.replace(LOOSE, ""), "scripts/loose_part.gd": lambda _: LOOSE}
    case("перенос без типа, /done — весь проект", 0, "находок 0", loose, args=(), done=True)
    rename = {"scripts/big.gd": lambda _: None, "scripts/world/big.gd": lambda _: BIG}
    case("переименование файла из базы — весь проект", 0, "находок 0", rename, args=())
    case("переименование, /done — весь проект", 0, "находок 0", rename, args=(), done=True)
    twins = {"scripts/long.gd": lambda _: None, "scripts/twin.gd": lambda _: None,  # две mix() разом, одна правлена
             "scripts/checks/long.gd": lambda _: "extends RefCounted\n\n" + LONG.replace("* 12\n", "* 13\n"),
             "scripts/checks/twin.gd": lambda _: TWIN}
    case("две одноимённые функции переехали, одна правлена", 0, "находок 0", twins)
    case("две одноимённые переехали, /done — весь проект", 0, "находок 0", twins, args=(), done=True)
    merged = {"scripts/long.gd": lambda _: None, "scripts/merged.gd": lambda _: "extends RefCounted\n\n" + gd_vars(
        "merged", 0, 130) + "\n" + LONG.replace("* 12\n", "* 13\n")}  # файл удалён, mix() с правкой — в новый
    case("функция из удалённого файла, правлен вызов", 0, "находок 0", merged)
    case("функция из удалённого файла, /done — весь проект", 0, "находок 0", merged, args=(), done=True)
    rule_cases(case)


def rule_cases(case):
    """Случаи против записанной базы: типы, закрытое, службы, вызов по имени, подъём базы, --only."""
    case("var x = 1 в .gd", 1, "без типа", {"scripts/util.gd": lambda t: t + "\nvar x = 1\n"})
    case("var x = 1 в файле с пробелом", 1, "без типа", {"scripts/old util.gd": lambda t: t + "\nvar x = 1\n"})
    case("var x = 1 в чужом черновике _scratch", 0, "находок 0",
         {"scripts/_scratch_probe.gd": lambda _: "extends RefCounted\n\nvar x = 1\n"})
    count = "\nfunc count(n: Node) -> int:\n\tvar t: int = 0\n\tfor i in n.get_child_count():\n\t\tt += i\n\treturn t\n"
    case("цикл по get_child_count()", 0, "находок 0", {"scripts/util.gd": lambda t: t + count})
    table = "\nfunc total() -> int:\n\tvar items: Array = [1, 2]\n\tvar sum: int = 0\n\tfor it in items:\n" \
        "\t\tsum += it\n\treturn sum\n"
    case("цикл по Array без типа элементов", 1, "for it", {"scripts/util.gd": lambda t: t + table})
    probe = {"tests/check_plan.gd": lambda _: "extends RefCounted\n\nfunc edge_ok(plan: Object) -> bool:\n"
             "\treturn plan._edge(1.5) > 1.0\n"}
    case("старая проверка с _закрытым: правлено только число", 0, "старое обращение",
         {"tests/check_plan.gd": lambda t: t.replace("1.5", "1.75")}, pre=probe)
    dead = {"scripts/dead.gd": lambda _: "extends RefCounted\n\n" + "".join(
        f"const DEAD_{n}: int = {n}\n" for n in range(25))}
    case("--only без текста — все строки", 0, "DEAD_24 —", dead, args=("--changed", "{tree}", "--only"), absent="и ещё")
    layers = "extends RefCounted\n\nfunc _grass() -> int:\n\treturn 1\n\nfunc pick(layer: String) -> int:\n" \
        '\treturn call("_" + layer)\n'
    case("вызов по собранному имени — не мёртвое", 0, "находок 0", {"scripts/layers.gd": lambda _: layers},
         absent="мёртвое: _grass")
    ignore = {"scripts/util.gd": lambda t: '@warning_ignore_start("unsafe_method_access")\n' + t}
    case("@warning_ignore без включения ключа", 1, "ослаблены типы", ignore)
    case("@warning_ignore тем же diff, что включает ключ", 0, "находок 0", dict(ignore, **{
        "project.godot": lambda t: t + "\n[debug]\n\ngdscript/warnings/unsafe_method_access=2\n"}))
    service = {"project.godot": lambda t: t + '\n[autoload]\n\nSound="*res://scripts/util.gd"\n'}
    case("новая служба без записи — заметка исполнителю", 0, "CONCEPT, Службы: Sound", service)
    case("новая служба без записи — весь проект", 1, "служба Sound", service, args=())
    case('.call("Nope")', 1, "по имени",
         {"scripts/util.gd": lambda t: t + '\nfunc poke(node: Object) -> void:\n\tnode.call("Nope")\n'})
    case("база поднята", 1, "база поднята",
         {"tools/code_baseline.json": lambda t: t.replace('"scripts/big.gd": 1101', '"scripts/big.gd": 1201')})
    case("порог поднят в базе", 1, "порог file_lines",
         {"tools/code_baseline.json": lambda t: t.replace('"limits": {}', '"limits": {"file_lines": 2000}')})


def check_guard(results, project, copy, plain):
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
        ("git log --grep обеими пометками", 'git log abc1234..HEAD -E --grep "^(Пункт|Item) 2:"'),
        ("revert найденного поиском",
         'git revert --no-edit $(git log abc1234..HEAD -E --grep "^(Пункт|Item) 2:" --format=%H)'),
        ("коммит с телом по-английски",
         'git commit -m "Item 3: Let players dig through stone" -m "Checks: full 66/0, quick 1/0."'),
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


def check_context(results, tmp, project, idle, plain):
    def context(name, cwd, expect, utf8=True, payload=None,
                needles=("в работе: 2 (круг 2/3)", "готовы: 1", "^(Пункт|Item)")):
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


def check_roadmap(results, tmp):
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


def sanity_frames(folder):
    """Пробные кадры для --sanity: сцена, её копия с шумом, другая сцена, серое и чёрное окна."""
    from PIL import Image, ImageChops, ImageDraw
    os.makedirs(folder, exist_ok=True)
    scene = Image.new("RGB", (640, 360), (90, 140, 200))
    draw = ImageDraw.Draw(scene)
    for n in range(12):
        draw.rectangle((n * 50, 200 - n * 7, n * 50 + 40, 360), fill=(40 + n * 12, 110, 60 + n * 5))
        draw.ellipse((n * 53, 30 + n * 9, n * 53 + 30, 60 + n * 9), fill=(250, 250, 240 - n * 10))
    noise = Image.effect_noise(scene.size, 12).convert("L")  # вокруг 128, ст. откл. 12
    noisy = Image.merge("RGB", [ImageChops.add(c, noise, 1, -128) for c in scene.split()])
    other = scene.transpose(Image.FLIP_LEFT_RIGHT)
    ImageDraw.Draw(other).rectangle((200, 60, 440, 300), fill=(200, 60, 40))
    names = {"сцена": scene, "шум": noisy, "другая": other,
             "серое": Image.new("RGB", scene.size, (77, 77, 77)), "чёрное": Image.new("RGB", scene.size)}
    for name, image in names.items():
        image.save(os.path.join(folder, name + ".png"))
    return {name: os.path.join(folder, name + ".png") for name in names}


def check_sanity(results, tmp):
    """look_sheet.py --sanity: пустой кадр, неотличимые варианты, кадр как в прошлом круге."""
    try:
        frames = sanity_frames(os.path.join(tmp, "кадры с пробелом"))
    except ImportError:
        results.append(("look_sheet", "--sanity: пропущено — нет Pillow", "—", "—", True))
        return
    f = frames
    cases = (("нормальные кадры", 0, "кадры в порядке", [f["сцена"], f["другая"]]),
             ("два одинаковых снимка стенда без --var", 0, "кадры в порядке", [f["сцена"], f["шум"]]),
             ("разные варианты", 0, "кадры в порядке", ["--var", "A=" + f["сцена"], "--var", "B=" + f["другая"]]),
             ("серое окно", 1, "брак: " + f["серое"], [f["сцена"], f["серое"]]),
             ("чёрное окно", 1, "чёрное окно", [f["чёрное"]]),
             ("варианты — копия с шумом", 1, "почти одинаковы", ["--var", "A=" + f["сцена"], "--var", "B=" + f["шум"]]),
             ("как прошлый круг", 1, "правка не дошла", ["--prev", f["шум"] + "=" + f["сцена"]]),
             ("прошлый круг другой", 0, "кадры в порядке", ["--prev", f["другая"] + "=" + f["сцена"]]),
             ("--prev без «=»", 2, "", [f["сцена"], "--prev", f["шум"]]))
    for name, expect, needle, extra in cases:
        p = subprocess.run([sys.executable, "-X", "utf8", LOOK_SHEET, "--sanity", *extra], capture_output=True, timeout=60)
        out = p.stdout.decode("utf-8", "replace")
        ok = p.returncode == expect and needle in out
        results.append(("look_sheet", "--sanity: " + name, f"код {expect}", f"код {p.returncode}", ok))


GROUPS = (("хуки работают", "хуки НЕ работают", lambda r: r[0] in ("guard_git", "session_context", "hooks.json")),
          ("проверка карты", "проверка карты НЕ работает", lambda r: r[0] == "roadmap_check"),
          ("проверка кода", "проверка кода НЕ работает", lambda r: r[0] in ("code_check", "паритет очереди")),
          ("проверка кадров", "проверка кадров НЕ работает", lambda r: r[0] == "look_sheet"))


def verdict(results, quick=False):
    """Таблица и строка «Итог:» по группам; код 1 — хоть одна группа неверна."""
    widths = [max(len(r[k]) for r in results) for k in range(4)]
    head = ("Хук", "Случай", "Ждали", "Вышло")
    widths = [max(w, len(h)) for w, h in zip(widths, head)]
    print("  ".join(h.ljust(w) for h, w in zip(head, widths)) + "  Итог")
    for r in results:
        print("  ".join(r[k].ljust(widths[k]) for k in range(4)) + ("  ок" if r[4] else "  ОШИБКА"))
    parts, failed = [], False
    for good, broken, member in GROUPS:
        rows = [r for r in results if member(r)]
        bad = [f"{r[0]}: {r[1]}" if good.startswith("хуки") else r[1] for r in rows if not r[4]]
        failed = failed or bool(bad)
        parts.append(f"{broken} — неверно {len(bad)} из {len(rows)}: " + "; ".join(bad) if bad
                     else f"{good} — {len(rows)}/{len(rows)} верно")
    print(f"Итог: {'; '.join(parts)}" + ("; случаи code_check не гонялись (--quick)" if quick else "") + ".")
    return 1 if failed else 0


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    tmp, quick = tempfile.mkdtemp(prefix="studio-selftest-"), "--quick" in sys.argv[1:]
    results = []
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
        check_guard(results, project, copy, plain)
        check_context(results, tmp, project, idle, plain)
        check_config(results)
        check_roadmap(results, tmp)
        if not quick:
            check_code(results, tmp)
        check_parity(results, tmp)
        check_sanity(results, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return verdict(results, quick)

if __name__ == "__main__":
    sys.exit(main())
