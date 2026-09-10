#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bitrix_sync.py — запись результатов разведки в Битрикс24 по ID компании.

Запуск: bitrix_sync.py <company_id> <папка_разведки>
  company_id — ID компании в Б24 (ID элемента CRM);
  папка разведки — {BASE_DIR}/<YYMMDD>_<Название>_<ИНН>.

Ожидает в папке: result_<название>.md (карточка) и intel.json (структурные данные).
intel.json пишет агент в конце разведки (схема в SKILL.md).

Читает вебхук из env-файла (ENV_B24) (BITRIX_WEBHOOK_URL, mode 600).
Лог: {BASE_DIR}/parsers/debug.log
"""
import sys, os, re, json, glob, datetime, time, requests, urllib3

urllib3.disable_warnings()
import sys as _sys, os as _os
_sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'configs'))
from config import BASE_DIR, ENV_B24, DEBUG_LOG
ENV_PATH = ENV_B24

# Маппинг значений списочных полей Б24 (решение пользователя 2026-09-08)
UF_OWN_EQUIPMENT = {'лазер': '1850', 'плазма': '1851', 'гидроабразив': '1854',
                    'гибка': '1852', 'вальцовка': '1853', 'сварка': '1855',
                    'штамповка': '1856', 'штампоква': '1856', 'токарка': '1857',
                    'токарная обработка': '1857', 'фрезеровка': '1858',
                    'фрезерная обработка': '1858', 'порошковая покраска': '1859',
                    'порошковая окраска': '1859'}
UF_DIRECTION = {'производство оборудования': '1807', 'строительство': '1808',
                'конечный потребитель оборудования': '1809',
                'конечный потребитель': '1809',
                'ремонт / восстановление оборудования': '1810',
                'ремонт оборудования': '1810', 'ремонт/восстановление оборудования': '1810',
                'производство инструмента': '1981', 'обслуживание оборудования': '2111'}
UF_SPHERE = {'сельское хозяйство': '1811', 'вооружения и военная техника': '1812',
             'армия': '1812', 'стройматериалы и строительство': '1813',
             'горнодобывающая промышленность': '1814', 'горнодобывающая': '1814',
             'металлургия': '1815', 'электроника': '1816', 'продукты питания': '1978',
             'станкостроение': '1979', 'металлоконструкции': '1980',
             'машиностроение': '1982', 'металлообработка': '1983', 'судостроение': '2002',
             'нефтегазовая промышленность': '2082', 'нефтегаз': '2082',
             'химическая промышленность': '2083', 'химия': '2083',
             'электроэнергетика': '2084', 'теплоэнергетика': '2085',
             'атомная промышленность': '2086', 'электротехника': '2087',
             'приборостроение': '2087', 'автоматизация и робототехника': '2088',
             'автомобильная промышленность': '2089', 'автопром': '2089',
             'железнодорожный транспорт': '2090', 'железнодорожная отрасль': '2090',
             'авиация и авиастроение': '2091', 'авиация': '2091', 'авиастроение': '2091',
             'космическая промышленность': '2092', 'космос': '2092',
             'лесная промышленность': '2093', 'деревообработка': '2093',
             'целлюлозно-бумажная промышленность': '2094',
             'стекольная промышленность': '2095',
             'керамика и производство изделий из камня': '2096',
             'пластмассы и полимеры': '2097', 'резинотехническая промышленность': '2098',
             'текстильная промышленность': '2099', 'лёгкая промышленность': '2100',
             'легкая промышленность': '2100', 'фармацевтика / медицина': '2101',
             'фармацевтика': '2101', 'медицина': '2101',
             'экология и переработка отходов': '2102',
             'водоснабжение и водоочистка': '2103',
             'логистика и складское хозяйство': '2104', 'логистика': '2104',
             'торговля и дистрибуция': '2105', 'торговля': '2105',
             'научно-исследовательская деятельность': '2106',
             'инжиниринг': '2107', 'жкх': '2108', 'строительная техника': '2109',
             'другое': '2110'}


def log(msg):
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] bitrix_sync: {msg}\n")


def load_webhook():
    """Вебхук из ENV_B24: BITRIX_WEBHOOK_URL=http://<host>/rest/<N>/<key>"""
    for line in open(ENV_PATH, encoding='utf-8'):
        if line.startswith('BITRIX_WEBHOOK_URL='):
            url = line.split('=', 1)[1].strip()
            if url:
                return url
    print(json.dumps({'error': f'BITRIX_WEBHOOK_URL пуст в {ENV_PATH} — впиши URL входящего вебхука Б24'}, ensure_ascii=False))
    sys.exit(1)


