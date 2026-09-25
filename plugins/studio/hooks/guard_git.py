#!/usr/bin/env python3
r"""PreToolUse-хук плагина studio: не дать одной сессии стереть или забрать чужую работу.

В папке проекта одновременно работают два чата на одной ветке, и у чата
замысла часто лежат незакоммиченные .md. Поэтому в проектах studio (есть
docs/BATCH.md в cwd или выше) хук останавливает git-команды, которые берут
или стирают всё разом:

  git add -A | --all | -u | .         git commit -a | .
  git stash (любой), --autostash      git clean
  git reset --hard                    git checkout . / -- .   git restore .
  git checkout -f, git switch -f      git push --force | -f | --force-with-lease | +ветка

`reset --hard`, `clean` и принудительное переключение разрешены в копиях
проекта (путь в -C, cd или cwd содержит `.wt/` или `.wt\`). `git checkout --
<файл>` поимённо разрешён везде. Команда разбирается по словам: текст
сообщения коммита, кавычки и here-doc не путаются с командами.

Код 2 блокирует вызов, текст из stderr уходит модели. Любая собственная
ошибка хука — код 0: он не должен мешать работе.
"""
import json
import os
import re
import sys

TWO_CHATS = ("в этой папке работают две сессии (чат замысла и чат разработки) на "
             "одной ветке, у чата замысла бывают незакоммиченные .md — такая "
             "команда забирает или стирает всё разом, и его работу тоже")
COPY = "../<папка>.wt/…"
RULES = {
    "add_all": ("git add -A / --all / -u / «.»", TWO_CHATS,
                "называть файлы поимённо: git add <файл> <файл>"),
    "commit_all": ("git commit -a или коммит «.»", TWO_CHATS,
                   "git add <файл> <файл>, затем git commit -m … без -a"),
    "stash": ("git stash", "stash забирает все незакоммиченные файлы папки, "
              "в том числе .md чата замысла, и он один на основную папку и все копии",
              "нужен прежний вид файла (например, доказать красное до правки) — "
              "отложить копию своего файла рядом, вернуть исходный "
              "(git show HEAD:<файл>), затем свой; отказ push — git fetch и "
              "git merge --no-edit origin/<ветка>"),
    "clean": ("git clean", TWO_CHATS,
              "удалить созданные пунктом файлы поимённо; в копии " + COPY + " clean можно"),
    "reset_hard": ("git reset --hard", TWO_CHATS,
                   "вернуть свои файлы поимённо: git checkout -- <файл>; убрать коммит — "
                   "git revert --no-edit <хеш>; в копии " + COPY + " reset --hard можно"),
    "discard_all": ("git checkout . / git restore . — все файлы разом", TWO_CHATS,
                    "вернуть файлы поимённо: git checkout -- <файл> <файл>"),
    "force_switch": ("переключение с -f / --discard-changes", TWO_CHATS,
                     "сначала закоммитить или вернуть поимённо свои файлы, потом "
                     "переключаться без -f; в копии " + COPY + " можно"),
    "push_force": ("git push --force / -f / --force-with-lease / +ветка",
                   "в ту же ветку коммитят оба чата — перезапись истории на сервере "
                   "сотрёт чужие коммиты",
                   "обычный git push; отказ — git fetch, git merge --no-edit "
                   "origin/<ветка> (pull --rebase не пойдёт при чужих незакоммиченных "
                   ".md) и снова push; конфликт — остановиться и описать владельцу"),
}

PREFIXES = {"sudo", "command", "exec", "time", "nohup", "env", "builtin", "noglob",
            "then", "do", "else", "elif", "if", "while", "until", "!"}
SHELLS = {"bash", "sh", "zsh", "dash", "pwsh", "powershell", "cmd", "eval",
          "invoke-expression", "iex"}
CD = {"cd", "chdir", "pushd", "set-location", "sl", "push-location"}
# Опции со значением отдельным словом: глобальные git и по подкомандам.
GLOBAL_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                "--config-env", "--super-prefix", "--exec-path"}
SHORT_VALUE = {"commit": "mFCct", "push": "o", "checkout": "bB", "restore": "s",
               "switch": "cC", "reset": "", "add": "", "clean": "e", "stash": "m"}
