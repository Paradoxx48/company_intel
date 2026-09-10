#!/usr/bin/env python3
"""cdp_parse.py — универсальный CDP-парсер для comp-intel.
Поднимает/использует локальный headless Chromium (DevTools 9222) и вытягивает
данные с SPA-сайтов, которые не отдают статику (ЕИС, b2b-center, corpmsp, ФРП).

Требования (локально, без системных правок):
  chromium --headless --no-sandbox --disable-gpu --ignore-certificate-errors \
           --remote-debugging-port=9222 --remote-allow-origins=* about:blank &
  pip install websocket-client  (в /tmp/leadenv)

Запуск:
  python3 cdp_parse.py eis 1234567890        # лоты ЕИС по ИНН (актуальные)
  python3 cdp_parse.py eis_done 1234567890   # лоты ЕИС ЗАВЕРШЁННЫЕ (contractStage=3)
  python3 cdp_parse.py eis_contract 0847500000926001836   # реестр контрактов + победитель по regNumber лота
  python3 cdp_parse.py b2b_market "ООО Пример"  # актуальные лоты b2b-center по НАЗВАНИЮ
  python3 cdp_parse.py b2b_archive "ООО Пример"  # архив (завершённые) по НАЗВАНИЮ
  python3 cdp_parse.py b2b 27457             # закупки b2b-center (id фирмы, 403 — не работает)
  python3 cdp_parse.py search corpmsp 1234567890   # поиск по сайту

Примечание: из среды Hermes (IP датацентра) b2b-center может вернуть 403,
а ЕИС — не отдать лоты (SPA-блок). См. SKILL.md «Ограничения среды».
"""
import json, time, re, sys, urllib.request
from websocket import create_connection

CDP = "http://127.0.0.1:9222"

def get_target():
    with urllib.request.urlopen(f"{CDP}/json/list") as r:
        for t in json.load(r):
            if t.get("type") == "page":
                return t["webSocketDebuggerUrl"]
    req = urllib.request.Request(f"{CDP}/json/new?about:blank", method="PUT", data=b"")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["webSocketDebuggerUrl"]

def connect():
    ws = create_connection(get_target(), timeout=90)
    st = {"mid": 0}
    def send(m, p=None):
        st["mid"] += 1
        ws.send(json.dumps({"id": st["mid"], "method": m, "params": p or {}}))
        return st["mid"]
    def wait_ev(m, to=30):
        t0 = time.time()
        while time.time() - t0 < to:
            try: d = json.loads(ws.recv())
            except: continue
            if d.get("method") == m: return d
    def evalx(e, to=12):
        eid = send("Runtime.evaluate", {"expression": e, "returnByValue": True})
        t0 = time.time()
        while time.time() - t0 < to:
            try: d = json.loads(ws.recv())
            except: continue
            if d.get("id") == eid and "result" in d:
                return d["result"].get("result", {}).get("value")
    return ws, send, wait_ev, evalx

def eis(inn):
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    # ВАЖНО: searchString={ИНН} БЕЗ угловых скобок <>. Скобки (<ИНН>) — это
    # шаблон, ЕИС их НЕ понимает и возвращает 0. Ждать ~12с (SPA рендер).
    url=(f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html"
         f"?searchString={inn}&morphology=on&pageNumber=1&sortDirection=false"
         f"&recordsPerPage=_10&showLotsInfoHidden=false&sortBy=UPDATE_DATE"
         f"&fz44=on&fz223=on&af=on&ca=on&pc=on&currencyIdGeneral=-1")
    send("Page.navigate", {"url": url}); we("Page.loadEventFired", 30); time.sleep(12)
    recs = ev("document.body.innerText.match(/\\d+\\s*запис/)?document.body.innerText.match(/\\d+\\s*запис/)[0]:'NONE'")
    print("RECORDS:", recs)
    for _ in range(6):
        links = ev("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="view.html"][href*=regNumber]')).map(a=>a.href))""")
        if links and links != "[]":
            ll = json.loads(links)
            real = [l for l in ll if re.search(r"regNumber=\d{10,20}", l)]
            if real:
                print("LOTS:", len(real))
                for l in real[:50]: print("  ", l)
                # раскрыть первые 3 лота: предмет + заказчик/поставщик
                for l in real[:3]:
                    reg = re.search(r"regNumber=(\d+)", l).group(1)
                    send("Page.navigate", {"url": f"https://zakupki.gov.ru/epz/order/notice/printForm/view.html?regNumber={reg}"})
                    we("Page.loadEventFired", 25); time.sleep(6)
                    info = ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,400)")
                    print(f"  [{reg}] {info[:350]}")
                ws.close(); return
        time.sleep(3)
    print("EIS: лоты не извлечены (возможно 0 записей ИЛИ headless rate-limit — повторить позже/с прокси).")
    ws.close()

def eis_done(inn):
    """ЕИС завершённые закупки (contractStage=3). Тот же метод, что eis()."""
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    url=(f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={inn}"
         f"&morphology=on&pageNumber=1&sortDirection=false&recordsPerPage=_10&showLotsInfoHidden=false"
         f"&sortBy=UPDATE_DATE&fz44=on&fz223=on&af=on&ca=on&pc=on&currencyIdGeneral=-1&contractStage=3")
    print("NAV (EIS done):", url)
    send("Page.navigate", {"url": url}); we("Page.loadEventFired", 30); time.sleep(12)
    txt=ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,300)")
    print("RECORDS:", (re.search(r"\\d+\\s*запис", txt).group(0) if re.search(r"\\d+\\s*запис", txt) else "NONE"))
    lots=ev("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="view.html"][href*=regNumber]')).map(a=>({h:a.href,t:a.innerText.replace(/\\s+/g,' ').slice(0,80)})).slice(0,15))""")
    if lots and lots!="[]":
        for l in json.loads(lots): print(f"  [{l['t']}]\n   {l['h']}")
    else:
        print("  нет лотов")
    ws.close()

