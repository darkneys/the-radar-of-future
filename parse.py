"""Парсер возможностей. Запуск: python parse.py   |   отладка: python parse.py --inspect URL"""
import json, re, sys, time, hashlib, signal, datetime as dt
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
INCL_DEFAULT = "стажир|internship|summer school|летн\\w+ школ|кейс|case championship|конференц|conference|summit|форум|fellowship|стипенд|scholarship|грант"
MONTHS = {m: i for i, m in enumerate(
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(), 1)}
MRX = "|".join(MONTHS)
DATE_RU = re.compile(rf"(\d{{1,2}})(?:\s*[–\-]\s*\d{{1,2}})?\s+({MRX})(?:\s+(\d{{4}}))?", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
DL_WORDS = re.compile(r"дедлайн|apply\s+by|applications?\s+(?:close|due)|due\s+(?:on|by)?|closes?|до\s*$|заявк\w*\s+(?:до|принимаются)|регистрац\w*\s+до|прием заявок|приём заявок|apply by|deadline", re.I)
SECTIONS = [
    ("case", r"кейс|case|чемпионат"),
    ("school", r"летн\w+\s+школ|summer school|школ\w+"),
    ("intern", r"стажир|стаж[её]р|internship|trainee|junior|аналитик"),
    ("summit", r"конференц|форум|саммит|summit|conference|симпозиум"),
]

MEN = {m: i for i, m in enumerate("january february march april may june july august september october november december".split(), 1)}
MEN_RX = "|".join(MEN)
DATE_EN1 = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MEN_RX})(?:,?\s+(\d{{4}}))?", re.I)
DATE_EN2 = re.compile(rf"\b({MEN_RX})\s+(\d{{1,2}})(?!\d)(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", re.I)

OPP_RX = re.compile(r"стажир|стаж[её]р|internship|summer\s+school|летн\w+\s+школ|кейс-?чемпионат|case\s+(?:championship|competition)|fellowship|стипенд|scholarship|грант|конференц|conference|summit|саммит|симпозиум|форум|программ\w+\s+для\s+студент|insight|spring\s+week|trainee|traineeship|олимпиад|hackathon|хакатон", re.I)
NEG_RX = re.compile(r"\bsenior\b|\blead\b|director|head\s+of|\bvp\b|principal|manager|менеджер|ведущ|руководител|директор|от\s+\d+\s+лет|опыт\w*\s+от|experienced|top[\s-]*\d+|best\s+\d+|топ[\s-]*\d+|how\s+to|как\s+(?:получить|найти|попасть)|tips|советы|обзор|review|guide|cookie|privacy|sign\s*in|log\s*in|subscribe|подписа|казино|casino|crypto|ставки|реклама|школьн\\w+|для\\s+школьников|учеников|\\d\\s*[-–]\\s*\\d+\\s*класс|мем", re.I)

CTA_RX = re.compile(r"заявк|регистрац|набор|откликн|подать|приём|прием|apply|registration|присоединя|ждём|ждем", re.I)

def score(title, text, dates, dl, cost):
    if not OPP_RX.search(title + " " + text): return 0  # без слов про стажировки, школы, кейсы и т.п. это не возможность
    s = 0
    if CTA_RX.search(title + " " + text[:400]): s += 1
    if OPP_RX.search(title): s += 2
    elif OPP_RX.search(text): s += 1
    if dates: s += 1
    if dl: s += 1
    if cost != U: s += 1
    if NEG_RX.search(title): s -= 3
    elif NEG_RX.search(text[:250]): s -= 1
    return s

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
    for rx, order in ((DATE_EN1, (1, 2)), (DATE_EN2, (2, 1))):
        for m in rx.finditer(text):
            d, mon = int(m.group(order[0])), MEN[m.group(order[1]).lower()]
            y = int(m.group(3)) if m.group(3) else today().year
            try:
                date = dt.date(y, mon, d)
                if not m.group(3) and date < today() - dt.timedelta(days=30): date = dt.date(y + 1, mon, d)
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
    if not dl and ev_dates and max(d for d, s in ev_dates) < today() - dt.timedelta(days=7): return None  # событие уже прошло
    e["score"] = score(title, text, ev_dates, dl, e["cost"])
    if e["score"] < src.get("min_score", 2): return None  # мало признаков возможности: отбрасываем
    return e

