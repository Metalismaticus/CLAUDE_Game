#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общие операции инструментов studio: пути, диск, план проверок, телеметрия.

Пользуются: run_check.py, slot_pool.py, batch_check.py, ops_report.py.

Правила, ради которых этот файл существует (docs/WHY.md плагина, раздел
«Почему пул слотов и один запуск проверок»):

- корень проекта и слоты — только абсолютные нормализованные пути;
  рабочая папка вызывающей оболочки не имеет значения;
- у каждого слота (и основной папки) — ровно один сборочный каталог
  `<проект>.wt/targets/<слот>`; CARGO_TARGET_DIR назначает run_check,
  руками его не выставляют и не меняют между командами;
- слот — ресурс исполнения, а не номер пункта: пул переиспользует слоты,
  кэш переживает пункты;
- перед созданием слота и тяжёлой сборкой — проверка свободного места
  (настраиваемый резерв); нехватка — остановка с причиной (код 3),
  а не цикл повторных сборок;
- телеметрия пишет только перечисленные поля (никаких секретов и полного
  окружения) и не имеет права ломать основную работу.
"""
import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SLOT_RE = re.compile(r"^slot(\d+)$")

# коды возврата, общие для инструментов студии
EXIT_OK = 0          # зелено / в порядке
EXIT_RED = 1         # провал проверки / несоответствие (первый ненулевой код ребёнка)
EXIT_BAD = 2         # неверный вызов, нет плана/файла, испорченные данные
EXIT_DISK = 3        # остановка: мало места (не красное, не повтор)


class OpError(Exception):
    """Ошибка вызова с готовым кодом возврата и сообщением."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def utf8_stdio():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


# --------------------------------------------------------------------- пути

