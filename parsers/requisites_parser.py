#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""requisites_parser.py — реквизиты/контакты/финансы/госзакупки с checko.ru (вход по ИНН или ОГРН).
Запуск: requisites_parser.py <ИНН|ОГРН>
Выход: JSON в stdout; диагностика — stderr и parsers/debug.log.
checko.ru открыт (без капчи); /search?query=<ID> редиректит на карточку /company/<слаг>-<ОГРН>.

Правки 2026-09-08 (ревью):
- detect_id(): ИНН/ОГРН/ОГРНИП по длине; fallback-и работают с верным идентификатором
  (ОГРН конвертируется в ИНН после Checko, find-org ищет только по ИНН);
- fallback вызывается при ЛЮБОЙ ошибке Checko (403/500/timeout, не только 429) и дозаполняет
  все пустые поля (address/capital/staff/founders_count/okved), а не только ceo/finance;
- мёртвый fin убран; финансы ищутся по заголовкам (value_after), а не позициям строк —
  устойчиво к изменениям HTML, IndexError исключён;
- директор: role хранится отдельно, ФИО-regex допускает дефисы/инициалы;
- адрес: Юридический адрес приоритетнее Предыдущего (previous_address отдельно);
- контакты нормализуются: телефон к +7XXXXXXXXXX, email lowercase, сайт к https://...;
- _get: retry с Retry-After, без бессмысленного sleep после последней попытки,
  обрабатывает ConnectionError/Timeout/5xx;