def eis_contract(regnumber):
    """Реестр контрактов ЕИС по regNumber лота → reestrNumber → победитель/цена."""
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    url=(f"https://zakupki.gov.ru/epz/contract/search/results.html?searchString={regnumber}"
         f"&morphology=on&search-filter=%D0%94%D0%B0%D1%82%D0%B5+%D1%80%D0%B0%D0%B7%D0%BC%D0%B5%D1%89%D0%B5%D0%BD%D0%B8%D1%8F"
         f"&pageNumber=1&sortDirection=false&recordsPerPage=_10&showLotsInfoHidden=false&sortBy=UPDATE_DATE&contractStage=3&currencyIdGeneral=-1")
    print("NAV (EIS contract):", url)
    send("Page.navigate", {"url": url}); we("Page.loadEventFired", 30); time.sleep(12)
    recs=ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,300)")
    print("REGISTRY:", (re.search(r"\\d+\\s*запис", recs).group(0) if re.search(r"\\d+\\s+запис", recs) else recs[:200]))
    reestr=ev("""(()=>{const m=document.body.innerText.match(/№\\s*(\\d{19})/);return m?m[1]:''})()""")
    if reestr:
        print("reestrNumber:", reestr)
        curl=f"https://zakupki.gov.ru/epz/contract/printForm/view.html?contractReestrNumber={reestr}"
        print("CARD:", curl)
        send("Page.navigate", {"url": curl}); we("Page.loadEventFired", 30); time.sleep(10)
        card=ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,5000)")
        w=re.search(r"(Поставщик.{0,500}|Исполнитель.{0,500}|ПОБЕДИТЕЛЬ.{0,500})", card)
        print("WINNER/CONTRACT:", w.group(1)[:500] if w else card[-1500:])
    else:
        print("reestrNumber не найден в тексте (повторить/прокси)")
    ws.close()


def b2b(firm_id):
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": f"https://www.b2b-center.ru/firms/ooo-metmash/{firm_id}/"})
    we("Page.loadEventFired", 30); time.sleep(8)
    t = ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,500)")
    print("B2B firms:", t)
    ws.close()

def b2b_market(name):
    """Поиск лотов на b2b-center по НАИМЕНОВАНИЮ (не по ИНН!). Работает из среды Hermes."""
    from urllib.parse import quote
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    url=(f"https://www.b2b-center.ru/market/?f_keyword={quote(name)}"
         f"&searching=1&trade=all&order_by=2&order_by_prev=0&order_dir_prev=0&show=all")
    print("NAV:", url)
    send("Page.navigate", {"url": url}); we("Page.loadEventFired", 30); time.sleep(10)
    txt=ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,300)")
    print("FORBIDDEN?", "Forbidden" in txt)
    lots=ev("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="/market/"][href*=tender-]')).map(a=>({h:a.href,t:a.innerText.replace(/\\s+/g,' ').slice(0,90)})).slice(0,30))""")
    if lots and lots!="[]":
        print("LOTS:")
        for it in json.loads(lots):
            print(f"  {it['t']}\n   {it['h']}")
    else:
        print("LOTS: none / 0 актуальных")
    ws.close()

def search(site, q):
    urls = {
        "corpmsp": f"https://corpmsp.ru/search?q={q}",
        "frprf": f"https://frprf.ru/search?q={q}",
    }
    if site not in urls:
        print("unknown site", site); return
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    send("Page.navigate", {"url": urls[site]})
    we("Page.loadEventFired", 25); time.sleep(7)
    print(f"{site}:", ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,600)"))
    ws.close()

def b2b_archive(name):
    """Архив (завершённые) лоты b2b-center по НАЗВАНИЮ. Работает из среды Hermes."""
    from urllib.parse import quote
    ws, send, we, ev = connect()
    send("Page.enable"); send("Runtime.enable")
    url=(f"https://www.b2b-center.ru/market/?f_keyword={quote(name)}"
         f"&searching=1&trade=all&order_by=2&order_by_prev=0&order_dir_prev=0&show=archive")
    print("NAV (B2B archive):", url)
    send("Page.navigate", {"url": url}); we("Page.loadEventFired", 30); time.sleep(10)
    txt=ev("document.body.innerText.replace(/\\s+/g,' ').slice(0,300)")
    print("FORBIDDEN?", "Forbidden" in txt)
    lots=ev("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="/market/"][href*=tender-]')).map(a=>({h:a.href,t:a.innerText.replace(/\\s+/g,' ').slice(0,90)})).slice(0,30))""")
    if lots and lots!="[]":
        print("ARCHIVE LOTS:")
        for it in json.loads(lots):
            print(f"  {it['t']}\n   {it['h']}")
    else:
        print("ARCHIVE LOTS: none / 0")
    ws.close()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "eis"
    arg = sys.argv[2] if len(sys.argv) > 2 else "1234567890"
    if mode == "eis": eis(arg)
    elif mode == "eis_done": eis_done(arg)
    elif mode == "eis_contract": eis_contract(arg)
    elif mode == "b2b": b2b(arg)
    elif mode == "b2b_market": b2b_market(arg)
    elif mode == "b2b_archive": b2b_archive(arg)
    elif mode == "search": search(sys.argv[2], sys.argv[3])
    else: print("modes: eis <inn> | eis_done <inn> | eis_contract <regNumber> | b2b <firm_id> | b2b_market <название> | b2b_archive <название> | search <corpmsp|frprf> <q>")


# by sichkarenkomax
