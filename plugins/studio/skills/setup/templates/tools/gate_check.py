#!/usr/bin/env python3
"""Ворота: перечислить каждую правку проверок, оснастки и эталонов в diff — по имени.

Развёрнут плагином studio (/setup). Только стандартная библиотека и git.
Зовут `reviewer` (п. 2 «Порядка»: незакоммиченное пункта против HEAD) и
`/start` в конце запуска (`--changed <хеш снятия>` — всё, что партия
закоммитила). Инструмент **перечисляет и помечает, не судит**: проверяющий
обязан открыть каждый названный кусок и ответить, не ослаблена ли проверка;
«ворот нет» — единственный ответ, при котором читать нечего.

    python -X utf8 tools/gate_check.py [--changed [<хеш>]] [--root <папка>] [--json]

Что считается воротами (по пути файла):
  тест       — tests/, test/, spec/, *_test.*, *.test.*, *Test.*, tests/scenarios/*.json
  оснастка   — tools/, scripts/, .github/workflows/, раннеры и конфиги проверок
               (pytest.ini, pyproject [tool.pytest], tsconfig, .csproj, project.godot,
               override.cfg, checks_override.cfg, export_presets.cfg)
  эталон     — snapshots/, __snapshots__/, golden/, baselines/, *.snap, perf-базы,
               VISUAL_TARGETS, accepted-*.png и другие двоичные файлы в docs/refs/
  база       — tools/code_baseline.json, tools/check_plan.json, tools/perf_ref.json
и по содержимому куска (любой файл):
  снято утверждение   — удалена строка с assert / expect / check / is_equal / ok(
  снята проверка      — удалена строка, объявляющая тест (def test_, func test_, it(, test()
  пропуск             — добавлен skip / xfail / pending / @warning_ignore / noqa / no cover
  предел времени      — в куске менялась строка с timeout / предел / --quit-after
  допуск              — менялась строка с tolerance / epsilon / approx / delta / допуск
  ослаблены типы      — убрано untyped / unsafe / Nullable / TreatWarningsAsErrors /
                        strict или warnings/<ключ>=2 → меньше

Вывод: строка на кусок «ворота: <файл> @<строки> — <теги>», затем
«Итог: ворот N в M файлах — прочитать каждые» или «Итог: ворот нет».
Коды возврата: 0 — перечислено (ворота есть или нет — это не вердикт);
2 — нет git, неверный хеш или вызов.
"""
import argparse
import json
import os
import re
import subprocess
import sys

PATH_TAGS = (
    ("база", re.compile(r"(^|/)tools/(code_baseline|check_plan|perf_ref)\.json$")),
    ("эталон", re.compile(r"(^|/)(snapshots?|__snapshots__|golden|baselines?)/|\.snap$|"
                          r"VISUAL_TARGETS|(^|/)tests?/perf/.*\.json$|(^|/)docs/refs/.*\.(png|jpg|jpeg|webp|wav|ogg)$")),
    ("тест", re.compile(r"(^|/)(tests?|spec)/|_test\.[A-Za-z]+$|\.test\.[A-Za-z]+$|Tests?\.[A-Za-z]+$")),
    ("оснастка", re.compile(r"(^|/)(tools|scripts)/|(^|/)\.github/workflows/|(^|/)(pytest\.ini|pyproject\.toml|"
                            r"tsconfig[^/]*\.json|override\.cfg|checks_override\.cfg|export_presets\.cfg|project\.godot)$|"
                            r"\.csproj$|(^|/)(jest|vitest|karma|mocha)[^/]*\.(js|ts|json|cjs|mjs)$")),
)
ASSERT = re.compile(r"\b(assert\w*|expect|check|is_equal|assert_eq|assert_true|assert_false|ok|should)\s*[\(\.]")
TEST_DECL = re.compile(r"^\s*(def\s+test_|func\s+test_|fn\s+test_|it\s*\(|test\s*\(|\[Test\]|\[Fact\]|#\[test\])")
SKIP = re.compile(r"\b(skip|xfail|pending|only|todo)\b|@warning_ignore|noqa|pragma:\s*no\s*cover|\bpass\s*$", re.I)
TIMEOUT = re.compile(r"timeout|предел|--quit-after|time_limit|deadline", re.I)
TOLERANCE = re.compile(r"tolerance|epsilon|approx|delta|допуск|tol\b|rel_?err", re.I)
TYPES_WEAKENED = re.compile(r"untyped|unsafe_|Nullable|TreatWarningsAsErrors|\"strict\"|strict\s*=|warnings/\w+\s*=", re.I)


