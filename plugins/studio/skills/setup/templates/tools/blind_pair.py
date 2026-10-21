#!/usr/bin/env python3
"""Слепая пара: наш кадр и образец бок о бок без меток, сторона случайна.

Развёрнут плагином studio (/setup). Судит ревьюер — до подписей:
подпись «наш кадр» даёт симпатию, оценка растёт от круга к кругу, подпись
«образец» — чужую строгость; слепая пара убирает обе.

    python -X utf8 tools/blind_pair.py --ours shots/a.png \
        --ref "docs/refs/деревья/ref-valheim-1.png" --runs 2 \
        --out rounds/p2-слепая/

Папки прогонов `r1`, `r2`, …: в каждой `A.<расширение>` и `B.<расширение>`
— наш кадр и образец в случайном порядке, у каждого прогона свой.
Соответствие запечатано в `<out>/map.json` и не печатается. Порядок
честный: сначала записать в отчёт оба слепых вердикта («Слепая пара
r1: A лучше — …», «Слепая пара r2: …»), потом открыть `map.json` и назвать
итог: оба выбора наш кадр — «выиграна»; хоть один — образец или «не знаю»
— «не выиграна».

Расширения кадров различаются — оба пересохраняются в PNG (нужна Pillow:
`python -m pip install pillow`); совпадают — копии байт в байт, Pillow не
нужна.

Коды возврата: 0 — готово; 1 — брак: кадры одинаковы байт в байт, слепая
пара неосмысленна; 2 — нет файла, нет Pillow при разных расширениях или
неверные аргументы.
"""
import argparse
import json
import secrets
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Анонимные пары «наш кадр против образца» со случайной стороной.")
    parser.add_argument("--ours", required=True, help="главный кадр варианта")
    parser.add_argument("--ref", required=True,
                        help="кадр «Образца для листа» паспорта")
    parser.add_argument("--runs", type=int, default=2,
                        help="прогонов пар, 1–4 (умолчание 2)")
    parser.add_argument("--out", required=True,
                        help="папка прогонов и map.json")
    args = parser.parse_args()

    ours, ref = Path(args.ours), Path(args.ref)
    for frame in (ours, ref):
        if not frame.is_file():
            print(f"нет файла: {frame}", file=sys.stderr)
            return 2
    if not 1 <= args.runs <= 4:
        print("--runs: от 1 до 4", file=sys.stderr)
        return 2
    if ours.read_bytes() == ref.read_bytes():
        print("брак: кадры одинаковы байт в байт — слепая пара неосмысленна")
        return 1

    same_ext = ours.suffix.lower() == ref.suffix.lower()
    image = None
    if not same_ext:
        try:
            from PIL import Image
        except ImportError:
            print("нет Pillow, а расширения различаются — нормализовать нельзя:\n"
                  "  python -m pip install pillow", file=sys.stderr)
            return 2
        image = Image

    out = Path(args.out)
    # повторный запуск с меньшим --runs не оставляет старых прогонов
    if out.is_dir():
        for old in out.glob("r*"):
            if old.is_dir() and (not old.name[1:].isdigit() or int(old.name[1:]) > args.runs):
                shutil.rmtree(old)
    frames = {"наш": ours, "образец": ref}
    runs = {}
    for n in range(1, args.runs + 1):
        rdir = out / f"r{n}"
        rdir.mkdir(parents=True, exist_ok=True)
        ours_left = bool(secrets.randbits(1))
        letters = ({"A": "наш", "B": "образец"} if ours_left
                   else {"A": "образец", "B": "наш"})
        ext = ours.suffix if same_ext else ".png"
        for letter, whose in letters.items():
            dest = rdir / f"{letter}{ext}"
            if image is not None:
                with image.open(frames[whose]) as img:
                    img.save(dest)
            else:
                shutil.copyfile(frames[whose], dest)
        runs[f"r{n}"] = letters

    (out / "map.json").write_text(
        json.dumps({"наш": str(ours), "образец": str(ref), "прогоны": runs},
                   ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"слепая пара готова: {out}/r1..r{args.runs}; соответствие запечатано"
          f" в {out}/map.json — открыть после записи обоих вердиктов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
