#!/usr/bin/env python3
"""Парсер ЕИС (zakupki.gov.ru) по организации 223-ФЗ.

Схема:
1. Вход на главную → session-cookie (без него 404).
2. /epz/organization/search/results.html?searchString=<ИНН> → agencyId карточки.
3. Карточка (view223/info.html?agencyId=N): реквизиты + готовые ссылки на
   договоры/план закупок с правильным customerIdOrg
   (формат "-1:<Название>zZzZzZzZ<ИНН>zZzZ<КПП>zZzZ<ОГРН>" — берётся из href, не пересобирать).
4. SSL ЕИС — самоподписанный CA → verify=False (только для этого домена).

Вывод: JSON на stdout.
"""
import sys, re, json, html as H
import requests, urllib3

urllib3.disable_warnings()
BASE = 'https://zakupki.gov.ru'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}


def new_session():
    s = requests.Session()
    s.headers.update(UA)
    s.get(BASE + '/', timeout=25, verify=False)
    return s


def strip_html(t):
    t = re.sub(r'<script[\s\S]*?</script>|<style[\s\S]*?</style>', ' ', t)
    return H.unescape(re.sub(r'<[^>]+>', '\n', t))


def entries_of(html):
    return re.findall(r'class="registry-entry__body"([\s\S]*?)(?=class="registry-entry__header"|$)', html)


def parse_entry(e):
    vals = [l.strip() for l in strip_html(e).split('\n') if l.strip()]

    LABELS = ['Объект закупки', 'Номер договора', 'Заказчик', 'Цена договора',
              'Начальная цена', 'Заключение договора', 'Размещено', 'Обновлено',
              'Окончание подачи заявок', 'Срок исполнения', 'Способ закупки',
              'Предмет договора', 'Период действия плана', 'Вид плана закупки']
    def grab(label):
        if label not in vals:
            return ''
        i = vals.index(label)
        out = []
        for v in vals[i+1:]:
            if v in LABELS:
                break
            out.append(v)
        return ' '.join(out).strip()
    d = {}
    if 'Объект закупки' in vals and 'Заказчик' in vals:
        i, j = vals.index('Объект закупки'), vals.index('Заказчик')
        d['object'] = ' '.join(vals[i+1:j])
    if 'Предмет договора' in vals:
        d['object'] = grab('Предмет договора')
    d['number'] = grab('Номер договора')
    d['customer'] = grab('Заказчик')
    d['price'] = (grab('Цена договора') or grab('Начальная цена')).replace(' Срок исполнения', '')
    d['date'] = grab('Заключение договора') or grab('Размещено')
    d['method'] = grab('Способ закупки')
    d['period'] = grab('Период действия плана')
    if 'Вид плана закупки' in vals:
        d['plan_kind'] = grab('Вид плана закупки')
        m = re.search(r'(\d+) позиции', ' '.join(vals))
        if m: d['positions'] = m.group(1)
    for k in ('price', 'customer', 'object', 'plan_kind'):
        if k in d:
            d[k] = d[k].replace('\xa0', ' ')
    if 'plan_kind' in d:
        d['plan_kind'] = re.sub(r'\s*Перейти к позициям.*', '', d['plan_kind']).strip()
    return {k: v for k, v in d.items() if v}


def find_org(s, inn):
    r = s.get(BASE + '/epz/organization/search/results.html?searchString=' + inn,
              timeout=25, verify=False)
    m = re.search(r'href="(/epz/organization/view223/info\.html\?agencyId=(\d+))"', r.text)
    if not m:
        return None
    lines = [l.strip() for l in strip_html(r.text).split('\n') if l.strip()]
    name = None
    for i, l in enumerate(lines):
        if l == 'Местонахождение' and i > 0:
            name = lines[i-1]
            break
    if name is None:
        for l in lines:
            if re.search(r'(АКЦИОНЕРНОЕ|ОБЩЕСТВО|ПУБЛИЧНОЕ|ФЕДЕРАЛЬНОЕ|ФГУП)', l) and len(l) < 200 and 'Местонахождение' not in l:
                name = l
                break
    return {'agency_id': m.group(2), 'info_path': m.group(1), 'name': name}


def parse_card(s, info_path):
    r = s.get(BASE + info_path, timeout=25, verify=False)
    lines = [l.strip() for l in strip_html(r.text).split('\n') if l.strip()]
    rec = {}
    for i, l in enumerate(lines):
        if l in ('ОГРН', 'ИНН', 'КПП') and i + 1 < len(lines):
            rec[l] = lines[i + 1]
    contracts_url = plan_url = None
    m = re.search(r'href="(/epz/contractfz223/search/results\.html\?[^"]*customerIdOrg=[^"]*)"', r.text)
    if m:
        contracts_url = H.unescape(m.group(1))
        # orderplan использует тот же customerIdOrg (ссылки orderplan в карточке не содержат его)
        plan_url = H.unescape(m.group(1)).replace('/epz/contractfz223/search/', '/epz/orderplan/search/')
    return rec, contracts_url, plan_url


def _norm_name(s):
    """Название для сверки: полное ОПФ → краткое, кавычки/пунктуация/регистр/пробелы прочь,
    слова сортируются (порядок ОПФ не влияет)."""
    s = re.sub(r'[^а-яa-z0-9 ]', ' ', str(s).lower())
    for full, short in (('общество с ограниченной ответственностью', 'ооо'),
                        ('публичное акционерное общество', 'пао'),
                        ('непубличное акционерное общество', 'ао'),
                        ('открытое акционерное общество', 'оао'),
                        ('закрытое акционерное общество', 'зао'),
                        ('акционерное общество', 'ао'),
                        ('федеральное государственное унитарное предприятие', 'фгуп')):
        s = s.replace(full, short)
    return ' '.join(sorted(s.split()))


def collect_entries(s, url, org_name=None, max_pages=5):
    """Записи реестра; если org_name задан — чужие закупки (customer != org) отбрасываются:
    extendedsearch/реестр по customerIdOrg может отдавать записи других заказчиков.
    Пагинация pageNumber=2..max_pages, остановка на первой пустой странице.
    Возвращает (entries, dropped)."""
    out, dropped = [], 0
    target = _norm_name(org_name) if org_name else None
    for page in range(1, max_pages + 1):
        page_url = url if page == 1 else (f"{url}{'&' if '?' in url else '?'}pageNumber={page}")
        r = s.get(BASE + page_url, timeout=25, verify=False)
        entries = [d for d in (parse_entry(e) for e in entries_of(r.text)) if d]
        if not entries:
            break
        for d in entries:
            if target and d.get('customer') and _norm_name(d['customer']) != target:
                dropped += 1
                continue
            out.append(d)
    return out, dropped


def main():
    if len(sys.argv) < 2:
        print('Usage: zakupki_223_parser.py <ИНН> [max_pages]')
        sys.exit(1)
    inn = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    s = new_session()
    org = find_org(s, inn)
    if not org:
        print(json.dumps({'error': f'organization {inn} not found in EIS'}, ensure_ascii=False))
        return
    rec, contracts_url, plan_url = parse_card(s, org['info_path'])
    contracts, dropped_c = collect_entries(s, contracts_url, org['name'], max_pages) if contracts_url else ([], 0)
    plan, dropped_p = collect_entries(s, plan_url, org['name'], max_pages) if plan_url else ([], 0)
    result = {
        'inn': inn,
        'org': {**org, 'requisites': rec},
        'notices': [],
        'contracts': contracts,
        'plan': plan,
        'customer_filtered': dropped_c + dropped_p,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
