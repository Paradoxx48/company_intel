#!/usr/bin/env python3
"""Поиск вероятных сотрудников компании во ВКонтакте.

Стратегия (по процессу разведки, шаг «Профили вероятных сотрудников ВК»):
1. Основной путь: VK API users.search (q=название, city=ИД города) — требует
   ПОЛЬЗОВАТЕЛЬСКИЙ токен с правами. Сервисный токен не подходит:
   "method is unavailable with service token" (проверено 2026-09-02).
2. Резерв: DDGS по site:vk.com «<название> <город>» — находит публичные упоминания
   профилей/постов, но не полноценный поиск по работодателю.

URL-шаблоны веб-поиска (требуют браузер с залогиненным аккаунтом, приводятся в выводе
для ручной проверки):
- https://vk.ru/search/people?city_id=<ID>&company=<название кириллицей>
- https://vk.ru/search/people?company=<название без ОПФ>
- https://vk.ru/search/people?company=<название латиницей>
- https://vk.ru/search/people?company=<название + ОПФ>

Города: Москва=1, СПб=2, Волгоград=10, Владивосток=37, Воронеж=42, Екатеринбург=49,
Казань=60, Калининград=61, Краснодар=72, Красноярск=73, Нижний Новгород=95,
Новосибирск=99, Омск=104, Пермь=110, Ростов-на-Дону=119, Самара=123, Уфа=151,
Хабаровск=153, Челябинск=158, Севастополь=185, Симферополь=627.
ИД малых городов в списке «важных» отсутствует — брать из
database.getCities (доступен с сервисным токеном).

Usage: vk_people_search.py <Название> [ГОРОД] [латинское название] [ОПФ]
Вывод: JSON на stdout.
"""
import sys, os, re, json, urllib.parse as UP
import sys as _sys, os as _sys_os
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configs'))
from config import ENV_VK, BASE_DIR as _BD
import requests

API = 'https://api.vk.com/method/'
V = '5.199'

CITY_IDS = {
    'москва': 1, 'санкт-петербург': 2, 'волгоград': 10, 'владивосток': 37,
    'воронеж': 42, 'екатеринбург': 49, 'казань': 60, 'калининград': 61,
    'краснодар': 72, 'красноярск': 73, 'нижний новгород': 95, 'новосибирск': 99,
    'омск': 104, 'пермь': 110, 'ростов-на-дону': 119, 'самара': 123, 'уфа': 151,
    'хабаровск': 153, 'челябинск': 158, 'севастополь': 185, 'симферополь': 627,
}
OPF = ('АО', 'ООО', 'ОАО', 'ПАО', 'ЗАО')


def token():
    """users.search требует ПОЛЬЗОВАТЕЛЬСКИЙ токен: VK_USER_TOKEN приоритетнее,
    сервисный вернёт 'method is unavailable' (проверено)."""
    tok = (os.getenv('VK_USER_TOKEN') or os.getenv('VK_TOKEN')
           or os.getenv('VK_SERVICE_TOKEN'))
    if not tok:
        from config import ENV_VK
        for p in (ENV_VK, os.path.join(os.path.expanduser('~'), '.env.vk')):
            if os.path.isfile(p):
                for line in open(p):
                    m = re.match(r'VK_USER_TOKEN=(\S+)', line.strip())
                    if m:
                        return m.group(1)
                for line in open(p):
                    m = re.match(r'VK_(?:SERVICE_)?TOKEN=(\S+)', line.strip())
                    if m:
                        return m.group(1)
    return tok


def translit(s):
    table = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i',
             'й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t',
             'у':'u','ф':'f','х':'kh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'',
             'э':'e','ю':'yu','я':'ya'}
    return ''.join(table.get(ch, ch) for ch in s.lower())


def city_id_by_name(city):
    if not city:
        return None, None
    c = city.strip().lower()
    if c in CITY_IDS:
        return CITY_IDS[c], city
    # точный поиск через database.getCities (доступен сервисному токену)
    tok = token()
    if tok:
        r = requests.get(API + 'database.getCities',
                         params={'q': city, 'country_id': 1, 'count': 1,
                                 'access_token': tok, 'v': V}, timeout=20).json()
        items = r.get('response', {}).get('items')
        if items:
            return items[0]['id'], items[0]['title']
    return None, city


def api_search(q, city_id, tok):
    params = {'q': q, 'count': 30, 'fields': 'city,occupation', 'access_token': tok, 'v': V}
    if city_id:
        params['city'] = city_id
    r = requests.get(API + 'users.search', params=params, timeout=20).json()
    if 'error' in r:
        return None, r['error'].get('error_msg')
    return r['response'], None


def ddgs_fallback(name, city):
    import subprocess
    q = f'{name} {city} site:vk.com/id' if city else f'{name} site:vk.com/id'
    out = []
    try:
        r = subprocess.run([sys.executable,
                            os.path.join(_BD, 'parsers', 'search_all.py'), q, 'ddgs', '1'],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
        for x in d.get('results', []):
            u = x.get('url') or ''
            m = re.search(r'vk\.(?:com|ru)/(id\d+)', u)
            if m:
                out.append({'profile': f'https://vk.com/{m.group(1)}',
                            'title': x.get('title'), 'snippet': (x.get('snippet') or '')[:200]})
    except Exception as e:
        out = [{'error': f'ddgs fallback failed: {e}'}]
    # дедупликация по id профиля
    seen, uniq = set(), []
    for o in out:
        pid = o.get('profile')
        if pid and pid not in seen:
            seen.add(pid)
            uniq.append(o)
    return uniq


def main():
    if len(sys.argv) < 2:
        print('Usage: vk_people_search.py <Название> [ГОРОД] [латиница] [ОПФ]')
        sys.exit(1)
    name = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    latin = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else translit(name).capitalize()
    opf = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    def people_url(params):
        return 'https://vk.ru/search/people?' + UP.urlencode(params)

    cid, city_title = city_id_by_name(city)
    templates = []
    if cid:
        templates.append({'label': 'город + название кириллицей',
                          'url': people_url({'city_id': cid, 'company': name})})
    templates.append({'label': 'только название без ОПФ',
                      'url': people_url({'company': name})})
    templates.append({'label': 'название латиницей',
                      'url': people_url({'company': latin})})
    if opf:
        templates.append({'label': 'название + ОПФ',
                          'url': people_url({'company': f'{opf} {name}'})})

    tok = token()
    api_results, api_err = (None, None)
    if tok:
        api_results, api_err = api_search(name, cid, tok)

    mentions = ddgs_fallback(name, city or '')
    ddgs_err = next((m.pop('error') for m in mentions if 'error' in m), None)

    out = {
        'company': name, 'city': city_title or city, 'city_id': cid,
        'web_templates': templates,
        'ddgs_error': ddgs_err,
        'note': ('веб-поиск vk.ru/search/people требует залогиненную сессию (JS); '
                 'сервисный токен НЕ даёт users.search. Шаблоны выше — для ручной проверки в браузере.'),
        'api_error': api_err,
        'profiles': [],
        'mentions_via_ddgs': mentions,
    }
    if api_results:
        out['profiles'] = [{
            'id': u['id'], 'url': f"https://vk.com/id{u['id']}",
            'name': f"{u.get('first_name')} {u.get('last_name')}",
            'city': (u.get('city') or {}).get('title'),
            'occupation': (u.get('occupation') or {}).get('name'),
        } for u in api_results.get('items', [])]
        out['total'] = api_results.get('count')
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
