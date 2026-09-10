#!/usr/bin/env python3
"""company_search_helper.py — вспомогательный runner для навыка comp-intel.
Собирает базовые данные о компании через DDGS (с retry по бэкендам) + прямые GET
на публичные источники. БЕЗ системных правок. Сертификаты/прокси — опционально.

Запуск:
  python3 company_search_helper.py "ООО Пример" 1234567890
  python3 company_search_helper.py "Название"            # только поиск
  python3 company_search_helper.py "" 1234567890         # только по ИНН

Источники данных (приоритет из SKILL.md):
  - DDGS: новости, соцсети (VK/TG/MAX), b2b-площадки, ЕИС-упоминания
  - list-org.com: ЕГРЮЛ/финансы/руководители (бесплатная альтернатива СПАРК)
  - rusprofile / zachestnyibiznes: реквизиты
Примечание: ЕИС (лоты), b2b-center, corpmsp, frprf, budget.gov.ru парсятся
ЧЕРЕЗ browser_exec (см. SKILL.md) — здесь только поисковые зацепки + статика.
"""
import sys, os, time, glob, subprocess, json

# --- venv с ddgs ---
VENV = "/tmp/leadenv"
def ensure_venv():
    if not os.path.exists(os.path.join(VENV, "bin", "python")):
        subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
        subprocess.run([os.path.join(VENV, "bin", "pip"), "install", "-q", "ddgs", "requests"], check=True)
    for p in glob.glob(os.path.join(VENV, "lib", "python*", "site-packages")):
        if p not in sys.path:
            sys.path.insert(0, p)

# сброс прокси-env (DDGS падает на socks)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

BACKENDS = ["lite", "duckduckgo", "html"]

def ddgs_search(query, max_results=8):
    """Поиск с retry по бэкендам DDGS."""
    from ddgs import DDGS
    last_err = None
    for b in BACKENDS:
        try:
            res = DDGS().text(query, max_results=max_results, backend=b, timelimit="y")
            if res:
                return res
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    # вернуть ошибку, если все бэкенды упали
    return [{"error": f"all backends failed: {last_err}"}]

def listorg(inn):
    """Бесплатная выжимка ЕГРЮЛ/финансов с list-org.com (статика)."""
    import requests
    try:
        r = requests.get(f"https://www.list-org.com/search?q={inn}&type=inn",
                         timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and "company" in r.text:
            # найти ссылку на карточку
            import re
            m = re.search(r'/company/(\d+)', r.text)
            if m:
                cid = m.group(1)
                r2 = requests.get(f"https://www.list-org.com/company/{cid}/", timeout=20)
                return {"company_id": cid, "url": f"https://www.list-org.com/company/{cid}/",
                        "len": len(r2.text), "status": r2.status_code}
        return {"status": r.status_code, "found": False}
    except Exception as e:
        return {"error": str(e)[:150]}

def main():
    ensure_venv()
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    inn = sys.argv[2] if len(sys.argv) > 2 else ""
    queries = []
    if name:
        queries += [f'{name} ИНН {inn}' if inn else f'{name} контакты отдел закупок',
                    f'{name} новости производство 2025 2026']
    if inn:
        queries += [f'{inn} ЕГРЮЛ', f'{inn} тендер победитель закупка',
                    f'{inn} субсидия грант МСП', f'{inn} max.ru канал telegram']
    print(f"=== Lead search: name='{name}' inn='{inn}' ===")
    for q in queries:
        print("\n" + "=" * 70)
        print("Q:", q)
        for it in ddgs_search(q):
            if "error" in it:
                print("  ERR:", it["error"]); break
            print(f"- {it.get('title')}\n  {it.get('href')}\n  {it.get('body','')[:200]}")
        time.sleep(1.0)
    if inn:
        print("\n" + "=" * 70)
        print("list-org.com (бесплатно):")
        print(json.dumps(listorg(inn), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()


# by sichkarenkomax
