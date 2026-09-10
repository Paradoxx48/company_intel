#!/usr/bin/env python3
"""Парсер вакансий работодателя с hh.ru (без API-токена).

Схема (правки 2026-09-08 по ревью структуры HTML):
- api.hh.ru требует зарегистрированный UA и авторизацию — 403/bad_user_agent для всех UA.
- Работает обычный поиск: hh.ru/search/vacancy?employer_id=<ID>&area=113&page=N
- Каркас выдачи парсится BeautifulSoup'ом по data-qa атрибутам (надёжнее CSS-классов,
  которые hh часто меняет): serp-item → title/salary/address.
- Пагинация: по 20 вакансий на страницу, гостю глубина 2 страницы;
  между запросами пауза random.uniform(5, 7.5) сек (иначе пустые ответы).
- Ретрай на 429/5xx: до 2 повторов с паузами 10/20 сек (не агрессивно — hh усиливает
  лимиты при частых повторах).
- Гиганты (счётчик ≥6000): максимум 2 страницы; если в выборке <2 производственных
  вакансий — warning о нерепрезентативности (решение 2026-09-08).
- stdout — ТОЛЬКО JSON; вся диагностика в stderr (для json_decode на стороне потребителя).

Usage: hh_vacancies.py <employer_id> [max_pages]
Вывод: JSON на stdout.
"""
import sys, re, json, time, random, html as H
import requests, urllib3

urllib3.disable_warnings()
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
DEFAULT_MAX_PAGES = 2      # задача — производственный профиль, не выгрузка всего
GIANT_THRESHOLD = 6000     # вакансий у работодателя → максимум 2 страницы
PROD_KEYWORDS = re.compile(
    r'токар|слесар|чпу|свар|наладчик|инженер-технолог|технолог|фрезеровщ|'
    r'сборщик|механик|оператор станк|станочник|гальван|термист|кузнец|'
    r'литейн|заготовитель|маляр-пул|окрасчик|дефектоскопист|контролёр otp|'
    r'контролер otp|инженер конструктор|конструктор-механик', re.I)


def diag(msg):
    """Диагностика — только в stderr (stdout держим чистым под JSON)."""
    print(f"[hh_vacancies] {msg}", file=sys.stderr)


def new_session():
    """Стартовая сессия с куки hh. Ответ не критичен — логируем и продолжаем."""
    s = requests.Session()
    s.headers.update(UA)
    try:
        r = s.get('https://hh.ru/', timeout=20)
        diag(f"home page: HTTP {r.status_code}")
    except requests.RequestException as e:
        diag(f"home page failed (продолжаем без куки): {type(e).__name__}: {str(e)[:80]}")
    return s


def parse_page(s, employer_id, page, warnings):
    """Одна страница выдачи с ретраем 429/5xx. Возвращает (items, total)."""
    for attempt in range(3):  # 1 попытка + 2 ретрая
        try:
            r = s.get('https://hh.ru/search/vacancy',
                      params={'employer_id': employer_id, 'area': 113, 'page': page},
                      timeout=25)
        except requests.RequestException as e:
            warnings.append(f'page {page}: request exception {type(e).__name__}')
            return [], None
        if r.status_code == 200:
            return _parse_html(r.text), _extract_total(r.text)
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            wait = (10, 20)[attempt]
            warnings.append(f'page {page}: HTTP {r.status_code}, retry in {wait}s')
            time.sleep(wait)
            continue
        warnings.append(f'page {page}: HTTP {r.status_code}')
        return [], None
    return [], None


def _extract_total(text):
    m = re.search(r'(\d+)\s*ваканси', H.unescape(re.sub(r'<script[\s\S]*?</script>', ' ', text)))
    return int(m.group(1)) if m else None