def git(root, *args):
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SystemExit(f"Итог: git не отработал ({e}) — неверный вызов.") from e
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def path_tags(path):
    p = path.replace("\\", "/")
    return [name for name, rx in PATH_TAGS if rx.search(p)]


def hunk_tags(removed, added):
    tags = []
    if any(ASSERT.search(l) for l in removed) and not any(ASSERT.search(l) for l in added):
        tags.append("снято утверждение")
    elif any(ASSERT.search(l) for l in removed):
        tags.append("изменено утверждение")
    if any(TEST_DECL.search(l) for l in removed) and not any(TEST_DECL.search(l) for l in added):
        tags.append("снята проверка")
    if any(SKIP.search(l) for l in added) and not any(SKIP.search(l) for l in removed):
        tags.append("пропуск")
    if any(TIMEOUT.search(l) for l in removed + added):
        tags.append("предел времени")
    if any(TOLERANCE.search(l) for l in removed + added):
        tags.append("допуск")
    if any(TYPES_WEAKENED.search(l) for l in removed) and not any(TYPES_WEAKENED.search(l) for l in added):
        tags.append("ослаблены типы")
    return tags


def parse(diff):
    """[(файл, строки, удалённые, добавленные, двоичный)] по unified diff."""
    out, path, hunk, removed, added, binary = [], None, None, [], [], False

    def flush():
        if path is not None and (hunk is not None or binary):
            out.append((path, hunk or "двоичный", removed, added, binary))

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            flush()
            m = re.search(r' b/(.+)$', line)
            path, hunk, removed, added, binary = (m.group(1) if m else line), None, [], [], False
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            binary = True
        elif line.startswith("@@"):
            flush()
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            start = int(m.group(1)) if m else 0
            count = int(m.group(2)) if m and m.group(2) else 1
            hunk, removed, added = f"{start}–{start + max(count, 1) - 1}", [], []
        elif hunk is not None:
            if line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:])
    flush()
    return out


def untracked(root):
    code, out, _ = git(root, "status", "--porcelain", "--untracked-files=all")
    files = [l[3:].strip().strip('"') for l in out.splitlines() if l.startswith("??")]
    rows = []
    for f in files:
        full = os.path.join(root, f)
        try:
            with open(full, "rb") as h:
                data = h.read()
            binary = b"\0" in data[:8000]
            lines = [] if binary else data.decode("utf-8", "replace").split("\n")
        except OSError:
            continue
        rows.append((f, "новый файл", [], lines, binary))
    return rows


def gates(rows):
    found = []
    for path, hunk, removed, added, binary in rows:
        tags = path_tags(path)
        if binary and tags:
            tags = [t + " (двоичный)" for t in tags]
        if not binary:
            tags += hunk_tags(removed, added)
        if tags:
            found.append({"file": path, "hunk": hunk, "tags": tags})
    return found


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--changed", nargs="?", const="HEAD~1", metavar="ХЕШ", help="закоммиченное от хеша до HEAD")
    ap.add_argument("--root", default=".", help="папка проекта или копии")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    code, out, _ = git(root, "rev-parse", "--is-inside-work-tree")
    if code or out.strip() != "true":
        print(f"Итог: нет git в {root} — мерить нечем.")
        return 2
    if args.changed:
        code, _, err = git(root, "rev-parse", "--verify", "--quiet", args.changed + "^{commit}")
        if code:
            print(f"Итог: нет коммита {args.changed} — неверный вызов.")
            return 2
        _, diff, _ = git(root, "diff", "--no-color", f"{args.changed}..HEAD")
        rows = parse(diff)
    else:
        _, diff, _ = git(root, "diff", "--no-color", "HEAD")
        rows = parse(diff) + untracked(root)
    found = gates(rows)
    if args.json:
        print(json.dumps({"gates": found, "count": len(found)}, ensure_ascii=False))
        return 0
    for g in found:
        print(f"ворота: {g['file']} @{g['hunk']} — {' · '.join(g['tags'])}")
    files = len({g["file"] for g in found})
    if found:
        print(f"Итог: ворот {len(found)} в {files} файлах — прочитать каждые и назвать в итоге, "
              "не ослаблена ли проверка.")
    else:
        print("Итог: ворот нет — правок проверок, оснастки и эталонов в diff нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