LONG_VALUE = {"--message", "--file", "--reuse-message", "--reedit-message", "--author",
              "--date", "--template", "--fixup", "--squash", "--trailer", "--cleanup",
              "--pathspec-from-file", "--push-option", "--repo", "--receive-pack",
              "--exec", "--source", "--orphan", "--create", "--force-create",
              "--exclude", "--chmod"}


def split_commands(text):
    """Разбить строку bash или PowerShell на простые команды — списки слов."""
    commands, words, word = [], [], None
    heredocs, i, n = [], 0, len(text)

    def end_word():
        nonlocal word
        if word is not None:
            words.append("".join(word))
        word = None

    def end_command():
        nonlocal words
        end_word()
        if words:
            commands.append(words)
        words = []

    while i < n:
        c = text[i]
        if c == "'":
            j = text.find("'", i + 1)
            j = n if j < 0 else j
            word = (word or []) + [text[i + 1:j]]
            i = j + 1
        elif c == '"':
            j, depth, buf = i + 1, 0, []
            while j < n and not (text[j] == '"' and depth == 0):
                if text[j] in "\\`" and text[j + 1:j + 2] == '"':
                    buf.append('"')
                    j += 2
                    continue
                if text.startswith("$(", j):
                    depth += 1
                    buf.append("$")
                    j += 1
                elif text[j] == "(" and depth:
                    depth += 1
                elif text[j] == ")" and depth:
                    depth -= 1
                buf.append(text[j])
                j += 1
            word = (word or []) + ["".join(buf)]
            i = j + 1
        elif c == "@" and word is None and text[i + 1:i + 2] in ("'", '"') \
                and text[i + 2:i + 3] in ("\n", "\r"):
            quote = text[i + 1]  # here-string PowerShell: @' … '@
            end = text.find("\n" + quote + "@", i + 2)
            end = n if end < 0 else end
            word = [text[i + 2:end]]
            i = end + 3
        elif text.startswith("<<", i) and not text.startswith("<<<", i):
            end_word()  # here-doc: тело — не команды
            m = re.compile(r"-?\s*(['\"]?)\\?([^\s'\";&|<>()]+)\1").match(text, i + 2)
            if m:
                heredocs.append(m.group(2))
                i = m.end()
            else:
                i += 2
        elif c == "`" and text[i + 1:i + 2] in ("\n", "\r"):
            i += 3 if text[i + 1:i + 3] == "\r\n" else 2  # перенос строки PowerShell
        elif c == "\\" and text[i + 1:i + 2] in ("\n", "\r"):
            i += 3 if text[i + 1:i + 3] == "\r\n" else 2  # перенос строки bash
        elif c in " \t\r":
            end_word()
            i += 1
        elif c == "&" and ("".join(word or [])[-1:] in ("<", ">") or text[i + 1:i + 2] == ">"):
            word = (word or []) + [c]  # 2>&1, &>
            i += 1
        elif c in ";&|\n()`" or (c == "{" and word is None) or \
                (c == "}" and not (word and "{" in "".join(word))):
            end_command()
            i += 1
            if c == "\n":
                for delim in heredocs:
                    while i < n:
                        eol = text.find("\n", i)
                        eol = n if eol < 0 else eol
                        line, i = text[i:eol].strip(), eol + 1
                        if line == delim:
                            break
                heredocs = []
        else:
            word = (word or []) + [c]
            i += 1
    end_command()
    return commands


def join(base, path):
    return os.path.normpath(os.path.join(base, path)) if path else base


def is_copy(path):
    return ".wt/" in path.replace("\\", "/") + "/"


def whole_tree(pathspec):
    """Путь, который значит «все файлы»: ., ./, *, :/, :(top) и т. п."""
    p = pathspec.replace("\\", "/")
    m = re.match(r":(\([^)]*\)|/)?", p)
    if m:
        p = p[m.end():]
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/") in ("", ".", "*")