BLOCKED = set()

def get(url, rp_cache={}):
    p = urlparse(url); base = f"{p.scheme}://{p.netloc}"
    if "google.com/alerts/feeds/" in url:  # персональная RSS-лента, выданная тебе для чтения программами
        r = requests.get(url, headers=HEAD, timeout=15); r.raise_for_status(); time.sleep(1); return r.text
    if base not in rp_cache:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEAD, timeout=10)
            if r.status_code == 200: rp.parse(r.text.splitlines())
            else: rp.parse([])  # robots.txt нет: ограничений нет
        except Exception:
            rp.parse([])
        rp_cache[base] = rp
    try:
        if not rp_cache[base].can_fetch(HEAD["User-Agent"], url):
            print(f"  robots.txt запрещает {url}, пропускаю"); BLOCKED.add(url); return None
    except Exception: pass
    r = requests.get(url, headers=HEAD, timeout=15); r.raise_for_status()
    time.sleep(1)
    return r.text

SALARY_RX = re.compile(r"(\d[\d\s]*\s?₽|з/п[^|]*)\s*$", re.I)

def split_digest(src, text, post_url):
    """Подборка вакансий в одном посте («💳 стажер ... 60 000 ₽ | Москва | полный день») -> отдельные карточки."""
    segs = [s.strip(" \ufe0f\n") for s in re.split(r"(?=[\U0001F300-\U0001FAFF\u2600-\u27BF])", text)]
    vac = [s for s in segs if s.count("|") >= 2]
    if len(vac) < 2: return None
    out = []
    for i, s in enumerate(vac):
        parts = [x.strip() for x in s.split("|")]
        head = re.sub(r"^[^\w«\"(]+", "", parts[0])
        m = SALARY_RX.search(head)
        cost = m.group(1).strip() if m else None
        title = (head[:m.start()] if m else head).strip(" -–—")
        e = make_event({**src, "min_score": 2}, title, s, f"{post_url}#v{i}", dict(place=parts[1][:60], fmt=parts[2][:40], cost=cost))
        if e: out.append(e)
    return out

def parse_telegram(src):
    src = {**src, "min_score": max(src.get("min_score", 2), 3)}
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
        purl = a["href"] if a else f"https://t.me/{src['channel']}"
        dig = split_digest(src, text, purl)
        if dig is not None: out += dig; continue
        title = next((l.strip(" •▪️🚀*") for l in text.split("\n") if l.strip()), "Пост")
        e = make_event(src, title, text, purl)
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
    if not out:  # RSS-лента, объявленная на странице
        feeds = [urljoin(src["list_url"], l["href"]) for l in soup.select('link[type*="rss"],link[type*="atom"]') if l.get("href")]
        for f in feeds[:1]:
            try:
                x = get(f); out += rss_events(src, x) if x else []
            except Exception as ex:
                print("  RSS не прочитан:", str(ex)[:60])
    if not out and src.get("harvest", True):
        out = harvest_links(src, soup)
    print(f"  JSON-LD/RSS/ссылки: найдено {len(out)}")
    return out

