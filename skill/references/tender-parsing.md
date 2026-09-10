# Парсинг тендеров: ЕИС и b2b-center (CDP-рецепты)

Конденсированная выжимка рабочих методов, проверенных в среде Hermes headless
(IP датацентра) 2026-08-28. Полный нарратив — в SKILL.md (раздел «Доступ к ЕИС»
и «Ограничения среды»). Здесь — только точные URL, селекторы и цепочки.

## Подготовка (один раз на сессию)
Chromium ставится: `apt-get install -y chromium` (одобрено пользователем).
Запуск CDP-демона:
```
chromium --headless --no-sandbox --disable-gpu --ignore-certificate-errors \
  --remote-debugging-port=9222 --remote-allow-origins=* about:blank &
```
Проверка: `curl -s http://127.0.0.1:9222/json/version`.
Подключение из Python: `websocket-client` → `create_connection(ws_url)`,
где `ws_url` берётся из `/json/list` (первый `type=="page"`).
Сертификат ГУЦ Минцифры для ЕИС: локально `eis_ca_bundle.pem`
(см. SKILL.md «Доступ к ЕИС»). browser_exec (встроенный harness) chromium
НЕ подхватывает — только прямой CDP через websocket-client.

## ЕИС (zakupki.gov.ru)

### Актуальные лоты (компания = заказчик ИЛИ поставщик)
ПОЛНЫЙ URL (обязательно `search-filter=Дате+размещения` + `pa=on`; без них
ЕИС отдаёт пустую выдачу). Ждать ~15-18 сек после навигации (SPA рендерит):
```
https://zakupki.gov.ru/epz/order/extendedsearch/results.html
  ?searchString={ИНН_ИЛИ_НАЗВАНИЕ}
  &morphology=on
  &search-filter=%D0%94%D0%B0%D1%82%D0%B5+%D1%80%D0%B0%D0%B7%D0%BC%D0%B5%D1%89%D0%B5%D0%BD%D0%B8%D1%8F
  &pageNumber=1&sortDirection=false&recordsPerPage=_10&showLotsInfoHidden=false
  &sortBy=UPDATE_DATE&fz44=on&fz223=on&af=on&ca=on&pc=on&pa=on&currencyIdGeneral=-1
```
Лоты в DOM: `a[href*="view.html"][href*=regNumber]` →
карточка: `https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg}`
Контакты заказчика в карточке: поле «Ответственное должностное лицо», почта, тел.

### 🔑 Поиск ПО НАЗВАНИЮ + сверка ИНН (рекомендуется из headless)
По ИНН ЕИС из headless на IP датацентра ЧАСТО возвращает «0 записей» даже для
активных ОПК (проверено на нескольких заказчиках — стабильно 0). По НАЗВАНИЮ лоты
РЕНДЕРЯТСЯ. Алгоритм:
1. `searchString={НАЗВАНИЕ}` (URL-кодировать кириллицу, `quote(name)`).
2. Собрать лоты (`a[href*=regNumber]`).
3. Для каждого лота открыть printForm, извлечь ИНН заказчика/поставщика
   (regex `\d{10,12}` в тексте карточки) и сравнить с целевым ИНН.
4. Писать ТОЛЬКО совпавшие (помечать `[СОВПАД]`), тёзок — отсекать (`[тёзка]`).
   Одноимённые юрлица иначе засорят карточку чужими закупками.
Готовый runner: `scripts/eis_search_name.py {НАЗВАНИЕ} {ИНН_ЦЕЛИ}`.

### Завершённые закупки (АРХИВ)
Тот же URL + `&contractStage=3`.

### Цепочка лот → КОНТРАКТ → ПОБЕДИТЕЛЬ (компания = поставщик/исполнитель)
1. Берём `regNumber` лота (напр. `0847500000926001836`).
2. Реестр контрактов:
   `https://zakupki.gov.ru/epz/contract/search/results.html?searchString={regNumber}`
   (ждать ~12с). Из текста вытащить `reestrNumber` — регулярка `№\s*(\d{19})`.