- _meta: источник каждого блока (checko/find-org/buxbalans) + data_quality;
- слаг не вычисляется (не использовался), urllib3.disable_warnings убран (verify не отключается).
"""
import sys, os, re, json, html as H, time, datetime, random, requests

import sys as _sys
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configs'))
from config import BASE_DIR, ENV_CHECKO, DEBUG_LOG
UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8',
    'Referer': 'https://checko.ru/',
    'Connection': 'keep-alive',
}
# 2026-09-07: checko.ru отдаёт 429 на запросы без браузерного набора заголовков (Accept/Accept-Language/Referer),
# капчи нет. С полным набором — 200 (проверено в ходе разведок).

NAME_RE = re.compile(r'^[А-ЯЁ][а-яё-]+(?:[- ][А-ЯЁ][а-яё-]+){1,2}$')


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] requisites_parser: {msg}\n")


def diag(msg):
    print(f"[requisites_parser] {msg}", file=sys.stderr)


def detect_id(value):
    """Тип идентификатора по длине: inn10/inn12/ogrn13/ogrnip15."""
    v = re.sub(r'\D', '', str(value))
    if re.fullmatch(r'\d{10}', v):
        return 'inn'
    if re.fullmatch(r'\d{12}', v):
        return 'inn'  # ИП
    if re.fullmatch(r'\d{13}', v):
        return 'ogrn'
    if re.fullmatch(r'\d{15}', v):
        return 'ogrnip'
    return None


def to_text(raw):
    t = H.unescape(re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', raw))
    t = re.sub(r'<[^>]+>', '\n', t)
    return [l.strip() for l in t.split('\n') if l.strip()]


def pick(lines, key, n=1):
    """Значение после ключа-строки (n строк)."""
    try:
        i = lines.index(key)
        return lines[i + n]
    except ValueError:
        return None


def value_after(lines, key, max_lines=5, stop_keys=('Выручка', 'Чистая прибыль', 'Капитал')):
    """Значение после заголовка-показателя. Склеиваем строки до max_lines или до следующего
    заголовка. На Checko 'Выручка' встречается 2+ раза (тренд-блок, график, таблица) — берём
    то вхождение, где значение похоже на сумму: цифры + размерность (млрд/млн/тыс/руб)
    (ревью 2026-09-08: поиск по заголовкам вместо позиций)."""
    for start in (i for i, l in enumerate(lines) if l == key):
        # тренд-блок ('= выросла до ...') отсекаем: сразу после заголовка должно идти число
        if not re.match(r'^[\d\u202f\s,.]', lines[start + 1] if start + 1 < len(lines) else ''):
            continue
        values = []
        for x in lines[start + 1:start + 1 + max_lines]:
            if x in stop_keys:
                break
            # стоп на служебных строках (следующий раздел и пр.)
            if re.match(r'^(Полная|Ключевые|Показывает|Финансовая|Филиалы|Список)', x):
                break
            values.append(x)
        joined = ' '.join(values)
        if re.search(r'\d', joined) and re.search(r'млрд|млн|тыс|руб', joined, re.I):
            return joined
    return None


def section(lines, start_key, stop_keys):
    """Подсписок от строки start_key до первой строки из stop_keys."""
    try:
        i = lines.index(start_key)
    except ValueError:
        return []
    out = []
    for l in lines[i + 1:]:
        if any(l == k or l.startswith(k) for k in stop_keys):
            break
        out.append(l)
    return out


def _get(s, url, **kw):
    """GET с retry на 429/5xx/сетевые сбои. Backoff 5/15 с (последний 429 не спит зря).
    Учитывает Retry-After, если сервер его отдаёт."""
    waits = (90, 120)  # Checko 429-окно ~90-300с (измерено 2026-09-09); 5/15с уже не проходит
    for attempt in range(3):
        try:
            r = s.get(url, timeout=25, **kw)
        except requests.RequestException as e:
            if attempt < 2:
                diag(f"request exception, retry: {type(e).__name__}: {str(e)[:80]}")
                time.sleep(waits[min(attempt, 1)])
                continue
            raise
        if r.status_code != 429 and r.status_code not in (500, 502, 503, 504):
            return r
        if attempt >= 2:
            return r  # финальный ответ без лишнего сна
        ra = r.headers.get('Retry-After')
        wait = int(ra) if (ra and ra.isdigit()) else waits[attempt]
        log(f"HTTP {r.status_code}, retry in {wait}s: {url}")
        time.sleep(wait)
    return r


def norm_phone(raw):
    """'+7 (999) 123-45-67' / '8 999 123-45-67' -> '+79991234567'; None если не телефон."""
    d = re.sub(r'\D', '', raw)
    if len(d) == 11 and d[0] in ('7', '8'):
        return '+7' + d[1:]
    if len(d) == 10:
        return '+7' + d
    return None


def norm_site(raw):
    """Любой вариант домена/URL -> https://host; None если не похоже на сайт."""
    s = raw.strip()
    if re.match(r'^https?://', s):
        return s
    if re.match(r'^(www\.)?[\w\-]+(\.[\w\-]+)+(/.*)?$', s):
        return 'https://' + s
    return None


# ---- Checko API v2 (ключ в ENV_CHECKO, mode 600) ----
API_BASE = 'https://api.checko.ru/v2'
API_ENV = ENV_CHECKO


def api_key():
    k = os.getenv('CHECKO_API_KEY')
    if not k and os.path.isfile(API_ENV):
        for line in open(API_ENV):
            m = re.match(r'CHECKO_API_KEY=(\S+)', line.strip())
            if m:
                return m.group(1)
    return k


def api_get(method, params):
    """GET API Checko. Возвращает (data|None, meta|None). Логирует today_request_count."""
    key = api_key()
    if not key:
        return None, None
    params = dict(params, key=key)
    r = requests.get(f'https://api.checko.ru/v2/{method}', params=params, timeout=25)
    try:
        out = r.json()
    except ValueError:
        return None, None
    meta = out.get('meta') or {}
    log(f"API {method}: HTTP {r.status_code}, today_request_count={meta.get('today_request_count')}")
    if meta.get('status') == 'ok':
        return out.get('data'), meta
    return None, meta