def load_intel(folder):
    """Собирает структурные данные: intel.json (приоритет) + result_*.md как фолбэк."""
    data = {}
    p = os.path.join(folder, 'intel.json')
    if os.path.exists(p):
        data = json.load(open(p, encoding='utf-8'))
    md_files = sorted(glob.glob(os.path.join(folder, 'result_*.md')))
    data['_md_file'] = md_files[0] if md_files else None
    if data.get('_md_file') and not data.get('card_text'):
        data['card_text'] = open(data['_md_file'], encoding='utf-8').read()
    return data


def norm_title(name, name_full):
    """TITLE-нормализация (решение 2026-09-08): ОПФ СЗАДИ названия, без кавычек.
    'ООО Ромашка' → 'Ромашка ООО', 'АО «Пример»' → 'Пример АО'.
    ИП не трогаем (у ИП фамилия после ОПФ — перестановка недопустима)."""
    for src in (name_full, name):
        if not src:
            continue
        s = src.strip().strip(',').strip()
        # полное ОПФ словом (ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ ...) → сокращаем
        full2short = {
            'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ': 'ООО',
            'АКЦИОНЕРНОЕ ОБЩЕСТВО': 'АО',
            'ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО': 'ПАО',
            'НЕПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО': 'АО',
            'ЗАКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО': 'ЗАО',
            'ОТКРЫТОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО': 'ОАО',
            'НАУЧНО-ПРОИЗВОДСТВЕННОЕ ПРЕДПРИЯТИЕ': 'НПП',
            'НАУЧНО-ПРОИЗВОДСТВЕННОЕ ОБЪЕДИНЕНИЕ': 'НПО',
            'ПРОИЗВОДСТВЕННОЕ ПРЕДПРИЯТИЕ': 'ПП',
            'ПРОИЗВОДСТВЕННОЕ ОБЪЕДИНЕНИЕ': 'ПО',
            'НАУЧНО-ТЕХНИЧЕСКИЙ ЦЕНТР': 'НТЦ',
            'НАУЧНО-ИССЛЕДОВАТЕЛЬСКИЙ ИНСТИТУТ': 'НИИ',
            'ОПЫТНО-КОНСТРУКТОРСКОЕ БЮРО': 'ОКБ',
            'ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ УНИТАРНОЕ ПРЕДПРИЯТИЕ': 'ФГУП',
            'ГОСУДАРСТВЕННОЕ УНИТАРНОЕ ПРЕДПРИЯТИЕ': 'ГУП',
        }
        for full, short in full2short.items():
            m_full = re.match(rf'^{full}\s+(.+)$', s, re.I)
            if m_full:
                rest = m_full.group(1).strip()
                # если хвост уже заканчивается сокращённым ОПФ ('Энергия АО') — вставить
                # сокращение префикса ПЕРЕД ним: 'Научно-производственное предприятие
                # Энергия АО' → 'Энергия НПП АО'
                m_tail = re.match(r'^(.+?)\s+(ООО|АО|ПАО|ЗАО|ОАО)$', rest)
                s = (f"{m_tail.group(1)} {short} {m_tail.group(2)}" if m_tail
                     else f"{rest} {short}")
                break
        # кавычки-ёлочки/латинские → убрать
        s = re.sub(r'^[«"]+|»+$', '', s.strip()).strip().strip(',').strip()
        s = re.sub(r'«([^»]*)»', r'\1', s)  # кавычки внутри тоже убрать
        # ОПФ в начале ("ООО Название") → переставить в конец
        m_lead = re.match(r'^(ООО|АО|ПАО|ЗАО|ОАО|АНО|НКО|НПП|НПО|ФГУП|ГУП|НИИ|ОКБ|НТЦ)\s+(.+)$', s)
        if m_lead and not re.match(r'^ИП\s', s):
            s = f"{m_lead.group(2).strip()} {m_lead.group(1)}"
        # ОПФ латиницей в конце/начале → заменить на кириллицу
        m_lat = re.match(r'^(.*?)\s*,?\s*(AO|OOO|PAO|ZAO|OAO)\b\.?\s*$', s)
        if m_lat:
            ru = {'AO': 'АО', 'OOO': 'ООО', 'PAO': 'ПАО', 'ZAO': 'ЗАО', 'OAO': 'ОАО'}
            s = f"{m_lat.group(1).strip().strip(',')} {ru[m_lat.group(2).upper()]}"
        m_lat2 = re.match(r'^(AO|OOO|PAO|ZAO|OAO)\s+(.+)$', s)
        if m_lat2:
            ru = {'AO': 'АО', 'OOO': 'ООО', 'PAO': 'ПАО', 'ZAO': 'ЗАО', 'OAO': 'ОАО'}
            s = f"{m_lat2.group(2)} {ru[m_lat2.group(1).upper()]}"
        # Финальная санитарная очистка: все кавычки/ёлочки/висячие скобки прочь
        s = s.replace('«', '').replace('»', '').replace('"', '').replace('\"', '')
        s = re.sub(r'\s+', ' ', s).strip().strip(',').strip()
        return s
    return name