3. Карточка контракта (победитель/исполнитель + цена + заказчик):
   `https://zakupki.gov.ru/epz/contract/contractCard/common-info.html?reestrNumber={reestrNumber}`
   ИЛИ printForm:
   `https://zakupki.gov.ru/epz/contract/printForm/view.html?contractReestrNumber={reestrNumber}`
   Искать блок «Поставщик»/«Исполнитель» (regex `Поставщик.{0,500}|Исполнитель.{0,500}`).

⚠ КРИТИЧНАЯ ЛОВУШКА: `searchString={ИНН}` — БЕЗ угловых скобок `<>`.
Если подставить `<4900012214>` (закодировать скобки), ЕИС вернёт «0 записей»,
хотя лоты есть. Скобки `<ИНН>` в примерах пользователя — это МАРКЕР шаблона,
а не литерал. Правильно: `searchString=4900012214`.

Нестабильность: на крупных выборках (Росатом) SPA не успевает за 12-18с, либо
после многих прогонов с одного IP срабатывает rate-limit → «0 записей».
Решение: пауза между запросами (3-8с) ИЛИ повтор с локального IP. Поиск ПО
ИНН из headless часто 0 — предпочитать ПО НАЗВАНИЮ + сверку ИНН (см. выше).
Прокси для ЕИС, по опыту, НЕ помогает (headless-SPA-блок, не IP-бан).

## b2b-center.ru

⚠ `/firms/{id}/` (карточка участника) → `403 Forbidden` по IP сервера Hermes
и через бесплатные прокси. НЕ использовать этот путь.

✅ `/market/` (публичный рынок лотов) → РАБОТАЕТ через CDP.
Актуальные:
```
https://www.b2b-center.ru/market/?f_keyword={НАЗВАНИЕ}
  &searching=1&trade=all&order_by=2&order_by_prev=0&order_dir_prev=0&show=all
```
Архив (завершённые):
```
...&show=archive
```
⚠ `f_keyword` — ПО НАИМЕНОВАНИЮ (не по ИНН!). Подставлять название
(по названию компании). Лоты в DOM: `a[href*="/market/"][href*=tender-]` →
`https://www.b2b-center.ru/market/{slug}/tender-NNNNNNN/`.
По лотам видно, что компания закупает (трубы, круги спецсталей, станки) —
прямой сигнал интереса для COMPANY.

ПОБЕДИТЕЛИ и ПРОТОКОЛЫ (`/protocol/`) и КОНТАКТЫ ОРГАНИЗАТОРА →
`404` / нет блока, требуют аккаунт. Честно писать
«не раскрыто (b2b-center, требуется аккаунт)», НЕ выдумывать.

## Матрица: что публично / что за аккаунтом
| Источник | Публично (CDP) | За аккаунтом |
|---|---|---|
| ЕИС лоты / архив | да | — |
| ЕИС контракт → победитель | да | — |
| ЕИС контакты заказчика | да (printForm) | — |
| b2b-center /market/ лоты | да | — |
| b2b-center победители/протоколы | нет | да |
| b2b-center контакты организатора | нет | да |
| b2b-center /firms/ карточка | нет (403) | — |

## Режимы scripts/cdp_parse.py
```
python3 cdp_parse.py eis {ИНН}            # лоты ЕИС актуальные
python3 cdp_parse.py eis_done {ИНН}       # лоты ЕИС завершённые (contractStage=3)
python3 cdp_parse.py eis_contract {regNumber}   # реестр контрактов + победитель
python3 cdp_parse.py b2b_market {НАЗВАНИЕ}      # лоты b2b-center актуальные
python3 cdp_parse.py b2b_archive {НАЗВАНИЕ}     # лоты b2b-center архив
python3 cdp_parse.py b2b {firm_id}        # 403 — НЕ работает, не использовать
python3 cdp_parse.py search corpmsp {ИНН}       # corpmsp / frprf через CDP
```

---
by sichkarenkomax
