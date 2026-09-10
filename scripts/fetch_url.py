#!/usr/bin/env python3
"""fetch_url.py — простой fetch через requests (ручной запуск; в конвейере — recover_page/search_all).

Контракт: контент страницы в stdout; ошибки — в stderr + exit 1 (чтобы «ERROR:...»
не выглядел как содержимое страницы). Прокси уважается через http_proxy/https_proxy.

Usage: fetch_url.py <URL> [timeout] [--insecure]
"""
import os, sys
import requests
import urllib3

urllib3.disable_warnings()

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'}


def fetch(url, timeout=20, insecure=False):
    proxies = {}
    if os.getenv('http_proxy'):
        proxies['http'] = os.getenv('http_proxy')
    if os.getenv('https_proxy'):
        proxies['https'] = os.getenv('https_proxy')
    resp = requests.get(url, timeout=timeout, proxies=proxies, headers=UA,
                        verify=not insecure)
    resp.raise_for_status()
    return resp.text


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print('Usage: fetch_url.py <URL> [timeout] [--insecure]', file=sys.stderr)
        sys.exit(2)
    url = args[0]
    tmo = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
    insecure = '--insecure' in sys.argv
    try:
        print(fetch(url, tmo, insecure))
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {str(e)[:200]}', file=sys.stderr)
        sys.exit(1)

# by sichkarenkomax
