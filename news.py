"""Деловая лента: собирает свежие бизнес-новости из RSS и Telegram в docs/news.json.
Запуск отдельно: python news.py  (parse.py вызывает его сам в конце)."""
import datetime as dt, email.utils, json, re, sys, time
from pathlib import Path
import requests, yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "news.json"
HEAD = {"User-Agent": "Mozilla/5.0 (compatible; RadarOfFuture/2.0; +https://github.com/darkneys/the-radar-of-future)"}
NOISE = re.compile(r"гороскоп|погода|футбол|хоккей|матч|сериал|концерт|знаменитост|звезд[аы] шоу|рецепт|horoscope|celebrity|football|soccer|nfl|nba|реклама|erid|\(video\)|podcast|подкаст|live:", re.I)


BIZ = re.compile(r"рын[оку]|компани|сделк|выручк|прибыл|акци|облигац|ставк|ipo|слиян|поглощ|инвест|market|stock|shares|bond|deal|merger|acquisi|revenue|profit|earnings|rates?\b|inflation|ceo|startup|funding|valuation|tariff|bank", re.I)


def now(): return dt.datetime.now(dt.timezone.utc)


def clean(html, n=None):
    t = re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ")).strip()
    return (t[:n].rsplit(" ", 1)[0] + "…") if n and len(t) > n else t


def parse_date(s):
    if not s: return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try: d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError: return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def fetch(url):
    r = requests.get(url, headers=HEAD, timeout=15)
    if r.status_code >= 500: time.sleep(3); r = requests.get(url, headers=HEAD, timeout=15)
    r.raise_for_status(); r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
    return r.text


def from_rss(f):
    import xml.etree.ElementTree as ET
    xml = fetch(f["url"]).lstrip("\ufeff").strip()
    root = ET.fromstring(xml.encode("utf-8")); ns = "{http://www.w3.org/2005/Atom}"; out = []
    for it in list(root.iter("item")) + list(root.iter(ns + "entry")):
        title = clean(it.findtext("title") or it.findtext(ns + "title"))
        link = (it.findtext("link") or "").strip()
        if not link:
            l = it.find(ns + "link"); link = l.get("href", "") if l is not None else ""
        when = parse_date(it.findtext("pubDate") or it.findtext(ns + "updated") or it.findtext(ns + "published")
                          or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        desc = clean(it.findtext("description") or it.findtext(ns + "summary") or "", 220)
        if title and link: out.append(dict(t=title, url=link, when=when, desc=desc))
    return out


def from_telegram(f):
    soup = BeautifulSoup(fetch(f"https://t.me/s/{f['channel']}"), "html.parser"); out = []
    for w in soup.select("div.tgme_widget_message_wrap"):
        t = w.select_one(".tgme_widget_message_text"); tm = w.select_one("time"); a = w.select_one("a.tgme_widget_message_date")
        if not t or not tm: continue
        lines = [l.strip(" •▪️*") for l in t.get_text("\n").split("\n") if len(l.strip()) > 3]
        if not lines: continue
        title = lines[0] if len(lines[0]) <= 160 else clean(lines[0], 150)
        desc = clean(" ".join(lines[1:]), 220)
        out.append(dict(t=title, url=a["href"] if a else f"https://t.me/{f['channel']}", when=parse_date(tm.get("datetime")), desc=desc))
    return out


def build():
    cfg = yaml.safe_load((ROOT / "news.yml").read_text(encoding="utf-8"))
    st = cfg.get("settings", {}); max_age = dt.timedelta(hours=st.get("max_age_hours", 48))
    prof = yaml.safe_load((ROOT / "profile.yml").read_text(encoding="utf-8"))
    tag_rx = {k: re.compile(v["words"], re.I) for k, v in prof["interests"].items()}
    items, seen, ok = [], set(), 0
    for f in cfg["feeds"]:
        try:
            got = from_telegram(f) if f.get("type") == "telegram" else from_rss(f)
        except Exception as ex:
            print(f"  новости: {f['name']} — ошибка: {str(ex)[:90]}"); continue
        n = 0
        for x in sorted(got, key=lambda x: x["when"] or now(), reverse=True):
            if not x["when"] or now() - x["when"] > max_age or NOISE.search(x["t"]): continue
            key = re.sub(r"[^a-zа-яё0-9]+", " ", x["t"].lower())[:70]
            if key in seen: continue
            seen.add(key)
            text = x["t"] + " " + x["desc"]
            tags = [k for k, rx in tag_rx.items() if rx.search(text)]
            if not tags: tags = list(f.get("topics", []))  # своих совпадений нет: берём тему ленты
            hits = sum(1 for k, rx in tag_rx.items() if rx.search(text))
            if f.get("strict") and not hits and not BIZ.search(text): continue
            items.append(dict(t=x["t"], url=x["url"], desc=x["desc"], src=f["name"].split(".")[0], region=f.get("region", "ru"),
                              tags=tags[:3], ts=x["when"].astimezone(dt.timezone.utc).isoformat(timespec="minutes"), _hits=hits))
            n += 1
            if n >= st.get("per_source", 6): break
        ok += 1; print(f"  новости: {f['name']} — {n}")
    if not items:
        print("Новостей не собрано, оставляю прошлую ленту"); return
    # свежесть + совпадение с интересами, но так, чтобы в ленте были и Россия, и мир
    def rank(x):
        age_h = (now() - dt.datetime.fromisoformat(x["ts"])).total_seconds() / 3600
        return x["_hits"] * 6 - age_h
    items.sort(key=rank, reverse=True)
    total = st.get("total", 36); ru = [x for x in items if x["region"] == "ru"]; intl = [x for x in items if x["region"] != "ru"]
    pick = ru[: total // 2] + intl[: total // 2]
    pick += [x for x in items if x not in pick][: total - len(pick)]
    pick.sort(key=lambda x: x["ts"], reverse=True)
    for x in pick: x.pop("_hits", None)
    OUT.write_text(json.dumps(dict(updated=now().isoformat(timespec="minutes"), sources=ok, count=len(pick), items=pick),
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Деловая лента: {len(pick)} новостей из {ok} источников")


if __name__ == "__main__":
    build()
