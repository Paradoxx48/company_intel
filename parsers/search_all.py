#!/usr/bin/env python3
"""Unified search parser for DuckDuckGo (via DDGS), Google (SerpAPI), and Yandex (XML API).
Usage:
  python3 search_all.py "query" [engine] [max_pages] [--fetch]
Engines: ddgs (default), google, yandex.
If engine=google, the environment variable SERPAPI_KEY must be set.
If engine=yandex, YANDEX_USER and YANDEX_KEY must be set.
--fetch enables the blocked‑page‑recovery step for each result URL.
"""
import sys
import os
import json
import re
import subprocess
import html as H
import requests

# Path to the DDGS venv packages (installed earlier)
DDGS_VENV_PATH = os.getenv('COMP_INTEL_DDGS_PKGS', '')  # путь к site-packages с ddgs, если venv
if os.path.isdir(DDGS_VENV_PATH):
    sys.path.insert(0, DDGS_VENV_PATH)

try:
    from ddgs import DDGS
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'ddgs'])
    from ddgs import DDGS

DDGS_BACKENDS = ['auto', 'duckduckgo', 'google']  # ретрай ТОЛЬКО при ошибке; пусто = честный ответ
DDGS_REGION = 'ru-ru'  # us-en давал таймауты startpage и пустые выдачи

def search_ddgs(query, max_pages=3, per_page=20):
    """Поиск с ретраем и переключением бэкендов DDGS.
    При TimeoutException/RequestError (startpage и др.) — следующий бэкенд, не падать."""
    last_err = None
    for be in DDGS_BACKENDS:
        try:
            results = []
            with DDGS() as ddgs:
                for res in ddgs.text(query, max_results=max_pages * per_page,
                                     backend=be, region=DDGS_REGION):
                    results.append({
                        'title': res.get('title'),
                        'url': res.get('href'),
                        'snippet': res.get('body'),
                    })
                    if len(results) >= max_pages * per_page:
                        break
            if results:
                return results
            # пусто, но без ошибки — дальше по бэкендам не идём: 'No results' это честный
            # ответ, переключение бэкенда его не изменит (ревью 2026-09-08, экономия запросов)
            return results
        except Exception as e:
            if 'No results' in str(e):
                return []  # честный пустой ответ, не гоняем остальные бэкенды
            last_err = e
            continue  # сетевая ошибка — пробуем следующий бэкенд
    if last_err:
        # последняя ошибка наружу только если это не 'No results'
        if 'No results' in str(last_err):
            return []
        raise last_err
    return []

def search_google(query, max_pages=3, per_page=20):
    api_key = os.getenv('SERPAPI_KEY')
    if not api_key:
        raise RuntimeError('SERPAPI_KEY env var not set – cannot query Google via SerpAPI')
    results = []
    for page in range(1, max_pages + 1):
        start = (page - 1) * per_page
        params = {
            'engine': 'google',
            'q': query,
            'hl': 'ru',
            'gl': 'ru',
            'start': start,
            'num': per_page,
            'api_key': api_key,
        }
        resp = requests.get('https://serpapi.com/search', params=params, timeout=30)
        if resp.status_code != 200:
            continue
        data = resp.json()
        for r in data.get('organic_results', []):
            results.append({
                'title': r.get('title'),
                'url': r.get('link'),
                'snippet': r.get('snippet'),
            })
            if len(results) >= max_pages * per_page:
                break
        if len(results) >= max_pages * per_page:
            break
    return results

def search_yandex(query, max_pages=3, per_page=20):
    user = os.getenv('YANDEX_USER')
    key = os.getenv('YANDEX_KEY')
    if not user or not key:
        raise RuntimeError('YANDEX_USER/YANDEX_KEY env vars not set – cannot query Yandex XML API')
    import urllib.parse
    import xml.etree.ElementTree as ET
    results = []
    for page in range(1, max_pages + 1):
        start = (page - 1) * per_page + 1
        params = {
            'user': user,
            'key': key,
            'query': query,
            'lr': '213',  # Russia region code
            'sortby': 'rlv',
            'page': str(page),
        }
        url = 'https://yandex.com/search/xml?' + urllib.parse.urlencode(params)
        resp = requests.get(url, timeout=30)
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            continue
        for group in root.findall('.//response/grouping/group'):
            doc = group.find('doc')
            if doc is None:
                continue
            title_el = doc.find('title')
            url_el = doc.find('url')
            snippet_el = doc.find('snippet')
            results.append({
                'title': title_el.text if title_el is not None else None,
                'url': url_el.text if url_el is not None else None,
                'snippet': snippet_el.text if snippet_el is not None else None,
            })
            if len(results) >= max_pages * per_page:
                break
        if len(results) >= max_pages * per_page:
            break
    return results