def _unit_row(years_data, year, row):
    y = years_data.get(str(year)) or {}
    v = y.get(str(row))
    return v if isinstance(v, (int, float)) else None


def parse_api(inn_ogrn):
    """Карточка через API Checko (company + finances + contracts + legal-cases).
    Возвращает dict того же плоского формата или None (нет ключа/ошибка API)."""
    cid = detect_id(inn_ogrn)
    if not cid:
        return None
    # карточка по ИНН или ОГРН
    params = {'inn': inn_ogrn} if cid == 'inn' else {'ogrn': inn_ogrn}
    card, meta = api_get('company', params)
    if not card:
        return None
    d = {'source': 'checko-api', 'query': inn_ogrn}
    d['name_short'] = card.get('НаимСокр')
    d['name_full'] = card.get('НаимПолн')
    d['inn'] = card.get('ИНН')
    d['ogrn'] = card.get('ОГРН')
    d['kpp'] = card.get('КПП')
    d['okpo'] = card.get('ОКПО')
    d['reg_date'] = card.get('ДатаРег')
    ya = card.get('ЮрАдрес') or {}
    d['address'] = ya.get('АдресРФ')
    okved = card.get('ОКВЭД') or {}
    d['okved_main'] = (str(okved.get('Код', '')) + ' ' + (okved.get('Наим') or '')).strip() or None
    uk = card.get('УстКап') or {}
    if uk.get('Сумма'):
        d['capital'] = f"{uk['Сумма']} руб."
    # статус/ликвидация
    st = card.get('Статус') or {}
    d['status'] = st.get('Наим')
    d['liquidated'] = bool(st and 'Ликв' in (st.get('Наим') or ''))
    # руководитель
    for ru in (card.get('Руковод') or []):
        d['ceo'] = {'role': (ru.get('НаимДолжн') or ru.get('ВидДолжн') or '').title(),
                    'name': ru.get('ФИО'), 'inn': ru.get('ИНН')}
        break
    # контакты (телефоны маскируются самим API)
    kc = card.get('Контакты') or {}
    d['phones'] = [p for p in (kc.get('Тел') or []) if p][:8]
    d['emails'] = [e.lower() for e in (kc.get('Емэйл') or []) if e][:8]
    if kc.get('ВебСайт'):
        d['websites'] = [norm_site(kc['ВебСайт'])][:5]
    # финансы (последний год с выручкой) — по ОГРН
    ogrn = card.get('ОГРН')
    fin_year = fin = None
    if ogrn:
        yd, _ = api_get('finances', {'ogrn': ogrn})
        if isinstance(yd, dict) and yd:
            for y in sorted(yd.keys(), reverse=True):
                rev = _unit_row(yd, y, '2110')
                if rev:
                    fin_year, fin = y, {'Выручка': rev, 'Чистая прибыль': _unit_row(yd, y, '2400'),
                                        'Капитал': _unit_row(yd, y, '1300')}
                    break
    if fin:
        d['finance'] = fin
        d['finance_year'] = f'{fin_year} год'
        d['_sources_checko'] = {'finance': 'checko-api'}
    # госзакупки (краткая сводка 44/223)
    gov = {}
    for law in ('44', '223'):
        g, _ = api_get('contracts', {'ogrn': ogrn, 'law': law})
        if isinstance(g, dict):
            gov[f'44' if law == '44' else '223'] = {
                'count': g.get('ЗапВсего'),
                'last': [{'номер': z.get('РегНомер'), 'цена': z.get('Цена'),
                          'заказчик': ((z.get('Заказ') or {}).get('НаимПолн')),
                          'ссылка': z.get('СтрЕИС')}
                         for z in (g.get('Записи') or [])[:3]]}
    if gov:
        d['gov_contracts_api'] = gov
    # арбитраж
    lc, _ = api_get('legal-cases', {'inn': card.get('ИНН') or inn_ogrn})
    # lc — уже словарь дел (api_get отдал out['data'])
    if isinstance(lc, dict) and lc.get('ЗапВсего'):
        d['arbitration_api'] = {'дел': lc.get('ЗапВсего'),
                                'сумма_исков': lc.get('ОбщСуммИск'),
                                'примеры': [{'номер': z.get('Номер'), 'суд': z.get('Суд'),
                                             'ссылка': z.get('СтрКАД'), 'дата': z.get('Дата')}
                                            for z in (lc.get('Записи') or [])[:3]]}
    return d


