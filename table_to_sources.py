"""Читает data/istochniki.xlsx (колонка D «Сайт / где искать», ссылки через « ; ») и создаёт sources_table.yml.
Запуск: python table_to_sources.py"""
from pathlib import Path
from urllib.parse import urlparse
import re, openpyxl, yaml
ROOT = Path(__file__).parent
ws = openpyxl.load_workbook(ROOT / "data" / "istochniki.xlsx")["Источники"]
INCL = "стажир|internship|школ|summer|кейс|case|конференц|conference|summit|форум|fellowship|стипенд|scholarship|грант|заявк|набор|программ"
sec = lambda c: "grant" if re.search(r"финансирован|стипенд|грант", c.lower()) else "intern" if "стажир" in c.lower() else "school" if "школ" in c.lower() else "case" if "кейс" in c.lower() else "summit"
country = lambda c: "Россия" if "Россия" in c else "Турция" if "Турция" in c else "Международно"
out, probe = [], []
for r in ws.iter_rows(min_row=2, values_only=True):
    cat, name, urls, rf, tr, method = r[1], r[2], r[3] or "", r[6], r[7], (r[8] or "")
    for u in [x.strip() for x in str(urls).split(";") if x.strip().startswith("http")]:
        host = urlparse(u).netloc.replace("www.", "")
        s = dict(name=name if ";" not in urls else f"{name} ({host})", org=name, sec=sec(cat), country=country(cat), rf=rf, tr=tr, include=INCL)
        if "t.me/" in u:
            ch = u.rstrip("/").split("/")[-1]
            s.update(type="telegram", channel=ch, org=f"Telegram @{ch}", country="Россия")
        elif re.search(r"(rss|feed|\.xml)", u, re.I):
            s.update(type="rss", list_url=u)
        else:
            s.update(type="site", list_url=u)
        m = method.lower()
        if "knightfrank.com" in u:
            s.update(enabled=False, skip_reason="сайт не отвечает серверам GitHub (зависание)"); out.append(s); probe.append(f"{u}\t{name}"); continue
        if "playwright" in m or m.strip().startswith("вручную"):
            s.update(enabled=False, skip_reason="сайт на JS или ведётся вручную: добавляй карточки в data/manual.json")
        else:
            s["enabled"] = True
        out.append(s); probe.append(f"{u}\t{name}")
(ROOT / "sources_table.yml").write_text(yaml.safe_dump({"sources": out}, allow_unicode=True, sort_keys=False), encoding="utf-8")
(ROOT / "data" / "sites_to_probe.txt").write_text("\n".join(probe), encoding="utf-8")
print(f"Ссылок: {len(out)}; включено в парсер: {sum(1 for s in out if s['enabled'])}")