def _extract_salary(win_html):
    """Зарплата из текста карточки: '85 000 – 101 000 ₽' (до строки 'Опыт'/'Выплаты')."""
    txt = re.sub(r'<[^>]+>', '\n', win_html)
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    parts = []
    for l in lines:
        if 'Опыт' in l or 'Выплаты' in l or len(l) > 40:
            break
        if '₽' in l or 'руб' in l.lower() or re.match(r'^[\d\u202f\s]{3,}$', l) or l in ('–', '—', '-'):
            parts.append(l)
        elif parts:
            break
    return ' '.join(parts) if parts else None


def _parse_html(html):
    """Каркас serp-item через BS4 (title/href), salary/city — из текста окна карточки:
    у hh зарплата рендерится просто текстом после title ('85 000 – 101 000 ₽'),
    data-qa="vacancy-serp__compensation" в SSR пуст."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    seen = set()
    titles = soup.select('[data-qa="serp-item__title-text"]')
    for idx, tag in enumerate(titles):
        a = tag.find_parent('a')
        href = a.get('href') if a else None
        m = re.search(r'/vacancy/(\d+)', href or '')
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        # окно карточки: до следующего title (позиции в исходном HTML через .sourceline нет —
        # используем find_all и nextSibling-обход через строковые позиции BS4 не даёт, поэтому
        # окно считаем по соседям в списке родителя serp-item)
        # контейнер карточки: class 'vacancy-card--<hash>' (стабильный префикс, хэш меняется)
        card = tag.find_parent(class_=re.compile('vacancy-card'))
        if card is None:
            card = tag.find_parent(class_=re.compile('serp-item'))
        win_html = str(card) if card else ''
        city_el = card.find(attrs={'data-qa': 'vacancy-serp__vacancy-address'}) if card else None
        city = H.unescape(city_el.get_text(strip=True)) if city_el else None
        sal = _extract_salary(win_html)
        items.append({
            'id': vid,
            'url': f'https://hh.ru/vacancy/{vid}',
            'name': tag.get_text(strip=True),
            'salary': sal,
            'city': city,
        })
    return items


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'usage: hh_vacancies.py <employer_id> [max_pages]'},
                         ensure_ascii=False))
        sys.exit(1)
    employer_id = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_PAGES
    giant_note = None
    warnings = []
    s = new_session()
    all_items, seen = [], set()
    total = None
    stop_reason = 'max_pages'
    pages_fetched = 0
    for page in range(max_pages):
        items, t = parse_page(s, employer_id, page, warnings)
        if not items and t is None and warnings and warnings[-1].startswith(f'page {page}: HTTP'):
            stop_reason = warnings[-1].split('HTTP')[-1].strip()
            break
        pages_fetched += 1
        total = total or t
        new = [x for x in items if x['id'] not in seen]
        for x in new:
            seen.add(x['id'])
            all_items.append(x)
        if not new:
            stop_reason = 'no_new_vacancies'
            break
        if page == 0 and total and total >= GIANT_THRESHOLD and max_pages > 2:
            max_pages = 2
            giant_note = (f'Гигант ({total} вакансий ≥ {GIANT_THRESHOLD}): лимит понижен '
                          f'до 2 страниц.')
        if page + 1 >= max_pages:
            break
        time.sleep(random.uniform(5.0, 7.5))

    # Проверка репрезентативности производственного профиля
    prod_count = sum(1 for x in all_items if x.get('name') and PROD_KEYWORDS.search(x['name']))
    parser_status = 'ok'
    if warnings:
        parser_status = 'partial'
    if total and total >= GIANT_THRESHOLD and prod_count < 2 and pages_fetched >= 2:
        warnings.append(f'выборка нерепрезентативна для производственного профиля: '
                        f'{prod_count} производственных вакансий из {len(all_items)} '
                        f'(компания-гигант, {total} вакансий всего)')
        parser_status = 'partial'

    out = {'employer_id': employer_id, 'total_claimed': total,
           'total_fetched': len(all_items), 'pages_fetched': pages_fetched,
           'parser_status': parser_status, 'stop_reason': stop_reason,
           'prod_vacancies_in_sample': prod_count,
           'warnings': warnings, 'vacancies': all_items}
    if giant_note:
        out['giant_limit_applied'] = giant_note
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()


# by sichkarenkomax