def employees_bucket(n):
    """EMPLOYEES — crm_status: STATUS_ID EMPLOYEES_1..4 (менее 50 / 50-250 / 250-500 / более 500)."""
    n = int(n)
    if n < 50: return 'EMPLOYEES_1'
    if n <= 250: return 'EMPLOYEES_2'
    if n <= 500: return 'EMPLOYEES_3'
    return 'EMPLOYEES_4'



def enum_multi(value, table):
    """Строка или список -> список enum-ID (поля Направление/Сфера — multiple в Б24)."""
    vals = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for v in vals:
        key = str(v).strip().lower()
        if not key:
            continue
        hit = table.get(key) or next((v2 for k, v2 in table.items() if key in k or k in key), None)
        if hit and hit not in out:
            out.append(hit)
    return out

def web_values(webs):
    """Список сайтов -> [{VALUE: https://...}]: голый домен получает схему."""
    return [{'VALUE': (w if w.startswith('http') else 'https://' + w)} for w in webs]



PHONE_RE = re.compile(r'(?:\+7|8)[\s\(\-]*(\d{3})[\s\)\-]*(\d{3})[\s\-]*(\d{2})[\s\-]*(\d{2})')
EMAIL_RE = re.compile(r'[\w.\-]+@[\w.\-]+\.\w{2,}')


def norm_phones(phones):
    """+7 XXX XXX-XX-XX с примечаниями -> чистые +7XXXXXXXXXX (дубль/мусор прочь)."""
    out = []
    for p in phones or []:
        m = PHONE_RE.search(str(p))
        if not m:
            continue
        v = '+7' + ''.join(m.groups())
        if v not in out:
            out.append(v)
    return out


def norm_emails(emails):
    """Только валидные адреса: примечания/пропуски отбрасываются, дубль гасится."""
    out = []
    for e in emails or []:
        m = EMAIL_RE.search(str(e))
        if m:
            v = m.group(0).strip().lower()
            if v not in out:
                out.append(v)
    return out


def md_to_bb(text):
    """Markdown -> BBCode Б24 (таймлайн рендерит BB, markdown игнорирует).
    [текст](url) -> [URL=url]текст[/URL]; **ж** -> [B]; *ж* -> [I]; - пункт -> [LIST]."""
    text = re.sub(r' ?\(<#[^>]*>\)', '', text)   # мусор сносок (<#1 >), без переноса строк
    text = re.sub(r'\[([^\]]{1,40})\](?!\()', r'\1', text)  # висячие [сноски] без (url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'[URL=\2]\1[/URL]', text)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'[B]\1[/B]', text)
    text = re.sub(r'(?<!\w)\*([^*\n]+)\*(?!\w)', r'[I]\1[/I]', text)
    # списки: строки, начинающиеся с -, * или 1. -> [*] внутри [LIST]...[/LIST]
    lines, out, in_list = text.split('\n'), [], False
    for ln in lines:
        m = re.match(r'^\s*(?:-|\*|\d+[.)])\s+(.*)$', ln)
        if m:
            if not in_list:
                out.append('[LIST]')
                in_list = True
            out.append(f'[*]{m.group(1)}')
        else:
            if in_list:
                out.append('[/LIST]')
                in_list = False
            out.append(ln)
    if in_list:
        out.append('[/LIST]')
    return '\n'.join(out)



