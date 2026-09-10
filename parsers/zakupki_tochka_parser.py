#!/usr/bin/env python3
"""Парсер закупок с zakupki.tochka.com по заказчику (страница /customers/<ИНН>/).

Схема (проверено на живых заказчиках 2026-09-02):
- Сайт — Next.js SSR; список закупок находится в SSR-потоке self.__next_f.push([1,"..."])
- В потоке лежат JSON-объекты {"purchase":{...}} с полями: seldonId, subject,
  purchasePrice, status.description, endDate, notificationNumber, purchaseLink
  (прямая ссылка на процедуру на ЭТП заказчика), epName.
- Балансное извлечение JSON из склеенного потока.
- Пагинация: ?page=N (пустые страницы возвращают 0 объектов — цикл до первой пустой).

Usage: zakupki_tochka_parser.py <ИНН> [max_pages]
Вывод: JSON на stdout.
"""
import sys, re, json
import requests, urllib3

urllib3.disable_warnings()
BASE = 'https://zakupki.tochka.com'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}


def fetch_page_purchases(s, inn, page):
    url = f'{BASE}/customers/{inn}/' + (f'?page={page}' if page > 1 else '')
    r = s.get(url, timeout=30, verify=False)
    if r.status_code != 200:
        return None, r.status_code
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', r.text, re.S)
    joined = ''.join(chunks).replace('\\"', '"').replace('\\\\', '\\')
    objs, pos = [], 0
    while True:
        i = joined.find('{"purchase":', pos)
        if i < 0:
            break
        depth = 0
        j = i
        in_str = False
        esc = False
        while j < len(joined):
            c = joined[j]
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        objs.append(joined[i:j + 1])
                        break
            j += 1
        pos = i + 1
    return [json.loads(o)['purchase'] for o in objs], 200


def norm(pu):
    return {
        'seldon_id': pu.get('seldonId'),
        'subject': pu.get('subject'),
        'price': pu.get('purchasePrice'),
        'status': (pu.get('status') or {}).get('description'),
        'end_date': (pu.get('endDate') or '')[:10],
        'notification_number': pu.get('notificationNumber'),
        'purchase_link': pu.get('purchaseLink'),
        'ep_name': pu.get('epName'),
    }


def main():
    if len(sys.argv) < 2:
        print('Usage: zakupki_tochka_parser.py <ИНН> [max_pages]')
        sys.exit(1)
    inn = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    s = requests.Session()
    s.headers.update(UA)
    all_pu, seen = [], set()
    pages_fetched = 0
    for page in range(1, max_pages + 1):
        pu_list, http_code = fetch_page_purchases(s, inn, page)
        if pu_list is None:
            break
        pages_fetched += 1
        if not pu_list:
            break
        for pu in pu_list:
            sid = pu.get('seldonId')
            if sid is None:  # без ID дедупить нельзя — не теряем записи
                all_pu.append(norm(pu))
            elif sid not in seen:
                seen.add(sid)
                all_pu.append(norm(pu))
    out = {
        'inn': inn,
        'source': 'zakupki.tochka.com',
        'customer_url': f'{BASE}/customers/{inn}/',
        'pages_fetched': pages_fetched,
        'total': len(all_pu),
        'purchases': all_pu,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
