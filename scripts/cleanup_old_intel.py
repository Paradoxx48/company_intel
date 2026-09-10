#!/usr/bin/env python3
"""cleanup_old_intel.py — ежедневная очистка папок разведки старше 5 дней.

Критерий возраста: mtime результата разведки (result_*.md) в папке; если результата
нет — mtime самой папки. Папка моложе 5 дней не трогается.
Удалённые папки логируются в {BASE_DIR}/cleanup.log.
Служебные пути (parsers, scripts, .tenchat_cache и файлы в корне) не трогаются.

Запуск: python3 cleanup_old_intel.py [--dry] [--retention N]
Решение пользователя 2026-09-08: retention 5 дней, удаление без подтверждения.
"""
import sys, os, re, shutil, datetime, glob, time

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'configs'))
from config import BASE_DIR
BASE = BASE_DIR
LOG = os.path.join(BASE, 'cleanup.log')
RETENTION_DAYS = 5
# папки-инфраструктура — не трогаем никогда
KEEP = {'parsers', 'scripts', '.tenchat_cache'}  # _archive*/dot-папки отсекаются ниже по префиксу


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(os.path.join(BASE, 'cleanup.log'), 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")


def folder_age_days(path):
    """Возраст папки по mtime результата разведки (result_*.md); если результата нет —
    по самой папке. Возвращает (age_days, источник_возраста)."""
    results = glob.glob(os.path.join(path, 'result_*.md'))
    if results:
        newest = max(os.path.getmtime(r) for r in results)
        src = 'result'
    else:
        newest = os.path.getmtime(path)
        src = 'folder'
    return (time.time() - newest) / 86400, src


def main():
    dry = '--dry' in sys.argv
    retention = RETENTION_DAYS
    if '--retention' in sys.argv:
        i = sys.argv.index('--retention')
        retention = int(sys.argv[i + 1])
    removed = []
    skipped_old = []
    if not os.path.isdir(BASE):
        print(f'ERROR: {BASE} не существует')
        sys.exit(1)
    for name in sorted(os.listdir(BASE)):
        path = os.path.join(BASE, name)
        if not os.path.isdir(path):
            continue  # файлы в корне (README.md, capabilities.json, cleanup.log...) не трогаем
        if name in KEEP or name.startswith('.') or name.startswith('_archive'):
            continue
        age, src = folder_age_days(path)
        if age > retention:
            removed.append((name, round(age, 1), src))
            if not dry:
                # удаление только если папка похожа на папку разведки (внутри result_*.md
                # или search-артефакты) — защита от случайного удаления чужой папки
                if not looks_like_intel(path):
                    log(f"SKIP {name}: не похоже на папку разведки (нет result_*/JSON-файлов)")
                    continue
                shutil.rmtree(path)
                log(f"DELETED {name}: возраст {age:.1f} дн (по {src})")
        elif age > retention - 2:
            skipped_old.append((name, round(age, 1)))

    if removed:
        log(f"retention={retention} дн" + (" (CLI)" if retention != RETENTION_DAYS else ""))
        for name, age, src in removed:
            print(f"DELETED: {name} ({age} дн, по {src})")
    else:
        print("NOTHING_TO_DELETE")
    if skipped_old:
        print(f"Скоро на удаление (3-5 дней): {', '.join(n for n, a in skipped_old)}")


def looks_like_intel(path):
    """Защита: внутри должна быть хотя бы карточка разведки или файлы с датами/логами."""
    for f in os.listdir(path):
        if f.startswith('result_') or f in ('recomendations.txt', 'debug.log') or \
           re.match(r'(search|checko|tochka|domain|hh|vk|tenchat|tgstat)', f):
            return True
    return False


if __name__ == '__main__':
    main()


# by sichkarenkomax