def parse(inn_ogrn):
    """Точка входа: сначала API Checko, при недоступности/лимите — HTML-парсер."""
    try:
        d = parse_api(inn_ogrn)
        if d:
            return d
        log(f"API недоступен/ошибка — HTML-фолбэк для {inn_ogrn}")
    except Exception as e:
        log(f"API EXCEPTION {inn_ogrn}: {type(e).__name__}: {e} — HTML-фолбэк")
    return parse_html(inn_ogrn)


def parse_html(inn_ogrn):
    s = requests.Session()
    s.headers.update(UA)
    r = _get(s, 'https://checko.ru/search', params={'query': inn_ogrn}, allow_redirects=True)
    if r.status_code != 200 or '/company/' not in str(r.url):
        log(f"search failed: query={inn_ogrn} status={r.status_code} url={r.url}")
        return {'error': f'search failed: HTTP {r.status_code}'}
    time.sleep(random.uniform(2.0, 3.0))
    r = _get(s, str(r.url))
    if r.status_code != 200:
        log(f"card failed: {r.url} status={r.status_code}")
        return {'error': f'card failed: HTTP {r.status_code}'}
    lines = to_text(r.text)
    d = {'source': str(r.url), 'query': inn_ogrn}
    sources = {}

    d['name_short'] = pick(lines, 'Список разделов')
    # шапка: "Название - Город - ИНН X - Гендиректор Фамилия И. О."
    m = re.match(r'^(.+?)\s+-\s+(.+?)\s+-\s+ИНН\s+(\d+)\s+-\s+(.+)$', lines[0] if lines else '')
    if m:
        d['name_short'], d['city'], d['inn'], d['head_brief'] = m.group(1), m.group(2), m.group(3), m.group(4)
        sources['inn'] = 'checko'
    try:
        i = lines.index('Список разделов')
        d['name_full'] = lines[i + 2] if i + 2 < len(lines) else d.get('name_short')
    except ValueError:
        d['name_full'] = d.get('name_short')
    d['ogrn'] = pick(lines, 'ОГРН')
    d['kpp'] = pick(lines, 'КПП')
    d['okpo'] = pick(lines, 'ОКПО')
    d['reg_date'] = pick(lines, 'Дата регистрации')
    # адрес: приоритет у 'Юридический адрес'; 'Предыдущий...' — отдельное поле previous_address
    addr, prev_addr = None, None
    for i, l in enumerate(lines):
        for j in range(i + 1, min(i + 5, len(lines))):
            if re.match(r'\d{6},', lines[j]):
                if l == 'Юридический адрес' and not addr:
                    addr = lines[j]
                elif l == 'Предыдущий юридический адрес' and not prev_addr:
                    prev_addr = lines[j]
                break
        if addr and prev_addr:
            break
    d['address'] = addr
    if prev_addr:
        d['previous_address'] = prev_addr
    d['okved_main'] = pick(lines, 'Вид деятельности')
    for i, l in enumerate(lines):
        if l == 'Уставный капитал' and i + 1 < len(lines):
            d['capital'] = lines[i + 1]
            break
    # руководитель: секция 'Руководитель' -> 'Генеральный директор' -> ФИО (role отдельно)
    for i, l in enumerate(lines):
        if l == 'Генеральный директор' and i + 1 < len(lines) and NAME_RE.match(lines[i + 1]):
            role = None
            # ищем ближайший выше заголовок должности
            for j in range(i - 1, max(i - 6, -1), -1):
                if re.search(r'[Дд]иректор|[Рр]уководитель|[Пп]редседатель|Управляющ', lines[j]):
                    role = lines[j]
                    break
            else:
                role = 'Генеральный директор'
            gd = {'role': role, 'name': lines[i + 1]}
            for j in range(i + 2, min(i + 6, len(lines))):
                if lines[j] == 'ИНН' and j + 1 < len(lines):
                    gd['inn'] = lines[j + 1]
                if lines[j].startswith('с '):
                    gd['since'] = lines[j][2:]
            d['ceo'] = gd
            sources['ceo'] = 'checko'
            break
    # контакты (нормализация)
    phones, emails, sites = [], [], []
    for l in lines:
        p = norm_phone(l)
        if p and p not in phones:
            phones.append(p)
        if '@' in l and 3 < len(l) < 80 and ' ' not in l and not l.startswith('@'):
            e = l.lower()
            if e not in emails:
                emails.append(e)
        if not p and '@' not in l:
            site = norm_site(l)
            if site and 'checko.ru' not in site and site not in sites:
                sites.append(site)
    d['phones'] = phones[:8]
    d['emails'] = emails[:8]
    d['websites'] = sites[:5]
    # госзакупки
    for i, l in enumerate(lines):
        if l in ('Заказчик', 'Поставщик') and i + 4 < len(lines):
            key = 'gov_as_customer' if l == 'Заказчик' else 'gov_as_supplier'
            contracts = re.search(r'(\d+)\s+контракт', lines[i + 4])
            d[key] = {'sum': ' '.join(lines[i + 1:i + 4]),
                      'contracts': contracts.group(1) if contracts else None}
        if 'контракта на общую сумму' in l:
            d['gov_summary'] = l
    # финансы: по заголовкам, не по позициям (ревью 2026-09-08)
    rev = value_after(lines, 'Выручка')
    if rev:
        d['finance'] = {'Выручка': rev,
                        'Чистая прибыль': value_after(lines, 'Чистая прибыль'),
                        'Капитал': value_after(lines, 'Капитал')}
        d['finance_year'] = next((x for x in lines if re.match(r'^\d{4} год$', x)), None)
        sources['finance'] = 'checko'
    # арбитраж
    for i, l in enumerate(lines):
        if re.match(r'^\d+\s+дел[аои]?$', l) and 'в качестве истца' in ' '.join(lines[i+1:i+4]):
            block = ' '.join(lines[i:i+9])
            d['arbitration'] = block.split('Последнее дело')[0].strip() + (
                ' | ' + [x for x in lines[i:i+9] if x.startswith('Последнее дело')][0]
                if any(x.startswith('Последнее дело') for x in lines[i:i+9]) else '')
            break
    fil = section(lines, 'Филиалы и представительства', ['Экс-руководители', 'Учредители', 'Связанные', 'Список разделов'])
    if fil:
        d['branches'] = [l for l in fil if re.match(r'\d{6},', l)][:3]
    if sources:
        d['_sources_checko'] = sources
    return d


