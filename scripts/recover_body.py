#!/usr/bin/env python3
"""recover_body.py — обёртка blocked-page-recovery: возвращает HTML-тело.

Режимы (флаги):
  (по умолчанию) — сырой HTML тела;
  --compact      — компактный текст для LLM (как recover_page.py);
  --clean        — сырой HTML с вырезанной wayback-шапкой (athena.js, COLLECTED BY,
                   captures/About this capture и пр.).

Актуальный контракт recover_page.py: тело пишется в файл через --out, stdout --json body не содержит.

Usage: recover_body.py <URL> [timeout] [--compact] [--clean]
Вывод: тело в stdout или NONE.
"""
import sys, os, re, json, subprocess, html as H

SKILL_RECOVER = '/root/.hermes/skills/web/blocked-page-recovery/scripts/recover_page.py'

WAYBACK_NOISE = (
    (re.compile(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', re.I), ' '),
    (re.compile(r'COLLECTED BY[^.]{0,200}\.'), ' '),
    (re.compile(r'Collection: Save Page Now Outlinks TIMESTAMPS[\s\S]{0,200}'), ' '),
    (re.compile(r'\d+\s+captures[\s\S]{0,3000}?About this capture'), ' '),
    (re.compile(r'(success\s+fail|About this capture|Wayback Machine)'), ' '),
)


def strip_wayback_noise(html_text):
    """Wayback-шапка (athena.js, COLLECTED BY, captures...) — служебный шум прочь."""
    for pat, repl in WAYBACK_NOISE:
        html_text = pat.sub(repl, html_text)
    return html_text


def compact_text(text, max_chars=12000):
    """HTML -> компактный текст для LLM (тот же контракт, что в recover_page.py/search_all.py)."""
    text = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = H.unescape(text)
    for pat, repl in WAYBACK_NOISE[1:]:  # wayback-шум (те же маркеры, что в recover_page.py)
        text = pat.sub(repl, text)
    text = H.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    seen, parts = set(), []
    for sent in re.split(r'(?<=[.!?;])\s+', text):
        k = sent.lower().strip()
        if k and k not in seen:
            seen.add(k)
            parts.append(sent)
    return ' '.join(parts)[:max_chars]


def get_body(url, timeout=60):
    os.makedirs('/tmp/search_recover', exist_ok=True)
    out_path = '/tmp/search_recover/' + re.sub(r'[^\w]', '_', url)[-120:] + '.html'
    cmd = [sys.executable, SKILL_RECOVER, url, '--json', '--out', out_path]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if cp.returncode != 0 or not os.path.exists(out_path):
            return None
        raw = open(out_path, encoding='utf-8', errors='replace').read()
        os.unlink(out_path)
        return raw or None
    except Exception:
        return None


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: recover_body.py <URL> [timeout] [--compact] [--clean]')
        sys.exit(1)
    url = sys.argv[1]
    args = [a for a in sys.argv[2:] if not a.startswith('--')]
    compact = '--compact' in sys.argv
    clean = '--clean' in sys.argv
    tmo = int(args[0]) if args and args[0].isdigit() else 60
    body = get_body(url, tmo)
    if body is None:
        print('NONE')
        sys.exit(0)
    if compact:
        # compact_text включает wayback-чистку; clean-проход до него только ломает script-пары
        body = compact_text(body)
    elif clean:
        body = strip_wayback_noise(body)
    print(body)

# by sichkarenkomax