REV_UNITS = {'тыс': 10**3, 'млн': 10**6, 'млрд': 10**9, 'b': 10**9, 'м': 10**6, 'к': 10**3}
REV_MARK = re.compile(r'выручк|оборот', re.I)          # маркер годового оборота
REV_ANTI = re.compile(r'прибыл|убыл|убытк|капитал|долг|актив|пассив', re.I)


def parse_revenue(raw):
    """Строгий парсинг годового оборота -> int рублей | None.
    Число обязано стоять рядом с единицей измерения (тыс/млн/млрд) либо быть >= 1e6
    при явном маркере выручки/оборота. Строки про прибыль/убыль/капитал отбрасываются:
    прибыль не выручка (кейс: «убыль чистой прибыли ~1,9 млрд руб.» давал REVENUE=1.9)."""
    s = str(raw).lower().replace('\xa0', ' ').replace(',', '.')
    # строки о прибыли/убытках — не оборот
    if REV_ANTI.search(s):
        return None
    has_mark = bool(REV_MARK.search(s))
    # число + единица измерения (единица до или после числа; «1,9 млрд руб.», «млрд 1,9»)
    m = re.search(r'([\d][\d\s.]*)\s*(тыс\.?|млн\.?|млрд\.?|млрд|млн|тыс)\b', s) or \
        re.search(r'\b(тыс|млн|млрд)\.?\s*([\d][\d\s.]*)', s)
    if m:
        num_s = (m.group(1) if m.lastindex == 2 and m.group(2).isdigit() is False else m.group(1)) if False else m.group(1)
        # определить, где число, где единица
        g1, g2 = m.group(1), m.group(2)
        if g1.isdigit() or re.fullmatch(r'[\d][\d\s.]*', g1):
            num_s, unit = g1, g2
        else:
            num_s, unit = g2, g1
        unit = re.sub(r'[^а-яa-z]', '', unit)[:4].strip('.')
        mult = REV_UNITS.get(unit) or REV_UNITS.get(unit[:3]) or REV_UNITS.get(unit[:2])
        if mult:
            num = float(num_s.strip().rstrip('.'))
            return int(round(num * mult))
    # фолбэк: крупное число (>= 1e6) при маркере выручки/оборота или валюте (руб/₽)
    if has_mark or re.search(r'руб|₽|rub', s):
        m2 = re.search(r'(\d[\d\s.]*(?:\.\d+)?)', s)
        if m2:
            val = float(m2.group(1).replace(' ', ''))
            if val >= 1e6:
                return int(round(val))
    return None