# ---- Фолбэк: find-org.net (директор/учредители) и buxbalans.ru (реквизиты) ----
# 2026-09-08: Checko иногда отдаёт урезанную страницу (нет блока Руководитель/Учредители)
# или падает (403/500/timeout). find-org ищет ТОЛЬКО по ИНН; если на входе ОГРН —
# ИНН берём из результата Checko (data['inn']).

def fallback_find_org(inn):
    """find-org.net: директор + учредители + реквизиты. Только по ИНН (10/12 цифр)."""
    try:
        s = requests.Session(); s.headers.update(UA)
        r = s.get('http://www.find-org.net/search/all/', params={'val': inn, 'type': 'inn'},
                  timeout=20, allow_redirects=True)
        if r.status_code != 200:
            log(f"find-org search HTTP {r.status_code}")
            return None
        m = re.search(r"href='/cli/([^']+)'", r.text)
        if not m:
            log(f"find-org: no card for {inn}")
            return None
        time.sleep(1)
        r2 = s.get(f"http://www.find-org.net/cli/{m.group(1)}", timeout=20)
        if r2.status_code != 200:
            log(f"find-org card HTTP {r2.status_code}")
            return None
        lines = to_text(r2.text)
        out = {'source': f"http://www.find-org.net/cli/{m.group(1)}"}
        for i, l in enumerate(lines):
            if l == 'Руководитель' and i + 2 < len(lines) and lines[i + 1] == ':':
                mm = re.match(r'(ГЕНЕРАЛЬНЫЙ ДИРЕКТОР|ДИРЕКТОР|ПРЕДСЕДАТЕЛЬ[^,]*?)\s+(.+)', lines[i + 2])
                if mm:
                    out['ceo'] = {'role': mm.group(1).title(), 'name': mm.group(2).title()}
            if l == 'Количество учредителей:' and i + 1 < len(lines):
                out['founders_count'] = lines[i + 1]
            if l == 'Юридический адрес:' and i + 1 < len(lines):
                out['address'] = lines[i + 1]
            if l == 'Уставной капитал:' and i + 1 < len(lines):
                out['capital'] = lines[i + 1]
            if l == 'Численность персонала:' and i + 1 < len(lines):
                out['staff'] = lines[i + 1]
            if l == 'Юридическое наименование' and i + 2 < len(lines) and lines[i + 1] == ':':
                out['name_full'] = lines[i + 2]
        return out if len(out) > 1 else None
    except Exception as e:
        log(f"find-org EXCEPTION {inn}: {type(e).__name__}: {e}")
        return None


