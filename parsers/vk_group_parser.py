#!/usr/bin/env python3
"""Парсер сообщества ВКонтакте через VK API (groups.getById + wall.get + users.get).

Требования:
- VK_SERVICE_TOKEN (сервисный ключ приложения ВК) в окружении или в конфигурационном env-файле (ENV_VK из configs/config.py)
- Без токена API недоступен ("Access denied: token required"), html сайта закрыт login-wall —
  проверено 2026-09-02: и desktop, и m.vk.com возвращают пустой SPA-скелет.

Что собирает (соответствует процессу разведки):
- группа: название, описание, количество участников, ссылка
- стена: до N постов, ≤2 лет, ≤20000 символов суммарно, со ссылками на посты
- участники: только при наличии токена с правами groups.members (по умолчанию пропускается)

Usage: vk_group_parser.py <group_id|screen_name> [posts_limit] ["<название компании>"]
Вывод: JSON на stdout. Третий аргумент (название компании) включает валидацию группы.
"""
import sys, os, re, json, time
import requests

API = 'https://api.vk.com/method/'
V = '5.199'
UA = {'User-Agent': 'Mozilla/5.0 Chrome/124.0'}


def token():
    tok = os.getenv('VK_SERVICE_TOKEN') or os.getenv('VK_TOKEN')
    if not tok:
        from config import ENV_VK
        for p in (ENV_VK, os.path.join(os.path.expanduser('~'), '.env.vk')):
            if os.path.isfile(p):
                for line in open(p):
                    m = re.match(r'VK_(?:SERVICE_)?TOKEN=(\S+)', line.strip())
                    if m:
                        return m.group(1)
    return tok


def call(method, params, tok):
    params = dict(params, access_token=tok, v=V)
    r = requests.get(API + method, params=params, timeout=20, headers=UA)
    d = r.json()
    if 'error' in d:
        raise RuntimeError(f"VK API {method}: {d['error'].get('error_msg')}")
    return d['response']


PHONE_HINT = re.compile(r'(?:\+7|8)[\s\(\-]?\d{3}|\d[\s\-]\d{3}[\s\-]\d{2}')
EMAIL_HINT = re.compile(r'[\w.\-]+@[\w.\-]+\.\w+')


def words_of(name):
    """Значимые слова названия (≥3 симв) для валидации совпадения."""
    return re.findall(r'[a-zа-яё0-9]{3,}', (name or '').lower())


def validate_group(grp, expected_name):
    """Проверка соответствия группы ожидаемому названию компании (ревью 2026-09-08:
    без валидации resolve_by_search отдавал случайную группу по смысловому совпадению).
    Возвращает (ok, reason, weak): weak=True — односоставное название совпало словом,
    но это НЕ гарантирует принадлежность компании (тёзки с тем же словом — сверить вручную).
    Caller обязан допроверить слабое совпадение по описанию/подписчикам."""
    if not grp or not expected_name:
        return False, 'no_group_or_no_expected_name', False
    words = words_of(expected_name)
    gname = (grp.get('name') or '').lower()
    hay_words = set(re.findall(r'[a-zа-яё0-9]{3,}', gname + ' ' + (grp.get('screen_name') or '').lower()))
    if not words:
        return False, 'empty_expected_name', False
    if len(words) == 1:
        ok = words[0] in hay_words
        reason = '' if ok else f'слово «{words[0]}» не в имени группы «{gname}»'
        return ok, reason, ok  # односоставное = всегда weak
    ok = all(w in hay_words for w in words)
    hits = sum(1 for w in words if w in hay_words)
    reason = '' if ok else f'name mismatch: группа «{gname}» vs «{expected_name}» ({hits}/{len(words)} слов)'
    return ok, reason, False


def resolve_by_search(name, tok):
    """Fallback: если screen_name не найден через API — искать id группы по DDGS 'site:vk.com <название>'."""
    import subprocess
    for q in (f'site:vk.com {name}', f'site:vkontakte.ru {name}'):
        try:
            r = subprocess.run([_sys.executable, os.path.join(_BD, 'parsers', 'search_all.py'), q],
                               capture_output=True, text=True, timeout=120)
            res = json.loads(r.stdout).get('results', [])
        except Exception:
            continue
        for item in res:
            m = re.search(r'vk\.com/(?:public|club)(-?\d+)', item['url'])
            if not m:
                m = re.search(r'vk\.com/wall(-\d+)_', item['url'])
            if not m:
                continue
            gid2 = m.group(1).lstrip('-')
            # проверка, что группа соответствует названию (по имени или URL-алиасу)
            try:
                g = call('groups.getById', {'group_id': gid2, 'fields': 'name,screen_name'}, tok)
                grp0 = g['groups'][0]
            except RuntimeError:
                continue
            ok, reason, weak = validate_group(grp0, name)
            if ok and not weak:  # слабое (1-словное) совпадение не считаем подтверждением
                return grp0
    return None