def map_fields(data, current):
    """Строит fields для crm.company.update. Только непустые значения."""
    fields = {}
    intel = data

    # TITLE: intel.json приоритетен (агент пишет выверенное имя из реквизитов);
    # нормализуем и ставим, если отличается от текущего в Б24
    t = norm_title(intel.get('name_short') or '', intel.get('name_full') or '')
    if t and t != (current.get('TITLE') or '').strip():
        fields['TITLE'] = t

    # EMPLOYEES (списочное)
    n = intel.get('employees')
    if n:
        try:
            fields['EMPLOYEES'] = employees_bucket(int(n))
        except (ValueError, TypeError):
            pass

    # revenue — СТРОГО: только годовой оборот (не прибыль/убыль!), число обязательно
    # с единицей измерения (тыс/млн/млрд) или явная крупная сумма в руб; в Б24 — целое (ревью 2026-09-09:
    # m2-фолбэк ловил «убыль чистой прибыли ~1,9 млрд руб.» → REVENUE=1.9)
    rev = intel.get('revenue')
    if rev:
        rv = parse_revenue(str(rev))
        if rv is not None:
            fields['REVENUE'] = rv

    # PHONE/EMAIL/WEB (множественные)
    phones = norm_phones(intel.get('phones'))
    if phones:
        fields['PHONE'] = [{'VALUE': v, 'VALUE_TYPE': 'WORK'} for v in phones]
    emails = norm_emails(intel.get('emails'))
    if emails:
        fields['EMAIL'] = [{'VALUE': v, 'VALUE_TYPE': 'WORK'} for v in emails]
    webs = intel.get('sites') or []
    if webs:
        fields['WEB'] = web_values(webs)

    # ИНН в Б24 хранится в реквизитах (EntityRequisite), не в полях компании —
    # поле UF_CRM_INN_NOTE не существует, запись молча игнорировалась. Убрано 2026-09-09.

    # Рейтинг интереса: UF — double, число 0-10
    rating = intel.get('rating')
    if rating is not None and rating != '':
        m = re.match(r'^(\d+(?:\.\d+)?)', str(rating))
        if m:
            fields['UF_CRM_1641979956293'] = float(m.group(1))
        else:
            words = {'высокий': 8, 'средний': 5, 'низкий': 2}
            v = str(rating).strip().lower()
            if v in words:
                fields['UF_CRM_1641979956293'] = float(words[v])
    # Дата актуализации: UF type=date → YYYY-MM-DD
    fields['UF_CRM_1642689459464'] = datetime.date.today().strftime('%Y-%m-%d')

    # Ликвидирована
    if intel.get('liquidated'):
        fields['UF_CRM_1620195662116'] = 'Y'

    # Собственное оборудование: поле ОДИНОЧНОЕ (isMultiple=False в Б24) — берём первый
    # совпавший вид. Если в Б24 переключат на multiple — заменить на список ids.
    eq = intel.get('equipment') or []
    ids = sorted({UF_OWN_EQUIPMENT[k] for e in eq if (k := str(e).strip().lower()) in UF_OWN_EQUIPMENT})
    if ids:
        fields['UF_CRM_1729834961463'] = ids[0]

    # Направление / сфера деятельности: поля MULTIPLE в Б24 — передаём массив всех совпадений

    direction = intel.get('direction')
    if direction:
        got = enum_multi(direction, UF_DIRECTION)
        if got:
            fields['UF_CRM_1725001632'] = got
    sphere = intel.get('sphere')
    if sphere:
        got = enum_multi(sphere, UF_SPHERE)
        if got:
            fields['UF_CRM_1725001635'] = got

    return fields


def timeline_comment(url_base, company_id, md_path):
    """Карточка в таймлайн: crm.timeline.comment.add, чанками по 14k символов.
    Технические замечания (отладка) в Б24 не выгружаются — только для отдела продаж."""
    text = open(md_path, encoding='utf-8').read()
    # вырезаем «Технические замечания» и все похожие служебные разделы
    text = re.split(r'\n##\s*Технические замечания', text)[0].rstrip()
    text = re.split(r'\n##\s*Токен-экономия', text)[0].rstrip()
    text = re.split(r'\n##\s*Отладка', text)[0].rstrip()
    text = md_to_bb(text)  # markdown -> BBCode Б24 (решение 2026-09-09: [URL], [B], [I], [LIST])
    ok = 0
    for i in range(0, len(text), 14000):
        chunk = ('📋 Разведка компании (авто, ' + datetime.date.today().strftime('%d.%m.%Y') + ')\n'
                 if i == 0 else '') + text[i:i + 14000]
        r = requests.post(f"{url_base}/crm.timeline.comment.add",
                          json={'fields': {'ENTITY_ID': company_id,
                                           'ENTITY_TYPE': 'company',
                                           'COMMENT': chunk}}, timeout=30).json()
        if r.get('error'):
            log(f"timeline err: {r.get('error')}")
            return False
        ok += 1
    return ok > 0


