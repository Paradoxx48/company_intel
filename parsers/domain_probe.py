#!/usr/bin/env python3
"""domain_probe.py — домены/поддомены/DNS/TLS/HTTP организации (шаг 5 разведки).

Два режима вывода (ревью 2026-09-08):
  по умолчанию — LLM-компактный JSON (без len/server/indent, пустые поля отброшены)
  --json       — полный технический JSON (indent, все поля)

Оптимизации:
- поддомены: минимальный probe (status/url/title) — телефоны/ИНН нужны только для root;
- DNS всех поддоменов и HTTP-пробы — параллельно (ThreadPoolExecutor, I/O-bound);
- wayback — только как discovery-fallback, если найдено <3 поддоменов;
- external_domains — только реально внешние (не сам домен и не его поддомены), опционально;
- AAAA добавлен к DNS;
- TLS: CN+SAN без возврата ошибок TLS в результат разведки;
- JSON: separators=(',', ':') — на 10-30% меньше.

Usage: domain_probe.py <domain1> [domain2 ...] [--json|--llm] [--tld-alts]
Вывод: LLM-компактный JSON по умолчанию; --json — полный технический.
--tld-alts: дополнительно проверяет <имя>.ru ↔ <имя>.com для каждого домена.
"""
import sys, re, json, socket, ssl, subprocess
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests, urllib3

urllib3.disable_warnings()
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
SUBDOMAINS = ['www', 'mail', 'smtp', 'ftp', 'portal', 'lk', 'docs', 'crm', 'b2b', 'shop',
              'store', 'edu', 'learn', 'support', 'help', 'cdn', 'api', 'dev', 'test', 'new',
              'webmail', 'ns1', 'git', 'wiki', 'intranet', 'exchange']


def log(msg):
    print(f"[domain_probe] {msg}", file=sys.stderr)


def dns_records(domain):
    """DNS: A/AAAA/MX/TXT/NS одним dig-вызовом (+noall +answer — тип в колонке)."""
    recs = {}
    try:
        out = subprocess.run(
            ['dig', '+noall', '+answer',
             domain, 'A', domain, 'AAAA', domain, 'MX', domain,
             domain, 'TXT', domain, 'NS', domain],
            capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            parts = line.split()
            # format: name ttl IN TYPE value
            if len(parts) < 5 or parts[2] != 'IN':
                continue
            rtype, value = parts[3], ' '.join(parts[4:])
            recs.setdefault(rtype, [])
            if value not in recs[rtype]:
                recs[rtype].append(value)
        for k in list(recs):
            recs[k] = recs[k][:5]
    except Exception:
        pass
    return recs


def tls_info(host, port=443, timeout=10):
    """TLS: CN + SAN. Ошибки TLS не попадают в результат разведки (ревью)."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                der = s.getpeercert(binary_form=True)
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix='.der') as f:
            f.write(der)
            path = f.name
        out = subprocess.run(['openssl', 'x509', '-in', path, '-noout', '-subject', '-ext', 'subjectAltName'],
                             capture_output=True, text=True, timeout=10).stdout
        os.unlink(path)
        cn = re.search(r'subject.*?CN\s*=\s*([^\n/,]+)', out)
        san = re.findall(r'DNS:([^,\s]+)', out)
        return {'cn': cn.group(1).strip() if cn else None,
                'san': sorted(set(san))[:20]}
    except Exception as e:
        log(f"TLS {host}: {type(e).__name__}")
        return None


def probe_full(url, timeout=15):
    """Полный зонд для root-домена: статус, редирект, заголовок, текст, контакты, ИНН/ОГРН."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, verify=False, allow_redirects=True)
        html = r.text
        title = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I | re.S)
        desc = re.search(r'<meta[^>]*name=["\']description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        links = re.findall(r'href="(https?://[^"\']+)"', html)
        return {
            'status': r.status_code,
            'url': r.url,
            'title': title.group(1).strip()[:120] if title else None,
            'description': desc.group(1).strip()[:200] if desc else None,
            'phones': sorted(set(re.findall(r'(?:\+7|8)[\s\(\-]?\d{3}[\)\s\-]?\s?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}', html)))[:5],
            'emails': sorted(set(re.findall(r'[\w.\-]+@[\w.\-]+\.\w+', html)))[:5],
            'inn_ogrn': sorted(set(re.findall(r'(?:ИНН|ОГРН)[:\s]*(\d{10,15})', html)))[:5],
            '_ext_links': links,  # внутреннее поле, вычищается при выводе
        }
    except Exception as e:
        log(f"probe {url}: {type(e).__name__}")
        return {'error': f'{type(e).__name__}: {str(e)[:80]}'}


def wayback_subdomains(domain):
    """Wayback CDX: альтернативный способ найти поддомены (discovery-fallback)."""
    try:
        r = requests.get(f'http://web.archive.org/cdx/search/cdx?url=*.{domain}&output=text&fl=original&collapse=urlkey&limit=300',
                         timeout=25)
        hosts = sorted({urlparse(l).netloc for l in r.text.splitlines() if urlparse(l).netloc.endswith(domain)})
        return hosts
    except Exception:
        return []


