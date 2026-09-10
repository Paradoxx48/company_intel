#!/usr/bin/env python3
"""recover_page.py — обёртка для ручного восстановления заблокированной страницы
(скилл blocked-page-recovery: Wayback, archive.today, Jina Reader).

Возвращает JSON: {"recovered": bool, "body": "<компактный текст>" | null, "error"?: "..."}
Тело пишется скиллом в tempfile (--out), здесь читается и очищается (compact_text).
Примечание: в конвейере разведки используется search_all.py --fetch — он вызывает
скилловый recover_page.py напрямую. Эта обёртка — для ручного запуска.

Usage: recover_page.py <URL> [--json]
"""
import sys, json, os, re, subprocess
import html as H

SKILL_RECOVER = '/root/.hermes/skills/web/blocked-page-recovery/scripts/recover_page.py'


def compact_text(text, max_chars=12000):
    """HTML -> компактный текст для LLM (тот же контракт, что в search_all.py)."""
    text = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = H.unescape(text)
    # wayback-обёртка — служебный шум (как в search_all.py)
    text = re.sub(r'COLLECTED BY[^.]{0,200}\.', ' ', text)
    text = re.sub(r'Collection: Save Page Now Outlinks TIMESTAMPS[\s\S]{0,200}', ' ', text)
    text = re.sub(r'The group is 100% composed of volunteers[\s\S]{0,400}?\.', ' ', text)
    text = re.sub(r'\d+\s+captures[\s\S]{0,3000}?About this capture', ' ', text)
    text = re.sub(r'(success\s+fail|About this capture|Wayback Machine)', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    seen, parts = set(), []
    for sent in re.split(r'(?<=[.!?;])\s+', text):
        k = sent.lower().strip()
        if k and k not in seen:
            seen.add(k)
            parts.append(sent)
    return ' '.join(parts)[:max_chars]


def recover(url):
    tmpdir = '/tmp/search_recover'
    os.makedirs(tmpdir, exist_ok=True)
    out_path = os.path.join(tmpdir, re.sub(r'[^\w]', '_', url)[-120:] + '.html')
    cmd = [sys.executable, SKILL_RECOVER, url, '--json', '--out', out_path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return {'recovered': False, 'error': out.stderr.strip()[:200]}
        if not os.path.exists(out_path):
            return {'recovered': False, 'error': 'body file not written'}
        raw = open(out_path, encoding='utf-8', errors='replace').read()
        os.unlink(out_path)
        if not raw:
            return {'recovered': False, 'error': 'empty body'}
        return {'recovered': True, 'body': compact_text(raw)}
    except Exception as e:
        return {'recovered': False, 'error': f'{type(e).__name__}: {str(e)[:120]}'}


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: recover_page.py <URL> [--json]')
        sys.exit(1)
    url = sys.argv[1]
    result = recover(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))

# by sichkarenkomax