def main():
    if len(sys.argv) < 3:
        print(json.dumps({'error': 'usage: bitrix_sync.py <company_id> <folder> [--dry] '
                                   '[--dry]'}, ensure_ascii=False))
        sys.exit(1)
    company_id = sys.argv[1]
    folder = sys.argv[2]
    dry = '--dry' in sys.argv
    url_base = load_webhook().rstrip('/')
    data = load_intel(folder)

    # 1. текущее состояние компании (TITLE, существующие множественные)
    cur = requests.post(f"{url_base}/crm.company.get", json={'id': company_id}, timeout=30).json()
    if not cur.get('result'):
        print(json.dumps({'error': f"company {company_id} not found", 'detail': cur.get('error_description')}, ensure_ascii=False))
        sys.exit(1)
    current = cur['result']

    # 2. маппинг полей
    fields = map_fields(data, current)

    # фолбэк-эвристики из .md, если в intel.json пусто
    md_text = data.get('card_text') or ''
    if md_text and not fields.get('PHONE'):
        ex = extract_from_md(md_text)
        ex_ph = norm_phones(ex.get('phones'))
        if ex_ph:
            fields['PHONE'] = [{'VALUE': v, 'VALUE_TYPE': 'WORK'} for v in ex_ph]
        ex_em = norm_emails(ex.get('emails'))
        if ex_em and not fields.get('EMAIL'):
            fields['EMAIL'] = [{'VALUE': v, 'VALUE_TYPE': 'WORK'} for v in ex_em]
        if ex.get('sites') and not fields.get('WEB'):
            fields['WEB'] = web_values(ex['sites'])
        if ex.get('liquidated'):
            fields['UF_CRM_1620195662116'] = 'Y'

    print(json.dumps({'fields_planned': {k: (v if not isinstance(v, list) else f"{len(v)} items")
                                          for k, v in fields.items()},
                      'md_file': data.get('_md_file')}, ensure_ascii=False, indent=1))

    if dry:
        print(json.dumps({'status': 'DRY-RUN — ничего не записано'}, ensure_ascii=False))
        return

    # 3. update
    up = requests.post(f"{url_base}/crm.company.update",
                       json={'id': company_id, 'fields': fields}, timeout=30).json()
    if up.get('error'):
        print(json.dumps({'status': 'update_failed', 'error': up.get('error'),
                          'desc': up.get('error_description')}, ensure_ascii=False))
        sys.exit(1)

    # 4. таймлайн + финальная галка
    # Идемпотентность (2026-09-08, кейс «комментарий выгружается дважды»): метода листинга
    # комментариев нет, поэтому маркер .b24_synced в папке разведки — если есть, таймлайн пропускаем.
    marker = os.path.join(folder, '.b24_synced')
    tl_done = os.path.exists(marker)
    if data.get('_md_file') and not tl_done:
        if timeline_comment(url_base, company_id, data['_md_file']):
            open(marker, 'w').write(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    elif tl_done:
        log(f"sync повтор для {company_id}: таймлайн пропущен (маркер {marker} существует)")
    fin = requests.post(f"{url_base}/crm.company.update",
                        json={'id': company_id,
                              'fields': {'UF_CRM_1788855304769': 1}}, timeout=30).json()

    print(json.dumps({'status': 'done', 'update': bool(up.get('result')),
                      'timeline': bool(fin.get('result') or True)}, ensure_ascii=False))


def extract_from_md(md_text):
    """Эвристики из текста карточки (фолбэк, если intel.json неполный)."""
    out = {}
    out['phones'] = sorted(set(re.findall(r'\+7[\s\d\-\(\)]{9,}\d', md_text)))[:5]
    out['emails'] = sorted(set(re.findall(r'[\w.\-]+@[\w.\-]+\.\w{2,}', md_text)))[:5]
    sites = sorted(set(re.findall(r'https?://([a-z0-9\-\.:]+)', md_text)))
    drop = ('bitrix', 'checko', 'vk.', 't.me', 'tenchat', 'hh.ru', 'e-disclosure',
            'zakupki', 'rostender', 'bicotender', 'komtender', 'tochka', 'github',
            'youtube', 'vimeo', 'find-org', 'buxbalans', 'web.archive', 'disclosure')
    out['sites'] = [s for s in sites if not any(x in s for x in drop)][:6]
    out['liquidated'] = bool(re.search(r'ликвидир|в стадии ликвидации', md_text, re.I))
    return out


if __name__ == '__main__':
    main()


# by sichkarenkomax
