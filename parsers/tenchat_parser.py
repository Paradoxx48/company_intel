#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tenchat_parser.py — карточка компании на TenChat с файловым кешем по ОГРН.

Страница https://tenchat.ru/<ОГРН> отдаёт Nuxt-payload (__NUXT_DATA__):
ЕГРЮЛ-блок (полное название, адрес, ИНН/ОГРН, дата создания, отрасль, численность,
рейтинг, директор с датой назначения). Страница /<ОГРН>/employees — видимые гостю
сотрудники (ФИО, должность, ссылка на профиль).

Кеш: .tenchat_cache/<ОГРН>.json, TTL 30 дней — повторные проходы не дёргают сайт.
Освежить кеш: tenchat_parser.py <ОГРН> --refresh

Usage: tenchat_parser.py <ОГРН> [--refresh]
Выход: JSON на stdout.
"""
import sys, re, json, os, time, datetime, urllib.request, urllib.error

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0'}
import sys as _sys, os as _sys_os
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configs'))
from config import CACHE_DIR
TTL_DAYS = 30
NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>([\s\S]*?)</script>')


def fetch_page(ogrn, suffix=''):
    """GET страницы tenchat.ru/<ОГРН><suffix>, разбор Nuxt-payload. Пусто — если payload нет."""
    url = f'https://tenchat.ru/{ogrn}{suffix}'
    req = urllib.request.Request(url, headers=UA)
    try:
        html = urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'tenchat HTTP {e.code} для {url}')
    m = NUXT_RE.search(html)
    if not m:
        raise RuntimeError(f'tenchat: NO NUXT DATA ({url}, len={len(html)})')
    return json.loads(m.group(1))


def _resolve_idx(data, v):
    """int-индекс в общий Nuxt-массив -> значение (2 уровня: dict{name: int→строка})."""
    for _ in range(2):
        if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < len(data):
            v = data[v]
        else:
            break
    if isinstance(v, dict):
        nm = v.get('name')
        if isinstance(nm, int) and 0 <= nm < len(data):
            nm = data[nm]
        v = nm
    return v


def resolve(data):
    """Достаём компанию и директора из Nuxt-массива."""
    out = {'company': {}, 'directors': []}
    for d in data:
        if not isinstance(d, dict):
            continue
        if 'shortName' in d and 'inn' in d:
            c = out['company']
            for k in ('shortName', 'fullName', 'address', 'inn', 'ogrn', 'creationDate',
                      'employeeCount', 'employeeCountDate', 'cityName', 'rating',
                      'reliabilityRating', 'mainDescription', 'partnerStatus'):
                v = _resolve_idx(data, d.get(k))
                if isinstance(v, str) and v.strip():
                    c[k] = v.strip()
                elif isinstance(v, (int, float)):
                    c[k] = v
            act = _resolve_idx(data, d.get('mainActivityType'))
            if isinstance(act, dict):
                c['mainActivity'] = _resolve_idx(data, act.get('name'))
        if 'name' in d and 'positionName' in d:
            parts = []
            for v in (d.get('surname'), d.get('name'), d.get('patronymic')):
                v = _resolve_idx(data, v)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            out['directors'].append({
                'name': ' '.join(parts),
                'position': _resolve_idx(data, d.get('positionName')),
                'date': _resolve_idx(data, d.get('date')),
                'inn': _resolve_idx(data, d.get('inn')),
            })
    return out


def parse_employees(data):
    """Сотрудники со страницы /<ОГРН>/employees (решение 2026-09-09: ФИО+должность+ссылка
    на профиль каждого видимого сотрудника идут в раздел «Контакты» карточки).
    TenChat отдаёт гостю лишь часть списка — сверять с employeeCount."""
    emps = {}
    for d in data:
        if not isinstance(d, dict) or 'name' not in d:
            continue
        uname = _resolve_idx(data, d.get('username'))
        fio_parts = []
        for k in ('surname', 'name', 'patronymic'):
            v = _resolve_idx(data, d.get(k))
            if isinstance(v, str) and v.strip():
                fio_parts.append(v.strip())
        pos = _resolve_idx(data, d.get('position') or d.get('positionName'))
        if not fio_parts or not (uname or pos):
            continue
        key = ' '.join(fio_parts)
        emps.setdefault(key, {
            'name': key,
            'position': pos.strip() if isinstance(pos, str) and pos.strip() else None,
            'profile': f'https://tenchat.ru/{uname}' if uname else None,
        })
    return list(emps.values())


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: tenchat_parser.py <ОГРН> [--refresh]'}))
        sys.exit(1)
    ogrn = re.sub(r'\D', '', sys.argv[1])
    refresh = '--refresh' in sys.argv
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache = os.path.join(CACHE_DIR, f'{ogrn}.json')
    if os.path.isfile(cache) and not refresh:
        age = time.time() - os.path.getmtime(cache)
        if age < TTL_DAYS * 86400:
            doc = json.load(open(cache))
            doc['cache'] = {'hit': True, 'age_days': round(age / 86400, 1),
                            'fetched': doc.get('cache', {}).get('fetched')}
            print(json.dumps(doc, ensure_ascii=False, indent=2))
            return
    try:
        doc = resolve(fetch_page(ogrn))
        try:
            doc['employees'] = parse_employees(fetch_page(ogrn, '/employees'))
        except Exception as e:
            doc['employees'] = []
            doc['employees_error'] = f'{type(e).__name__}: {str(e)[:80]}'
        doc['cache'] = {'hit': False, 'fetched': datetime.datetime.now().isoformat(timespec='seconds')}
        with open(cache, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(json.dumps({'error': f'tenchat: {type(e).__name__}: {str(e)[:150]}'}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(doc, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

# by sichkarenkomax