def rss_events(src, xml):
    import xml.etree.ElementTree as ET
    rx = re.compile(src["include"], re.I) if src.get("include") else None
    root = ET.fromstring(xml.encode("utf-8")); out = []
    ns = "{http://www.w3.org/2005/Atom}"
    for it in list(root.iter("item")) + list(root.iter(ns + "entry")):
        title = (it.findtext("title") or it.findtext(ns + "title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            l = it.find(ns + "link"); link = l.get("href", "") if l is not None else ""
        desc = it.findtext("description") or it.findtext(ns + "summary") or it.findtext(ns + "content") or ""
        text = BeautifulSoup(desc, "html.parser").get_text(" ")
        title = BeautifulSoup(title, "html.parser").get_text(" ").strip()
        if "google.com/url" in link:
            from urllib.parse import parse_qs
            link = parse_qs(urlparse(link).query).get("url", [link])[0]
        if rx and not rx.search(title + " " + text): continue
        e = make_event(src, title, title + ". " + text, link or src["list_url"])
        if e: out.append(e)
    return out

def parse_rss(src):
    xml = get(src["list_url"])
    return rss_events(src, xml) if xml else []

def harvest_links(src, soup):
    """Запасной вариант: ссылки со страницы, чей текст похож на программу или стажировку."""
    rx = re.compile(src.get("include") or INCL_DEFAULT, re.I); seen, out = set(), []
    for a in soup.select("a[href]"):
        txt = a.get_text(" ", strip=True); href = urljoin(src["list_url"], a["href"])
        if not (12 <= len(txt) <= 140) or not rx.search(txt) or href in seen or href.startswith(("mailto:", "javascript:", "#")): continue
        if not re.search(r"20\d\d|programme?|программ|apply|application|заявк|набор|регистрац|registration|deadline|дедлайн|открыт", txt, re.I): continue
        seen.add(href); e = make_event(src, txt, txt, href)
        if e: out.append(e)
        if len(out) >= 12: break
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
    dflt = cfg.get("defaults", {}); events = []; stats = dict(есть=0, пусто=0, robots=0, ошибка=[])
    extra = ROOT / "sources_table.yml"
    if extra.exists():
        cfg["sources"] += (yaml.safe_load(extra.read_text(encoding="utf-8")) or {}).get("sources", [])
    for src in cfg["sources"]:
        if not src.get("enabled", True): continue
        src = {**dflt, **src}; print("•", src["name"])
        try:
            if hasattr(signal, "SIGALRM"):  # жёсткий лимит 45 с на источник (Linux/GitHub Actions)
                def _to(*a): raise TimeoutError("источник отвечает слишком долго")
                signal.signal(signal.SIGALRM, _to); signal.alarm(45)
            got = {"telegram": parse_telegram, "rss": parse_rss}.get(src["type"], parse_site)(src)
            print(f"  найдено: {len(got)}"); events += got
            if got: stats["есть"] += 1
            elif BLOCKED: stats["robots"] += 1
            else: stats["пусто"] += 1
            BLOCKED.clear()
        except BaseException as ex:
            if isinstance(ex, KeyboardInterrupt): raise
            print("  ОШИБКА:", ex); stats["ошибка"].append(src["name"])
        finally:
            if hasattr(signal, "SIGALRM"): signal.alarm(0)
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else []
    for m in manual: m.setdefault("dl_iso", ""); m.setdefault("source", "вручную")
    seen, merged = set(), []
    for e in manual + events:
        k = e["url"] if e["url"] and e["source"] != "вручную" else e["title"].lower()
        k2 = re.sub(r"\W+", " ", e["title"].lower()).strip()
        if k in seen or ("t:" + k2) in seen: continue
        seen.add(k); seen.add("t:" + k2); merged.append(e)
    merged.sort(key=lambda e: (e["dl_iso"] == "", e["dl_iso"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": dt.datetime.now().strftime("%d.%m.%Y %H:%M"), "events": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    build_standalone()
    print(f"ИТОГ по источникам: с находками {stats['есть']}, пусто {stats['пусто']}, запрещено robots.txt {stats['robots']}, ошибок {len(stats['ошибка'])}")
    if stats["ошибка"]: print("Ошибки в источниках:", "; ".join(stats["ошибка"][:15]))
    print(f"Готово: {len(merged)} мероприятий ({len(events)} новых из парсинга)")

if __name__ == "__main__":
    inspect(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--inspect" else main()