def norm(path):
    """Абсолютный нормализованный путь (регистронезависимо только разделители)."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def long_path(path):
    """Длинная форма пути (8.3-имена -> полные): git и TEMP дают разные формы."""
    return os.path.realpath(norm(path))


def same_dir(a, b):
    """Один и тот же каталог (учёт регистра и 8.3/длинной формы)."""
    return os.path.normcase(long_path(a)) == os.path.normcase(long_path(b))


def find_root(start=None):
    """Корень проекта: папка с docs/BATCH.md (выше не поднимаемся, чем есть)."""
    p = norm(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(p, "docs", "BATCH.md")) or \
                os.path.isdir(os.path.join(p, "tools")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def main_root(path):
    """Корень ОСНОВНОЙ папки проекта: путь внутри слота ведёт к его проекту.

    Слот лежит в `../<проект>.wt/slotK/…` — «.wt» это СУФФИКС имени каталога
    (один уровень над проектом), а не отдельный компонент пути.
    """
    p = norm(path)
    root = find_root(p)
    if root is None:
        return p
    parts = root.replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-1]):
        if part.endswith(".wt"):
            return norm("/".join(parts[:i] + [part[:-3]]))
    return root


def wt_dir(root):
    """`../<папка проекта>.wt` — рабочая территория студии рядом с проектом."""
    root = norm(root)
    return norm(os.path.join(os.path.dirname(root), os.path.basename(root) + ".wt"))


def slot_name(path):
    """Имя слота для пути: slotK из `<проект>.wt/slotK/...`, иначе main."""
    parts = norm(path).replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-1]):
        if part.endswith(".wt"):
            cand = parts[i + 1]
            if SLOT_RE.match(cand):
                return cand
            return "main"  # .wt/<что-то ещё> — не слот
    return "main"


def slot_path(root, slot):
    return norm(os.path.join(wt_dir(main_root(root)), slot))


def target_dir(root):
    """ЕДИНСТВЕННЫЙ сборочный каталог для этого корня/слота (Rust: CARGO_TARGET_DIR)."""
    return norm(os.path.join(wt_dir(main_root(root)), "targets", slot_name(root)))


def ops_log(root):
    return norm(os.path.join(wt_dir(main_root(root)), "usage", "studio-ops.jsonl"))


# --------------------------------------------------------------------- диск

def disk_free_bytes(path):
    """Свободно байт на диске пути; None — не удалось узнать (не ломаем работу)."""
    p = norm(path)
    probe = p if os.path.exists(p) else os.path.dirname(p)
    while not os.path.exists(probe) and probe:
        probe = os.path.dirname(probe)
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        pass
    if os.name == "nt":
        try:
            free = ctypes.c_ulonglong(0)
            wpath = probe if probe.startswith("\\\\?\\") else "\\\\?\\" + probe
            ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(wpath), None, None, ctypes.byref(free))
            return free.value if ok else None
        except Exception:  # noqa: BLE001 — телеметрия не ломает работу
            return None
    return None


def dir_size_bytes(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path, onerror=lambda _e: None):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def human_size(n):
    if n is None:
        return "?"
    size = float(n)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} Б"


def check_disk(path, plan):
    """(ok, free_bytes, reserve_bytes, сообщение). Резерв — из плана, ГБ."""
    free = disk_free_bytes(path)
    if free is None:
        return True, None, None, "свободное место не удалось узнать — проверка пропущена"
    reserve = int(plan.get("disk_reserve_gb", 10) or 0) * 1024 ** 3
    if free < reserve:
        msg = (f"мало места: свободно {human_size(free)}, резерв {human_size(reserve)} "
               f"— остановлено ДО запуска; чистка только зарегистрированных неактивных "
               f"кэшей: tools/slot_pool.py clean-cache --free, разбор лишних каталогов: "
               f"tools/slot_pool.py diagnostics")
        return False, free, reserve, msg
    return True, free, reserve, ""


# --------------------------------------------------------------------- план

def plan_path(root):
    return norm(os.path.join(main_root(root), "tools", "check_plan.json"))


def load_plan(root):
    p = plan_path(root)
    if not os.path.isfile(p):
        raise OpError(EXIT_BAD, f"нет {p} — план проверок кладёт /setup "
                                "(steps/testing.md); без него run_check и пул "
                                "слотов не работают")
    try:
        # utf-8-sig: PowerShell-запись добавляет BOM — обычный utf-8 падает на нём
        plan = json.load(open(p, encoding="utf-8-sig"))
    except ValueError as e:
        raise OpError(EXIT_BAD, f"{p} испорчен: {e}")
    checks = plan.get("checks")
    if not isinstance(checks, list):
        raise OpError(EXIT_BAD, f"{p}: нет списка checks — заполните по steps/testing.md")
    seen = set()
    for c in checks:
        name = c.get("name")
        cmd = c.get("cmd")
        modes = c.get("modes")
        if not isinstance(name, str) or not name or name in seen:
            raise OpError(EXIT_BAD, f"{p}: имя проверки пропущено или повторено: {name!r}")
        seen.add(name)
        if not isinstance(cmd, list) or not all(isinstance(a, str) for a in cmd) or not cmd:
            raise OpError(EXIT_BAD, f"{p}: cmd проверки «{name}» — список аргументов "
                                    f'(например ["cargo", "test"]), не строка')
        if not isinstance(modes, list) or not modes or \
                not set(modes) <= {"quick", "item", "affected", "full", "long"}:
            raise OpError(EXIT_BAD, f"{p}: modes проверки «{name}» — непустой список из "
                                    "quick/item/affected/full/long")
        if not isinstance(c.get("areas", []), list):
            raise OpError(EXIT_BAD, f"{p}: areas проверки «{name}» — список строк")
    builds = plan.get("builds", "none")
    if builds not in ("app", "none"):
        raise OpError(EXIT_BAD, f"{p}: builds — \"app\" (проверкам нужно готовое приложение, "
                                f"сборку гонит run_check) или \"none\" (движок/владелец запускают "
                                f"сами), получено {builds!r}")
    return plan


def is_rust_stack(root, plan):
    """Rust-настройки — ТОЛЬКО Rust-стеку: Cargo.toml в корне или src-tauri/…"""
    cargo = plan.get("cargo") or {}
    if cargo.get("enabled") is True:
        return True
    if cargo.get("enabled") is False:
        return False
    root = main_root(root)
    return os.path.isfile(os.path.join(root, "Cargo.toml")) or \
        os.path.isfile(os.path.join(root, "src-tauri", "Cargo.toml"))


# --------------------------------------------------------------------- запуск

def run_cmd(cmd_args, cwd, env=None, timeout_s=None):
    """Запуск без оболочки, списком аргументов (пробелы и кириллица в путях — безопасно).

    -> (code, ms, out, err) — ПОЛНЫЙ вывод (хвост для показа делает вызывающий).
    Код 124 — превышен предел времени.
    """
    import time
    t0 = time.monotonic()
    out, err = [], []
    try:
        proc = subprocess.Popen(
            [str(a) for a in cmd_args], cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return 127, int((time.monotonic() - t0) * 1000), "", f"не удалось запустить {cmd_args[0]}: {e}"
    try:
        for line in proc.stdout:
            out.append(line)
        for line in proc.stderr:
            err.append(line)
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        code = 124
    ms = int((time.monotonic() - t0) * 1000)
    return code, ms, "".join(out), "".join(err)


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001 — лучшее, что можно сделать
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def git(root, *args, timeout_s=120):
    # core.quotepath=off: кириллица в путах — читаемой, не октал-эскейпами
    return run_cmd(["git", "-c", "core.quotepath=off", *args],
                   cwd=root, timeout_s=timeout_s)


# --------------------------------------------------------------------- файлы

def atomic_write_json(path, data):
    """Запись состояния целиком: временный файл + replace — нет полуобновлённых файлов."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


