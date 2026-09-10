#!/usr/bin/env python3
"""checko_retry_loop.py — ретрай-цикл Checko для батча разведок (шаблон из SKILL.md п.1).

Правило (SKILL.md): батчи 3+ компаний при 429 Checko — ретрай-цикл с паузой 300 с,
повторяет только упавшие, до 3 кругов. При полном провале — сообщить пользователю
(капча решается вручную) и продолжать конвейер без Checko.

Список компаний передаётся файлом (одна на строку: `<ИНН> <имя>` или просто `<ИНН>`)
или аргументами: checko_retry_loop.py <ИНН1> [ИНН2 ...] [--circles N] [--pause S]
Папка разведки ищется по маске {BASE_DIR}/*_<ID> (стандартное имя
YYMMDD_<Название>_<ИНН|ОГРН>) — передавай тот идентификатор, что в имени папки.
Результат каждого запроса пишется в checko_raw.txt папки.

Вывод: построчно `try<N> <ИНН>: OK|FAIL`, финал ALL-DONE / FINISHED.
"""
import sys, os, re, json, glob, time, subprocess

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'configs'))
from config import BASE_DIR, PYTHON_BIN
PARSER = PYTHON_BIN
PARSER_SCRIPT = os.path.join(BASE_DIR, 'parsers', 'requisites_parser.py')
BASE = BASE_DIR


def load_pairs(args):
    """(<ИНН>, <имя или None>) из аргументов: ИНН или 'ИНН Имя' или файл-список."""
    pairs = []
    for a in args:
        if os.path.isfile(a):
            for line in open(a, encoding='utf-8'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(maxsplit=1)
                pairs.append((re.sub(r'\\D', '', parts[0]), parts[1] if len(parts) > 1 else None))
        elif a.isdigit():
            pairs.append((a, None))
    return pairs


def folder_of(inn):
    hits = glob.glob(f'{BASE}/*_{inn}') or glob.glob(f'{BASE}/*_{inn}_*')
    return hits[0] if hits else None


def already_ok(path):
    """checko_raw.txt без error и содержательный — повторять не нужно."""
    try:
        cur = open(path, encoding='utf-8').read()
    except OSError:
        return False
    return '"error"' not in cur and len(cur) > 200


def main():
    args = sys.argv[1:]
    circles = 3
    pause = 300
    if '--circles' in args:
        i = args.index('--circles')
        circles = int(args[i + 1]); args = args[:i] + args[i + 2:]
    if '--pause' in args:
        i = args.index('--pause')
        pause = int(args[i + 1]); args = args[:i] + args[i + 2:]
    pairs = load_pairs(args)
    if not pairs:
        print('Usage: checko_retry_loop.py <ИНН...> | <файл-список> [--circles N] [--pause S]')
        sys.exit(1)

    for attempt in range(1, circles + 1):
        pending = []
        for inn, name in pairs:
            folder = folder_of(inn)
            if not folder:
                print(f'try{attempt} {inn}: NO-FOLDER (в каталоге разведки нет папки, чьё имя заканчивается на этот ИД)', flush=True)
                pending.append((inn, name))
                continue
            path = os.path.join(folder, 'checko_raw.txt')
            if already_ok(path):
                continue
            r = subprocess.run([PARSER, PARSER_SCRIPT, inn],
                               capture_output=True, text=True, timeout=600)
            out = r.stdout if r.stdout.strip() else r.stderr
            with open(path, 'w', encoding='utf-8') as f:
                f.write(out)
            ok = '"error"' not in out and len(out) > 200
            print(f'try{attempt} {inn}: {"OK" if ok else "FAIL"}', flush=True)
            if not ok:
                pending.append((inn, name))
            if attempt < circles or (inn, name) != pairs[-1]:
                time.sleep(pause)  # пауза только между запросами, не после последнего
        if not pending:
            print('ALL-DONE', flush=True)
            return
        pairs = pending
    print('FINISHED (остались неудачные: ' + ', '.join(i for i, _ in pairs) + ')', flush=True)


if __name__ == '__main__':
    main()

# by sichkarenkomax