def parse_args(sub, args):
    """Флаги (короткие буквы и длинные имена) и позиционные слова подкоманды."""
    flags, positional, i = set(), [], 0
    short_value = SHORT_VALUE.get(sub, "")
    while i < len(args):
        a = args[i]
        i += 1
        if re.match(r"^\d*(>>?|<)", a) or a.startswith("&>"):
            if re.fullmatch(r"\d*(>>?|<)|&>>?", a):
                i += 1  # имя файла перенаправления
            continue
        if a == "--":
            positional += args[i:]
            break
        if a.startswith("--"):
            name = a.split("=", 1)[0]
            flags.add(name[2:])
            if "=" not in a and name in LONG_VALUE:
                i += 1
        elif a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:]):
                flags.add(ch)
                if ch in short_value:
                    if k == len(a) - 2:
                        i += 1
                    break
        else:
            positional.append(a)
    return flags, positional


def check_git(args, cwd):
    where, i = cwd, 0
    while i < len(args) and args[i].startswith("-"):
        opt = args[i].split("=", 1)
        if opt[0] in ("-C", "--work-tree"):
            value = opt[1] if len(opt) > 1 else (args[i + 1] if i + 1 < len(args) else "")
            where = join(where, value)
        i += 1 if len(opt) > 1 or opt[0] not in GLOBAL_VALUE else 2
    if i >= len(args):
        return None, where
    sub, (flags, pos) = args[i], parse_args(args[i], args[i + 1:])
    copy = is_copy(where)
    if sub == "add" and (flags & {"A", "all", "u", "update", "no-ignore-removal"}
                         or any(map(whole_tree, pos))):
        return "add_all", where
    if sub == "commit" and (flags & {"a", "all"} or any(map(whole_tree, pos))):
        return "commit_all", where
    if sub == "stash" or (sub in ("pull", "rebase", "merge") and "autostash" in flags):
        return "stash", where
    if sub == "clean" and not copy:
        return "clean", where
    if sub == "reset" and "hard" in flags and not copy:
        return "reset_hard", where
    if sub in ("checkout", "restore") and any(map(whole_tree, pos)):
        return "discard_all", where
    if not copy and ((sub == "checkout" and flags & {"f", "force"} and "--" not in args)
                     or (sub == "switch" and flags & {"f", "force", "discard-changes"})):
        return "force_switch", where
    if sub == "push" and (flags & {"f", "force", "force-with-lease", "force-if-includes"}
                          or any(p.startswith("+") for p in pos)):
        return "push_force", where
    return None, where


def find_violations(command, cwd, depth=0):
    """Список (правило, папка) для всех git-вызовов в строке команды."""
    found = []
    for words in split_commands(command):
        while words and (words[0].lower() in PREFIXES
                         or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0])):
            words = words[1:]
        if not words:
            continue
        name = os.path.basename(words[0].replace("\\", "/")).lower()
        name = re.sub(r"\.(exe|cmd|bat)$", "", name)
        args = words[1:]
        if name in CD:
            target = [a for a in args if not a.startswith("-")]
            cwd = join(cwd, target[0]) if target else cwd
        elif name in SHELLS and depth < 3:
            for a in args:
                if not a.startswith("-") and not a.startswith("/"):
                    found += find_violations(a, cwd, depth + 1)
        elif name == "git":
            rule, where = check_git(args, cwd)
            if rule:
                found.append((rule, where))
    return found


def in_studio(path):
    """Есть docs/BATCH.md в папке или выше — проект развёрнут /setup."""
    path = os.path.abspath(path)
    while True:
        if os.path.isfile(os.path.join(path, "docs", "BATCH.md")):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def main():
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        # Claude Code присылает событие в UTF-8, а Python на Windows читает
        # stdin в кодировке системы (cp1251): кириллический путь превращался
        # в кракозябры, docs/BATCH.md «не находился», и хук молча пропускал всё.
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        return 0
    if event.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    cwd = event.get("cwd") or os.getcwd()
    rules = []
    for rule, where in find_violations(command, cwd):
        if rule not in rules and (in_studio(cwd) or in_studio(where)):
            rules.append(rule)
    if not rules:
        return 0
    lines = []
    for rule in rules:
        what, why, instead = RULES[rule]
        lines.append(f"studio: остановлено — {what}. Почему: {why}. Вместо: {instead}.")
    lines.append("См. CLAUDE.md проекта, «Два чата».")
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        if os.environ.get("STUDIO_HOOK_SELFTEST"):
            raise  # selftest.py отличает поломку хука от «пропустить»
        sys.exit(0)