def fallback_buxbalans(inn):
    """buxbalans.ru: реквизиты + вид деятельности. Только по ИНН. 404 = нет данных."""
    try:
        s = requests.Session(); s.headers.update(UA)
        r = s.get(f'https://buxbalans.ru/{inn}.html', timeout=20, allow_redirects=True)
        if r.status_code != 200:
            return None
        lines = to_text(r.text)
        out = {'source': f'https://buxbalans.ru/{inn}.html'}
        for i, l in enumerate(lines):
            if l == 'Полное название -' and i + 1 < len(lines):
                out['name_full'] = lines[i + 1]
            if l == 'ИНН:' and i + 1 < len(lines) and re.match(r'^\d{10,12}$', lines[i + 1]):
                out['inn'] = lines[i + 1]
            if l == 'ОГРН:' and i + 1 < len(lines):
                out['ogrn'] = lines[i + 1]
            if l == 'КПП:' and i + 1 < len(lines):
                out['kpp'] = lines[i + 1]
            if l == 'Дата регистрации:' and i + 1 < len(lines):
                out['reg_date'] = lines[i + 1]
            if l == 'Вид деятельности:' and i + 1 < len(lines):
                out['okved_main'] = lines[i + 1]
            if l == 'Юридический адрес:' and i + 1 < len(lines):
                out['address'] = lines[i + 1]
            if l == 'Статус компании -' and i + 1 < len(lines):
                out['status'] = lines[i + 1]
        return out if len(out) > 1 else None
    except Exception as e:
        log(f"buxbalans EXCEPTION {inn}: {type(e).__name__}: {e}")
        return None