def compact_text(text, max_chars=12000):
    """HTML/текст -> очищенный компактный текст для LLM (ревью 2026-09-08):
    script/style/nav/footer/forms прочь, видимый текст, whitespace-нормализация,
    дедуп дублирующихся строк, truncate."""
    text = re.sub(r'<script[\s\S]*?</script>', ' ', text, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', ' ', text, flags=re.I)
    text = re.sub(r'<noscript[\s\S]*?</noscript>', ' ', text, flags=re.I)
    text = re.sub(r'<(nav|footer|header|form)[\s\S]*?</\1>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = H.unescape(text)
    # Wayback-обёртка (toolbar/баннер) — служебный шум: чистим известные wayback-маркеры
    text = re.sub(r'COLLECTED BY[^.]{0,200}\.', ' ', text)
    text = re.sub(r'Collection: Save Page Now Outlinks TIMESTAMPS[\s\S]{0,200}', ' ', text)
    text = re.sub(r'The group is 100% composed of volunteers[\s\S]{0,400}?\.', ' ', text)
    text = re.sub(r'\d+\s+captures[\s\S]{0,3000}?About this capture', ' ', text)
    text = re.sub(r'(success\s+fail|About this capture|Wayback Machine)', ' ', text)
    # cookie-баннеры и согласия — шаблонный шум на 5-10% объёма типовых страниц
    text = re.sub(r'(?:мы|наш сайт)\s*(?:использ\w*|применяем)\s*cookie[^.]{0,200}?(?:\.|!|\?)', ' ', text, flags=re.I)
    text = re.sub(r'(?:this|our)\s+(?:site|website)\s+uses?\s+cookies?[^.]{0,200}?(?:\.|!|\?)', ' ', text, flags=re.I)
    text = re.sub(r'(?:принимаю|согласен|accept(?:ing)?|allow)\s*(?:все|all)?\s*cookie[^.]{0,120}', ' ', text, flags=re.I)
    text = re.sub(r'\b(?:cookie|cookie-файл[ы]?|файл[ы]?\s+cookie)\b[^.]{0,150}?(?:политик[аеи]|согласи[ея]|настройк[аи])[^.]{0,100}', ' ', text, flags=re.I)
    text = re.sub(r'Продолжая\s+(?:пользовать\w*|работать\w*)\s+сайтом[^.]{0,200}?(?:\.|!|\?)', ' ', text, flags=re.I)
    text = re.sub(r'By\s+(?:continuing|using)[^.]{0,200}?(?:\.|!|\?)', ' ', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    # дедуп дублирующихся строк/фраз: делим на предложения, чистим повторы
    seen, parts = set(), []
    for sent in re.split(r'(?<=[.!?;])\s+', text):
        key = sent.lower().strip()
        if key and key not in seen:
            seen.add(key)
            parts.append(sent)
    text = ' '.join(parts)
    return text[:max_chars]


def recover_page(url, tmpdir='/tmp/search_recover'):
    """Восстановление заблокированной страницы через recover_page.py (тело в tempfile
    через --out), затем compact_text. Возвращает ОЧИЩЕННЫЙ текст или None."""
    import tempfile, os
    script_path = '/root/.hermes/skills/web/blocked-page-recovery/scripts/recover_page.py'
    os.makedirs(tmpdir, exist_ok=True)
    out_path = os.path.join(tmpdir, re.sub(r'[^\w]', '_', url)[-120:] + '.html')
    cmd = [sys.executable, script_path, url, '--json', '--out', out_path]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            return None
        data = json.loads(completed.stdout)
        if not data.get('recovered'):
            return None
        if not os.path.exists(out_path):
            return None
        raw = open(out_path, encoding='utf-8', errors='replace').read()
        os.unlink(out_path)
        return compact_text(raw) if raw else None
    except Exception:
        return None

# Чёрный список агрегаторов-дубликатов (реквизиты/карточки компаний).
# Эти домены дублируют данные из ЕГРЮЛ/list-org и офиц. сайта — отсекаются на уровне парсера,
# чтобы не тратить токены на чтение и не попадать в search_filtered.
DOMAIN_BLACKLIST = {
    'rusprofile.ru', 'spark-interfax.ru', 'audit-it.ru', 'checko.ru',
    'focus.kontur.ru', 'kontur.ru', 'zachestnyibiznes.ru',
    'companies.rbc.ru', 'rbc.ru', 'synapsenet.ru', 'saby.ru',
    'cbonds.ru', 'kontragent.skrin.ru', 'skrin.ru', 'kartoteka.ru',
    'vbr.ru', 'kontragent.vbr.ru', 'tbank.ru', 'birweb.1prime.ru',
    '1prime.ru', 'star-pro.ru', 'b2b.house',
    'list-org.com',  # обрабатывается отдельным шагом процесса (шаг 16)
    'sudact.ru', 'kotelnich.appspot.com', 'booksite.ru',
}

def is_blacklisted(url):
    """Возвращает True, если домен URL в чёрном списке агрегаторов."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
        # убираем www.
        if host.startswith('www.'):
            host = host[4:]
        for bl in DOMAIN_BLACKLIST:
            if host == bl or host.endswith('.' + bl):
                return True
        return False
    except Exception:
        return False

def filter_results(results):
    """Отсекает чёрный список, сохраняя порядок."""
    filtered = []
    dropped = []
    for r in results:
        url = r.get('url') or ''
        if is_blacklisted(url):
            dropped.append(url)
        else:
            filtered.append(r)
    return filtered, dropped

def main():
    if len(sys.argv) < 2:
        print('Usage: search_all.py "query" [engine] [max_pages] [--fetch]')
        sys.exit(1)
    query = sys.argv[1]
    engine = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] in ('ddgs', 'google', 'yandex') else 'ddgs'
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 3
    fetch = '--fetch' in sys.argv

    if engine == 'ddgs':
        results = search_ddgs(query, max_pages)
    elif engine == 'google':
        results = search_google(query, max_pages)
    else:
        results = search_yandex(query, max_pages)

    # Отсекаем агрегаторы ДО вывода
    total_before = len(results)
    results, dropped = filter_results(results)
    total_after = len(results)

    if fetch:
        for r in results:
            body = recover_page(r['url'])
            r['body'] = body
    out = {
        'query': query, 'engine': engine,
        'stats': {'raw': total_before, 'filtered': total_after,
                  'dropped_by_blacklist': len(dropped)},
        'results': results,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()


# by sichkarenkomax