class Lock:
    """Межпроцессная блокировка файлов состояния (O_EXCL; лежачая — старше 60 с — снимается)."""

    def __init__(self, path, stale_s=60.0):
        self.path = path
        self.stale_s = stale_s
        self.fd = None

    def __enter__(self):
        import time
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(50):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > self.stale_s:
                        os.remove(self.path)  # лежачий замок — снимаем
                        continue
                except OSError:
                    pass
                time.sleep(0.1)
        raise OpError(EXIT_RED, f"не удалось занять блокировку {self.path} "
                                 "(другой процесс держит её дольше 5 с)")

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
                os.remove(self.path)
            except OSError:
                pass
        return False


# --------------------------------------------------------------------- телеметрия

TELEMETRY_KINDS = {
    "slot_acquire", "slot_release", "slot_park", "slot_unpark", "slot_prep",
    "slot_remove", "cache_clean", "check_run", "disk_stop", "batch_check",
}

def log_event(root, kind, **fields):
    """Одна строка в <проект>.wt/usage/studio-ops.jsonl.

    Только перечисленные поля, без окружения и секретов; любая ошибка — молча:
    телеметрия не ломает основную работу.
    """
    if kind not in TELEMETRY_KINDS:
        return
    try:
        line = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind}
        for k, v in fields.items():
            if v is None or isinstance(v, (bool, int, float, str)):
                line[k] = v
        p = ops_log(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — телеметрия не ломает работу
        pass


def read_events(root):
    """Строки телеметрии (список словарей); нет файла — пусто."""
    try:
        with open(ops_log(root), encoding="utf-8-sig") as f:
            return [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []


def is_registered_target(path, root):
    """Путь — зарегистрированный сборочный каталог (в targets/ основного .wt, имя слота)."""
    p = norm(path)
    base = norm(os.path.join(wt_dir(main_root(root)), "targets"))
    if os.path.dirname(p).lower() != base.lower():
        return None
    name = os.path.basename(p)
    return name if name == "main" or SLOT_RE.match(name) else None


def strays_temp_targets():
    """Забытые target-каталоги во временной папке (шли туда при ручном CARGO_TARGET_DIR)."""
    found = []
    try:
        for entry in os.listdir(tempfile.gettempdir()):
            if re.match(r"target-slot\d+", entry):
                found.append(norm(os.path.join(tempfile.gettempdir(), entry)))
    except OSError:
        pass
    return found