def enrich_fallbacks(data, inn_ogrn):
    """Дозаполнение пустых полей фолбэками. Работает по настоящему ИНН:
    если на входе ОГРН, берём data['inn'] из результата Checko (ревью 2026-09-08).
    Возвращает (data, список полей, полученных из фолбэков)."""
    # ИНН для фолбэков: из Checko-результата или входное значение, если оно похоже на ИНН
    inn = data.get('inn') or (inn_ogrn if detect_id(inn_ogrn) in ('inn', None) else None)
    if not inn:
        log(f"fallback skipped: нет ИНН (вход {inn_ogrn} = {detect_id(inn_ogrn)}, Checko не дал inn)")
        data['_meta'] = {
            'primary_source_ok': not data.get('error'),
            'fallback_used': False,
            'fallbacks': [],
            'fields_from_fallback': [],
            'partial': bool(data.get('error')),
            'skip_reason': 'no_inn',
        }
        return data, []
    fallback_fields = []
    from_fo, from_bb = None, None

    # find-org: CEO / адрес / капитал / персонал / учредители / name_full
    need_fo = (not (data.get('ceo') or {}).get('name') or not data.get('address')
               or not data.get('capital') or not data.get('staff') or not data.get('founders_count'))
    if need_fo:
        from_fo = fallback_find_org(inn)
        if from_fo:
            data['fallback_find_org'] = from_fo
            if from_fo.get('ceo') and not (data.get('ceo') or {}).get('name'):
                data['ceo'] = dict(from_fo['ceo'])
                if from_fo.get('name_full'):
                    data['ceo']['source_name'] = from_fo['name_full']
                fallback_fields.append('ceo')
            for k in ('founders_count', 'name_full', 'staff', 'capital', 'address'):
                if from_fo.get(k) and not data.get(k):
                    data[k] = from_fo[k]
                    fallback_fields.append(k)

    # buxbalans: реквизиты + ОКВЭД
    need_bb = (not data.get('finance') or not data.get('okved_main')
               or not data.get('kpp') or not data.get('ogrn'))
    if need_bb:
        from_bb = fallback_buxbalans(inn)
        if from_bb:
            data['fallback_buxbalans'] = from_bb
            for k in ('okved_main', 'kpp', 'ogrn', 'reg_date', 'name_full', 'address', 'status'):
                if from_bb.get(k) and not data.get(k):
                    data[k] = from_bb[k]
                    fallback_fields.append(k)

    # _meta: источники и качество (плоский формат сохранён, решение 2026-09-08)
    used = []
    if from_fo:
        used.append('find-org')
    if from_bb:
        used.append('buxbalans')
    data['_meta'] = {
        'primary_source_ok': not data.get('error'),
        'fallback_used': bool(used),
        'fallbacks': used,
        'fields_from_fallback': fallback_fields,
        'partial': bool(data.get('error') or fallback_fields),
    }
    return data, fallback_fields


if __name__ == '__main__':
    q = next((a for a in sys.argv[1:] if a.isdigit()), None)
    if not q:
        print(json.dumps({'error': 'usage: requisites_parser.py <ИНН|ОГРН>'}, ensure_ascii=False))
        sys.exit(1)
    try:
        data = parse(q)
        # фолбэки — при ЛЮБОМ исходе Checko (и успех с пропусками, и любая ошибка):
        # enrich_fallbacks сам решает, какие поля добирать (ревью 2026-09-08).
        data, fb_fields = enrich_fallbacks(data, q)
        if fb_fields:
            data['_meta']['partial'] = True
        diag(f"query={q} error={data.get('error')} fallback_fields={data.get('_meta', {}).get('fields_from_fallback')}")
        # служебные блоки (_meta/_sources/фолбэк-сырцы) — в одну строку: меньше токенов в контексте
        for k in ('_meta', '_sources_checko', 'fallback_find_org', 'fallback_buxbalans'):
            if isinstance(data.get(k), (dict, list)):
                data[k] = json.dumps(data[k], ensure_ascii=False, separators=(',', ':'))
        print(json.dumps(data, ensure_ascii=False, indent=1))
    except Exception as e:
        log(f"EXCEPTION query={q}: {type(e).__name__}: {e}")
        print(json.dumps({'error': f'{type(e).__name__}: {e}'}, ensure_ascii=False))
        sys.exit(1)


# by sichkarenkomax