def main():
    if len(sys.argv) < 2:
        print('Usage: vk_group_parser.py <group_id|screen_name> [posts_limit] [expected_company_name]')
        sys.exit(1)
    gid = sys.argv[1]
    m = re.match(r'^(?:public|club)(\d+)$', gid)
    if m:
        gid = m.group(1)
    posts_limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    # expected_name: название компании для валидации группы (арг 3, опционально).
    # Без него случайное совпадение алиаса не отсекается! (ревью 2026-09-08)
    expected = sys.argv[3] if len(sys.argv) > 3 else None
    tok = token()
    if not tok:
        print(json.dumps({'error': 'VK token not found. Put VK_SERVICE_TOKEN into env or the configured env file',
                          'hint': 'https://dev.vk.com/ru/api/access-token/getting-started'}, ensure_ascii=False))
        sys.exit(2)
    out = {'group': None, 'posts': []}
    # кириллические имена API не принимает — сразу в поиск; латиницу/число пробуем напрямую
    grp = None
    if gid.isascii():
        try:
            g = call('groups.getById', {'group_id': gid, 'fields': 'members_count,description,contacts,links'}, tok)
            grp = g['groups'][0]
        except RuntimeError as e:
            grp = None
    if grp is None:
        # fallback: поиск id по названию
        found = resolve_by_search(gid, tok)
        if found is None:
            print(json.dumps({'error': f'group not resolved for "{gid}": search fallback failed'},
                             ensure_ascii=False))
            sys.exit(3)
        grp = call('groups.getById', {'group_id': found['id'],
                                      'fields': 'members_count,description,contacts,links'}, tok)['groups'][0]
    # Валидация имени группы против ожидаемого названия компании (ревью 2026-09-08)
    if expected:
        ok, reason, weak = validate_group(grp, expected)
        if not ok:
            out['validation'] = {'passed': False, 'reason': reason}
            out['candidate_group'] = {'name': grp.get('name'), 'screen_name': grp.get('screen_name'),
                                      'url': f"https://vk.com/{grp.get('screen_name') or grp.get('id')}"}
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return
        out['validation'] = {'passed': True, 'weak_match': weak,
                             'note': ('односоставное название — сверь описание/подписчиков вручную'
                                      if weak else '')}
    # если expected не передан — фиксируем в JSON, что валидации не было
    if not expected:
        out['validation'] = {'passed': None, 'reason': 'expected_name not provided — группа не проверена'}
    contacts = []
    for c in (grp.get('contacts') or []):
        contacts.append({
            'user_id': c.get('user_id'),
            'url': f"https://vk.com/id{c['user_id']}" if c.get('user_id') else None,
            'desc': c.get('desc'),
            'email': c.get('email'),
            'phone': c.get('phone'),
        })
    out['group'] = {
        'id': grp.get('id'), 'screen_name': grp.get('screen_name'),
        'name': grp.get('name'), 'description': (grp.get('description') or '')[:500],
        'members_count': grp.get('members_count'), 'url': f"https://vk.com/{grp.get('screen_name') or gid}",
        'contacts': contacts,
    }
    wall = call('wall.get', {'owner_id': -grp['id'], 'count': min(posts_limit, 100), 'filter': 'owner'}, tok)
    cutoff = time.time() - 2 * 365.25 * 86400  # посты ≤2 лет (SKILL.md п.7)
    seen_ids, total_chars = set(), 0
    for item in wall.get('items', []):
        if (item.get('date') or 0) < cutoff:
            continue
        pid = item.get('id')
        if pid in seen_ids:  # дедуп: закреплённый пост может дублироваться (filter: owner)
            continue
        seen_ids.add(pid)
        text = item.get('text') or ''
        if total_chars + len(text) > 20000:
            break
        total_chars += len(text)
        # хвост поста важен для закупочных списков (контакты/потребности в конце):
        # если в первых 500 симв есть телефон/почта — пост-«оглавление», берём до 1000
        head = text[:500]
        lim = 1000 if (PHONE_HINT.search(head) or EMAIL_HINT.search(head)) else 500
        out['posts'].append({
            'id': item.get('id'),
            'date': item.get('date'),
            'text': text[:lim],
            'url': f"https://vk.com/wall-{grp['id']}_{item.get('id')}",
            'likes': (item.get('likes') or {}).get('count'),
            'reposts': (item.get('reposts') or {}).get('count'),
            'views': (item.get('views') or {}).get('count'),
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
