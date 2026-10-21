"""Проверка сайтов из data/sites_to_probe.txt: можно ли их парсить и как.
Запуск: python probe.py   -> печатает таблицу и сохраняет data/probe_report.txt"""
import re, time
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from parse import HEAD, jsonld_events

ROOT = Path(__file__).parent
lines = [l.split("\t") for l in (ROOT / "data" / "sites_to_probe.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
report = []
for dom, name in lines:
    url = dom if dom.startswith("http") else "https://" + dom
    res = dict(dom=dom, name=name, robots="?", status="?", jsonld=0, rss="-", verdict="")
    try:
        from urllib.parse import urlparse
        rp = robotparser.RobotFileParser(f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt")
        rp.read()
        res["robots"] = "да" if rp.can_fetch(HEAD["User-Agent"], url) else "ЗАПРЕЩЕНО"
        if res["robots"] == "да":
            r = requests.get(url, headers=HEAD, timeout=20); res["status"] = r.status_code
            soup = BeautifulSoup(r.text, "html.parser")
            res["jsonld"] = len(jsonld_events(soup))
            feeds = [urljoin(url, l["href"]) for l in soup.select('link[type*="rss"],link[type*="atom"]') if l.get("href")]
            res["rss"] = feeds[0] if feeds else "-"
            js = len(soup.get_text(strip=True)) < 800
            res["verdict"] = ("JSON-LD: добавить как site" if res["jsonld"] else "RSS: добавить как rss" if feeds
                              else "похоже на JS-сайт: нужен Playwright или вручную" if js else "нужны селекторы (--inspect)")
        else:
            res["verdict"] = "пропустить (robots.txt)"
    except Exception as ex:
        res["verdict"] = "ошибка: " + str(ex)[:60]
    line = f"{res['dom'][:60]:60} robots={res['robots']:10} http={res['status']} jsonld={res['jsonld']} rss={res['rss']}  => {res['verdict']}"
    print(line); report.append(line); time.sleep(1.5)
(ROOT / "data" / "probe_report.txt").write_text("\n".join(report), encoding="utf-8")
