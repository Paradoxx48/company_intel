# Основная настройка

## 1\. Размещение файлов

Скопируйте `parsers/`, `scripts/`, `configs/` в рабочий каталог, например:

```
/root/comp\\\_intel/
├── parsers/     (11 парсеров + BPMN)
├── scripts/     (утилиты)
└── configs/
```

Задайте переменные окружения (или отредактируйте `configs/config.py`):

|Переменная|Дефолт|Назначение|
|-|-|-|
|`COMP\\\_INTEL\\\_BASE`|`/root/comp\\\_intel`|каталог, куда пишутся разведки|
|`COMP\\\_INTEL\\\_PYTHON`|`python3`|python с установленным ddgs (можно venv)|
|`COMP\\\_INTEL\\\_DDGS\\\_PKGS`|—|путь к site-packages venv (если ddgs в venv)|

Зависимости: `pip install ddgs requests beautifulsoup4 pypdf urllib3 websocket-client`;
утилиты ОС: `curl`, `dig` (пакет dnsutils), опционально headless Chromium для `cdp\\\_parse.py`.

## 2\. Профиль вашей компании (обязательно!)

Заполните `skill/references/company-capabilities.md` — это основа скоринга:
агент сравнивает найденные потребности клиента с вашим профилем и выставляет рейтинг 0-10.
Пустой профиль = дежурный низкий скоринг.

Сюда можее добавить производственные мощности, каталог, целевую аудиторию, ваши преимущества и т.д.

## 3\. Ключи и доступы

Создайте env-файлы из `configs/\\\*.example` (chmod 600):

* `.env.checko` — `CHECKO\\\_API\\\_KEY=...` (тариф ≥100 запросов/день; без ключа работает HTML-парсер);
* `.env.b24` — `BITRIX\\\_WEBHOOK\\\_URL=http://<host>/rest/<N>/<key>` (входящий вебхук с правами crm);
* `.env.vk` — `VK\\\_SERVICE\\\_TOKEN` (описание группы/посты) или `VK\\\_USER\\\_TOKEN` (дополнительно поиск сотрудников).

## 4\. Формат папки разведки

Каждая разведка — отдельная папка `{COMP\\\_INTEL\\\_BASE}/<YYMMDD>\\\_<Название>\\\_<ИНН|ОГРН>`.
Скрипты ищут папку по хвостовому идентификатору в имени, поэтому имя соблюсти обязательно.
Агент создаёт папку и ведёт конвейер; вспомогательные файлы (`intel.json`, `checko\\\_raw.txt`,
`debug.log`) пишутся туда же.

## 5\. Порядок вызовов (шпаргалка)

```bash
# поиск
python3 parsers/search\\\_all.py "Название Город"
python3 parsers/search\\\_all.py "Название ИНН"
# реквизиты/финансы (API → HTML автоматически)
python3 parsers/requisites\\\_parser.py <ИНН|ОГРН>
# домены (включая альтернативные TLD)
python3 parsers/domain\\\_probe.py domain.ru --tld-alts
# закупки
python3 parsers/zakupki\\\_223\\\_parser.py <ИНН> \\\[max\\\_pages]
python3 parsers/zakupki\\\_tochka\\\_parser.py <ИНН> \\\[max\\\_pages]
# выгрузка в Б24 (после карточки и intel.json)
python3 parsers/bitrix\\\_sync.py <company\\\_id> "<папка>"
```

## 6\. Ретраи и лимиты (важно знать)

* **Checko**: батчи ≤5 компаний, пауза 10 мин между пачками; 429 → ретрай-цикл
(`scripts/checko\\\_retry\\\_loop.py`) для 3+ компаний, для 1-2 — фолбэк find-org/buxbalans
(автоматически в requisites\_parser). Капча решается вручную в браузере — сообщите пользователю.
* **hh.ru**: пауза ≥5 с между страницами; гиганты (≥6000 вакансий) режутся до 2 страниц.
* **Google**: не чаще 1 раза в 10 мин (rate-limit файл `.google\\\_last\\\_ts`).
* **ЕИС**: только для домена zakupki.gov.ru — `verify=False` (самоподписанный сертификат).

## 7\. Очистка

`scripts/cleanup\\\_old\\\_intel.py \\\[--dry] \\\[--retention N]` — удаляет папки разведки старше
5 дней (лог `{BASE\\\_DIR}/cleanup.log`). Ставьте на cron (ежедневно) — вывод пустой,
когда удалять нечего.

\---

by sichkarenkomax

