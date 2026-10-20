"""Парсер возможностей. Запуск: python parse.py   |   отладка: python parse.py --inspect URL"""
import json, re, sys, time, hashlib, datetime as dt
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse
import requests, yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "events.json"
MANUAL = ROOT / "data" / "manual.json"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; RadarBot/1.0; personal student project)"}
U = "уточнить на сайте"
MONTHS = {m: i for i, m in enumerate(
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(), 1)}
MRX = "|".join(MONTHS)
DATE_RU = re.compile(rf"(\d{{1,2}})(?:\s*[–\-]\s*\d{{1,2}})?\s+({MRX})(?:\s+(\d{{4}}))?", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
DL_WORDS = re.compile(r"дедлайн|до\s*$|заявк\w*\s+(?:до|принимаются)|регистрац\w*\s+до|прием заявок|приём заявок|apply by|deadline", re.I)
SECTIONS = [
    ("case", r"кейс|case|чемпионат"),
    ("school", r"летн\w+\s+школ|summer school|школ\w+"),
    ("intern", r"стажир|internship|trainee|junior|аналитик"),
    ("summit", r"конференц|форум|саммит|summit|conference|симпозиум"),
]

def today(): return dt.date.today()

def find_dates(text):
    """Возвращает список (date, original_text, is_deadline)."""
    res = []
    for m in DATE_RU.finditer(text):
        d, mon = int(m.group(1)), MONTHS[m.group(2).lower()]
        y = int(m.group(3)) if m.group(3) else None
        if y is None:
            y = today().year
            try:
                if dt.date(y, mon, d) < today() - dt.timedelta(days=30): y += 1
            except ValueError: continue
        try: date = dt.date(y, mon, d)
        except ValueError: continue
        res.append((date, m.group(0), bool(DL_WORDS.search(text[max(0, m.start() - 35):m.start()]))))
    for m in DATE_NUM.finditer(text):
        try: date = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError: continue
        res.append((date, m.group(0), bool(DL_WORDS.search(text[max(0, m.start() - 35):m.start()]))))
    return res

def find_cost(text):
    t = text.lower()
    if re.search(r"бесплатн|free of charge|\bfree\b|без оплаты", t): return "бесплатно"
    m = re.search(r"(\d[\d\s]{2,}\s?(?:₽|руб|р\.)|[$€£]\s?\d[\d,\s]*|\d[\d,\s]*\s?(?:usd|eur|gbp|[$€£]))", text, re.I)
    if m: return m.group(0).strip()
    if "оплачива" in t or "paid internship" in t: return "оплачиваемая"
    return U

def classify(text, default):
    for sec, rx in SECTIONS:
        if re.search(rx, text, re.I): return sec
    return default

def make_event(src, title, text, url, extra=None):
    extra = extra or {}
    dates = find_dates(text)
    dl = next(((d, s) for d, s, isdl in dates if isdl), None)
    ev_dates = [(d, s) for d, s, isdl in dates if not isdl]
    first = (ev_dates[0] if ev_dates else None)
    e = dict(sec=classify(title + " " + text, src.get("sec", "summit")), title=title.strip()[:140], org=src.get("org", src["name"]),
             country=src.get("country", U), place=extra.get("place") or U, fmt=extra.get("fmt") or U,
             dates=extra.get("dates") or (first[1] if first else U),
             dl=(dl[1] if dl else U), dl_iso=(dl[0].isoformat() if dl else ""),
             cost=extra.get("cost") or find_cost(text), cond=U,
             desc=re.sub(r"\s+", " ", text).strip()[:400], url=url,
             rf=src.get("rf", "проверить"), tr=src.get("tr", "проверить"), ok=0, source=src["name"])
    if dl and dl[0] < today(): return None  # дедлайн прошёл
    return e

def get(url, rp_cache={}):
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if base not in rp_cache:
        rp = robotparser.RobotFileParser(base + "/robots.txt")
        try: rp.read()
        except Exception: pass
        rp_cache[base] = rp
    try:
        if not rp_cache[base].can_fetch(HEAD["User-Agent"], url):
            print(f"  robots.txt запрещает {url}, пропускаю"); return None
    except Exception: pass
    r = requests.get(url, headers=HEAD, timeout=25); r.raise_for_status()
    time.sleep(1.5)
    return r.text

def parse_telegram(src):
    html = get(f"https://t.me/s/{src['channel']}")
    if not html: return []
    soup = BeautifulSoup(html, "html.parser"); out = []
    rx = re.compile(src["include"], re.I) if src.get("include") else None
    for w in soup.select("div.tgme_widget_message_wrap"):
        t = w.select_one(".tgme_widget_message_text")
        if not t: continue
        text = t.get_text("\n").strip()
        if rx and not rx.search(text): continue
        a = w.select_one("a.tgme_widget_message_date"); tm = w.select_one("time")
        if tm and tm.get("datetime"):
            try:
                age = (today() - dt.datetime.fromisoformat(tm["datetime"]).date()).days
                if age > src.get("max_age_days", 90): continue
            except ValueError: pass
        title = next((l.strip(" •▪️🚀*") for l in text.split("\n") if l.strip()), "Пост")
        e = make_event(src, title, text, a["href"] if a else f"https://t.me/{src['channel']}")
        if e: out.append(e)
    return out

def jsonld_events(soup):
    found = []
    def walk(o):
        if isinstance(o, list): [walk(x) for x in o]
        elif isinstance(o, dict):
            t = o.get("@type"); t = t if isinstance(t, list) else [t]
            if any(str(x).endswith("Event") for x in t): found.append(o)
            for v in o.values():
                if isinstance(v, (dict, list)): walk(v)
    for s in soup.select('script[type="application/ld+json"]'):
        try: walk(json.loads(s.string or ""))
        except Exception: pass
    return found

def parse_site(src):
    html = get(src["list_url"])
    if not html: return []
    soup = BeautifulSoup(html, "html.parser"); out = []
    for j in jsonld_events(soup):
        loc = j.get("location") or {}
        loc = loc[0] if isinstance(loc, list) and loc else loc
        place = loc.get("name") if isinstance(loc, dict) else None
        offers = j.get("offers") or {}
        offers = offers[0] if isinstance(offers, list) and offers else offers
        price = offers.get("price") if isinstance(offers, dict) else None
        cost = ("бесплатно" if str(price) in ("0", "0.0") else f"{price} {offers.get('priceCurrency','')}".strip()) if price not in (None, "") else None
        start = str(j.get("startDate", ""))[:10]
        text = f"{j.get('name','')}. {j.get('description','')}"
        e = make_event(src, j.get("name", "Событие"), text, urljoin(src["list_url"], j.get("url", "")) or src["list_url"],
                       dict(place=place, cost=cost, dates=start or None))
        if e: out.append(e)
    sel = src.get("selectors")
    if sel and not out:
        for it in soup.select(sel["item"]):
            ti = it.select_one(sel.get("title", "a")); a = it.select_one(sel.get("link", "a"))
            if not ti: continue
            text = (it.select_one(sel["text"]).get_text(" ") if sel.get("text") and it.select_one(sel["text"]) else it.get_text(" "))
            e = make_event(src, ti.get_text(" ").strip(), text, urljoin(src["list_url"], a["href"]) if a and a.get("href") else src["list_url"])
            if e: out.append(e)
    print(f"  JSON-LD/селекторы: найдено {len(out)}")
    return out

def inspect(url):
    html = get(url); soup = BeautifulSoup(html, "html.parser")
    print("JSON-LD событий:", len(jsonld_events(soup)))
    print("Ссылки на странице (первые 40), по ним подбери селекторы:")
    for a in soup.select("a[href]")[:40]:
        txt = a.get_text(" ", strip=True)
        if txt: print(f"  {txt[:60]!r:65} -> {a['href'][:80]}  [{a.parent.name}.{'.'.join(a.parent.get('class', []))}]")

def build_standalone():
    """Делает docs/radar.html с данными внутри: открывается двойным кликом и работает без хостинга."""
    if not (ROOT / "docs" / "index.html").exists():
        print("docs/index.html не найден, пропускаю radar.html"); return
    t = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    data = OUT.read_text(encoding="utf-8").replace("</", "<\\/")
    t = t.replace('fetch("events.json").then(r=>r.json())', "Promise.resolve(" + data + ")")
    (ROOT / "docs" / "radar.html").write_text(t, encoding="utf-8")

def main():
    cfg = yaml.safe_load((ROOT / "sources.yml").read_text(encoding="utf-8"))
    dflt = cfg.get("defaults", {}); events = []
    for src in cfg["sources"]:
        if not src.get("enabled", True): continue
        src = {**dflt, **src}; print("•", src["name"])
        try:
            got = parse_telegram(src) if src["type"] == "telegram" else parse_site(src)
            print(f"  найдено: {len(got)}"); events += got
        except Exception as ex:
            print("  ОШИБКА:", ex)
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else []
    for m in manual: m.setdefault("dl_iso", ""); m.setdefault("source", "вручную")
    seen, merged = set(), []
    for e in manual + events:
        k = e["url"] if e["url"] and e["source"] != "вручную" else e["title"].lower()
        if k in seen: continue
        seen.add(k); merged.append(e)
    merged.sort(key=lambda e: (e["dl_iso"] == "", e["dl_iso"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "events": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    build_standalone()
    print(f"Готово: {len(merged)} мероприятий ({len(events)} новых из парсинга)")

if __name__ == "__main__":
    inspect(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--inspect" else main()
