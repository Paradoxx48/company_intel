#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tgstat_parser.py — данные Telegram-канала компании (TGStat + telegram-dialogs.ru).

Источники (по убыванию приоритета):
1. telegram-dialogs.ru/channel/@<user> — ОТКРЫТ (200 гостю, без CF): подписчики,
   описание, последние посты (дата, просмотры, текст; на живых каналах — до 10,
   пост-страниц у источника нет, url каждого поста = ссылка на канал). Пагинации нет.
2. tgstat.ru — под Cloudflare (403 гостю и ботовым UA); карточка канала и посты
   вытягиваются поисковыми сниппетами (DDGS 'site:tgstat.ru <user>').
t.me/s/<user> (TCP к Telegram заблокирован на хостинге) и Wayback CDX (снимки редки)
не используются.

Usage: tgstat_parser.py <username>
Выход: JSON на stdout.
"""
import sys, re, json, subprocess, urllib.request

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/126.0'}
import sys as _sys, os as _os
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configs'))
from config import BASE_DIR, PYTHON_BIN
SEARCH_ALL = os.path.join(BASE_DIR, 'parsers', 'search_all.py')


def ddgs(query):
    try:
        r = subprocess.run(['python3', SEARCH_ALL, query], capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout).get('results', [])
    except Exception:
        return []


def fetch(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
    except Exception as e:
        return ''


def clean(t):
    t = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', t)
    text = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', text)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: tgstat_parser.py <username>'}))
        sys.exit(1)
    user = sys.argv[1].lstrip('@')
    out = {'channel': {'username': f'@{user}',
                       'sources': [f'https://telegram-dialogs.ru/channel/@{user}',
                                   f'https://tgstat.ru/channel/@{user}',
                                   f'https://t.me/{user}']},
           'posts': [], 'notes': []}

    # 1) telegram-dialogs.ru — открыт, даёт подписчиков + 3 последних поста
    html = fetch(f'https://telegram-dialogs.ru/channel/@{user}')
    if html:
        text = clean(html)
        m = re.search(r'@%s\s*([\d.,]+[KM]?) подписчиков' % re.escape(user), text, re.I)
        if m:
            out['channel']['subscribers'] = m.group(1)
        dm = re.search(r'Добавлен (\d{2}\.\d{2}\.\d{4})', text)
        if dm:
            out['channel']['indexed_since'] = dm.group(1)
        desc = re.search(r'@%s\s*[\d.,]+[KM]? подписчиков\s*[^\n]*?(.+?)Добавлен' % re.escape(user), text, re.I)
        if desc:
            out['channel']['description'] = desc.group(1).strip()[:400]
        # пост-блоки идут подряд: дата/просмотры следующего поста = граница предыдущего
        pat = re.compile(r'(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})\s*·\s*👁\s*([\d.,]+K?)\s*')
        hits = list(pat.finditer(text))
        for i, pm in enumerate(hits):
            date, views = pm.group(1), pm.group(2)
            start = pm.end()
            end = hits[i + 1].start() if i + 1 < len(hits) else text.find('Похожие каналы', start)
            if end == -1:
                end = len(text)
            body = text[start:end].strip()
            if body.startswith('Л '):
                body = body[2:].strip()
            if len(body) > 5:
                out['posts'].append({'date': date, 'views': views,
                                     'text': body[:3000],
                                     'url': f'https://t.me/{user}'})
        if out['posts']:
            out['notes'].append('Источник: telegram-dialogs.ru (открыт, без Cloudflare); последние посты, пагинации нет.')
        else:
            out['notes'].append('telegram-dialogs.ru: карточка открыта, но пост-блок не распознан.')

    # 2) tgstat.ru через поисковые сниппеты (карточка + пост-страницы)
    seen_urls = {p['url'] for p in out['posts']}
    for r in ddgs(f'site:tgstat.ru {user}'):
        sn = r.get('snippet', '')
        if user.lower() not in (r['url'] or '').lower():
            continue
        if r['url'].rstrip('/') == f'https://tgstat.ru/channel/@{user}':
            if 'description' not in out['channel']:
                out['channel']['description'] = sn[:400]
        dm = re.search(r'([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2} [А-Яа-я]+ \d{4})', sn)
        pid = re.search(r'/channel/@%s/(\d+)' % re.escape(user), r['url'] or '')
        post_url = f'https://t.me/{user}/{pid.group(1)}' if pid else None
        if pid and dm and post_url not in seen_urls:
            seen_urls.add(post_url)
            out['posts'].append({'date': dm.group(1), 'views': None,
                                 'text': sn.replace(dm.group(1) + ' - ', '', 1)[:2000],
                                 'url': post_url})
    if any(p['url'] != f'https://t.me/{user}' for p in out['posts']):
        out['notes'].append('Дополнительно: пост-страницы из индекса tgstat.ru (Cloudflare блокирует прямой доступ).')
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