def resolve_host(host):
    """DNS-резолв поддомена (getaddrinfo: A и AAAA, ревью). Возвращает IP или None."""
    try:
        infos = socket.getaddrinfo(host, 443)
        for info in infos:
            if info[0] == socket.AF_INET:
                return info[4][0]
        return infos[0][4][0] if infos else None
    except Exception:
        return None


def analyze_domain(dom, full_json=False):
    """Полный анализ одного домена. Возвращает dict для вывода."""
    res = {'domain': dom}
    res['dns'] = dns_records(dom)
    # TLS только если A-запись есть (иначе бессмысленно)
    if res['dns'].get('A'):
        res['tls'] = tls_info(dom)

    # root: полный зонд
    http = probe_full(f'https://{dom}/')
    if not http.get('status'):
        http = probe_full(f'http://{dom}/')
    res['http'] = http

    # поддомены: DNS параллельно -> HTTP только для живых
    hosts = [f'{sd}.{dom}' for sd in SUBDOMAINS]
    resolved = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(resolve_host, h): h for h in hosts}
        for fut in as_completed(futures):
            h = futures[fut]
            ip = fut.result()
            if ip:
                resolved[h] = ip
    # HTTP-зонды живых поддоменов параллельно (минимальные)
    subs = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for h, ip in resolved.items():
            futures[ex.submit(probe_full_first, h)] = h
        for fut in as_completed(futures):
            h = futures[fut]
            r = fut.result()
            if r and r.get('status') and 200 <= r['status'] < 300:
                entry = {'status': r['status']}
                if r.get('title'):
                    entry['title'] = r['title']
                subs[h] = entry
    if subs:
        res['subdomains'] = subs

    # wayback как discovery-fallback: только если типовые поддомены дали мало
    if len(subs) < 3:
        wb = wayback_subdomains(dom)
        if wb:
            res['wayback'] = wb[:20]

    # external_domains: только реально внешние (ревью) — только в full-режиме
    ext_links = http.get('_ext_links') or []
    if full_json and ext_links:
        root = dom.lower().removeprefix('www.')
        ext = sorted({
            urlparse(l).hostname
            for l in ext_links
            if urlparse(l).hostname
            and urlparse(l).hostname != root
            and not urlparse(l).hostname.endswith('.' + root)
        })[:15]
        if ext:
            res['external_domains'] = ext
    http.pop('_ext_links', None)
    return res


def probe_full_first(host):
    """Зонд поддомена: полный текст, но без извлечения контактов (для title)."""
    try:
        r = requests.get(f'https://{host}/', headers=UA, timeout=10, verify=False, allow_redirects=True)
        title = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.I | re.S)
        t = title.group(1).strip()[:80] if title else None
        # mojibake-детект: неправильная кодировка (title с 'Ð' подряд — битый UTF-8)
        if t and ('\u00d0' in t or '\u00d1' in t):
            t = None
        return {'status': r.status_code, 'title': t}
    except Exception:
        return None


def compact(res):
    """LLM-компактная версия: без пустых полей, без служебного."""
    out = {'domain': res['domain']}
    if res.get('dns'):
        out['dns'] = res['dns']
    if res.get('tls'):
        out['tls'] = res['tls']
    h = res.get('http') or {}
    if h.get('status'):
        site = {'status': h['status']}
        if h.get('url'):
            site['url'] = h['url']
        if h.get('title'):
            site['title'] = h['title']
        if h.get('description'):
            site['description'] = h['description']
        for k in ('phones', 'emails', 'inn_ogrn'):
            if h.get(k):
                site[k] = h[k]
        out['site'] = site
    if res.get('subdomains'):
        out['subdomains'] = res['subdomains']
    if res.get('wayback'):
        out['wayback'] = res['wayback']
    return out


def alt_tlds(dom):
    """Альтернативный TLD: <имя>.ru → <имя>.com (и наоборот). Кейс 2026-09-09: второй
    официальный домен найден только через DDGS «<имя> вакансии» — теперь проверяется сразу.
    Для зон, отличных от .ru/.com, альтернативу не добавляем (мусор)."""
    parts = dom.rsplit('.', 1)
    if len(parts) != 2:
        return []
    name, tld = parts
    if tld == 'ru':
        return [f'{name}.com']
    if tld == 'com':
        return [f'{name}.ru']
    return []


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    full_json = '--json' in sys.argv
    if not args:
        print('Usage: domain_probe.py <domain1> [domain2 ...] [--json|--llm] [--tld-alts]')
        sys.exit(1)
    if '--tld-alts' in sys.argv:
        extra = [alt for dom in args for alt in alt_tlds(dom.strip().lower())]
        args = list(dict.fromkeys(args + extra))
    results = {}
    for dom in args:
        dom = dom.strip().lower()
        res = analyze_domain(dom, full_json=full_json)
        results[dom] = res if full_json else compact(res)
    indent = 2 if full_json else None
    print(json.dumps(results, ensure_ascii=False, separators=(',', ':'), indent=indent))


if __name__ == '__main__':
    main()


# by sichkarenkomax
