"""Обогащение карточек: ИИ-разбор полей, регион, теги интересов, рейтинг «подходит мне», дедлайны.

ИИ-разбор (по желанию). Нужен ключ любого сервиса с OpenAI-совместимым API:
  LLM_API_KEY   — ключ (по умолчанию бесплатный Google Gemini: https://aistudio.google.com/apikey)
  LLM_BASE_URL  — адрес API, по умолчанию https://generativelanguage.googleapis.com/v1beta/openai
  LLM_MODEL     — модель, по умолчанию gemini-2.5-flash
  LLM_MAX_ITEMS — сколько новых записей разбирать за запуск (по умолчанию 200)
Результаты кешируются в data/llm_cache.json, поэтому каждая запись разбирается один раз.
Без ключа всё работает на правилах (регулярки), просто поля будут заполнены хуже.
"""
import json, os, re, time, hashlib, datetime as dt
from pathlib import Path
import requests, yaml

ROOT = Path(__file__).parent
CACHE = ROOT / "data" / "llm_cache.json"
PROFILE = yaml.safe_load((ROOT / "profile.yml").read_text(encoding="utf-8"))
U = "уточнить на сайте"
SECS = ("conf", "grant", "case", "intern", "school", "other")
TAGS = tuple(PROFILE["interests"])
RX = {k: re.compile(v["words"], re.I) for k, v in PROFILE["interests"].items()}
PRESTIGE = re.compile(PROFILE.get("prestige", "$^"), re.I)
AVOID = re.compile(PROFILE.get("avoid", "$^"), re.I)
RU_RX = re.compile(r"росси|москв|петербург|спб|казан|новосибир|екатеринбург|сколково|вшэ|мгу|мфти|₽|руб\b|\.ru\b|рф\b", re.I)

def today(): return dt.date.today()
def key(e): return hashlib.sha1((e.get("url", "") + "|" + e.get("title", "")).encode()).hexdigest()[:16]
def empty(v): return v in (None, "", U, "уточнить", "-", "—") or (isinstance(v, str) and re.match(r"(уточнить|не указан|нет данных|неизвестн|n/a|not specified)", v.strip().lower()) is not None)

# ---------- ИИ ----------
PROMPT = """Ты помогаешь вести «Радар возможностей» для студентки. Профиль: {about}
Интересы (теги): {tags}.
Сегодня {today}. Ниже записи, собранные парсером из Telegram-каналов, RSS и сайтов. Для КАЖДОЙ записи верни объект:
{{"i": номер,
 "keep": true/false — false, если это не возможность для студента (новость, реклама курса, вакансия для опытных, для школьников, мероприятие уже прошло, мусор),
 "sec": одно из conf (конференции, форумы, саммиты), grant (стипендии, гранты, fellowships), case (кейс-чемпионаты и бизнес-конкурсы), intern (стажировки, spring weeks, insight-программы), school (летние/зимние школы, интенсивы), other (хакатоны, олимпиады, акселераторы, прочее),
 "region": "ru" если проводится в России или российской организацией, иначе "intl",
 "title": короткое понятное название на русском без эмодзи (бренды и названия программ оставь как есть),
 "org": организатор,
 "place": город/страна или "онлайн",
 "fmt": "онлайн" / "очно" / "гибрид" + детали (длительность, нагрузка),
 "dates": даты проведения текстом на русском,
 "start_iso": дата начала YYYY-MM-DD или "",
 "dl": дедлайн подачи текстом на русском,
 "dl_iso": дедлайн YYYY-MM-DD или "",
 "cost": стоимость участия или "бесплатно" / "оплачиваемая" (для стажировок),
 "funded": true, если покрываются расходы (полная стипендия, оплачиваемая стажировка, бесплатный проезд/проживание),
 "cond": кто может участвовать (курс, возраст, гражданство, язык) — коротко,
 "desc": 1–2 предложения на русском: что это и чем полезно,
 "tags": подмножество {tags},
 "fit": 0–10 — насколько это сильная и подходящая возможность именно для неё (престиж, польза для карьеры, доступность для 1–2 курса),
 "rf": "да" / "нет" / "проверить" — могут ли участвовать граждане России}}
Если сведений нет — пустая строка. Ничего не выдумывай: бери только то, что есть в тексте.
Ответь строго JSON: {{"items": [ ... ]}}

Записи:
{items}"""

def _call(batch):
    base = (os.getenv("LLM_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
    model = (os.getenv("LLM_MODEL") or "gemini-2.5-flash")
    items = "\n\n".join(f"[{i}] источник: {e.get('source','')} ({e.get('country','')})\nссылка: {e.get('url','')}\nтекст: {e.get('_raw') or (e.get('title','') + '. ' + e.get('desc',''))}"
                        for i, e in enumerate(batch))
    tags = ", ".join(f"{k} = {v['label']}" for k, v in PROFILE["interests"].items())
    body = {"model": model, "temperature": 0.1, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": PROMPT.format(about=PROFILE.get("about", ""), tags=tags, today=today().isoformat(), items=items)}]}
    for attempt in range(3):
        r = requests.post(base + "/chat/completions", json=body, timeout=120,
                          headers={"Authorization": "Bearer " + (os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY"))})
        if r.status_code == 429: time.sleep(20 * (attempt + 1)); continue
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        txt = re.sub(r"^```(?:json)?|```$", "", txt.strip()).strip()
        return {int(x["i"]): x for x in json.loads(txt).get("items", []) if "i" in x}
    return {}

def _apply(e, r):
    if r.get("keep") is False: return None
    for f in ("title", "org", "place", "fmt", "dates", "dl", "cost", "cond", "desc"):
        if not empty(r.get(f)): e[f] = str(r[f]).strip()
    for f in ("start_iso", "dl_iso"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get(f, ""))): e[f] = r[f]
    if r.get("sec") in SECS: e["sec"] = r["sec"]
    if r.get("region") in ("ru", "intl"): e["region"] = r["region"]
    if r.get("rf") in ("да", "нет", "проверить"): e["rf"] = r["rf"]
    if isinstance(r.get("tags"), list): e["tags"] = [t for t in r["tags"] if t in TAGS]
    if r.get("funded") is True: e["funded"] = 1
    if isinstance(r.get("fit"), (int, float)): e["fit"] = max(0, min(10, r["fit"]))
    e["ai"] = 1
    return e

def enrich(events):
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    has_key = bool(os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY"))
    if not has_key: print("ИИ-разбор новых записей выключен: нет LLM_API_KEY (кеш всё равно применяется)")
    limit = int(os.getenv("LLM_MAX_ITEMS", "200")) if has_key else 0
    todo = [e for e in events if e.get("source") not in ("вручную", "подборка") and key(e) not in cache][:limit]
    done = 0
    for i in range(0, len(todo), 8):
        batch = todo[i:i + 8]
        try:
            res = _call(batch)
        except Exception as ex:
            print("  ИИ: ошибка", str(ex)[:120]); break
        for j, e in enumerate(batch):
            if j in res: cache[key(e)] = res[j]; done += 1
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        time.sleep(float(os.getenv("LLM_PAUSE", "5")))  # бесплатный тариф: несколько запросов в минуту
    out = []
    for e in events:
        r = cache.get(key(e))
        if r is not None:
            e = _apply(e, r)
            if e is None: continue
        out.append(e)
    return out, done

# ---------- правила, рейтинг, финальная чистка ----------
def relevance(e):
    text = " ".join(str(e.get(f, "")) for f in ("title", "org", "desc", "cond", "_raw"))
    s = 16 + 12 * min(len(e["tags"]), 3)
    if e.get("top"): s += 15
    if PRESTIGE.search(text): s += 10
    if e.get("funded") or str(e.get("cost", "")).startswith(("бесплат", "оплачива")): s += 8
    if e.get("dl_iso"):
        s += 4
        try:
            days = (dt.date.fromisoformat(e["dl_iso"]) - today()).days
            if 0 <= days <= 30: s += 6
        except ValueError: pass
    if e.get("fit") is not None: s += (e["fit"] - 5) * 4
    if AVOID.search(text): s -= 30
    if not e.get("ok") and not e.get("ai"): s -= 8  # сырые карточки без проверки
    return max(0, min(100, round(s)))

_TR = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "abvgdeejziiklmnoprstufhccss_y_eua")
_GENERIC = set("stazh stazher stazhirovka stipendia stipendiya stipe progr programma konkurs forum summit sammit schoo shkola letnaya summe inter internship scholarship grant granty chempionat champ case keis studen univers moskv rossi sankt peter conference konferenciya award prize premiya fellowship insig spring winter young molod globa inter natio oplachivaemaya novyi nabor trainee karera career start junior yunior camp kemp otkryt otkryv priem zayavki\n".replace("\n", "").split())
def _stems(text):
    words = re.findall(r"[a-zа-яё]{4,}", text.lower())
    return {w.translate(_TR).replace("_", "")[:5] for w in words} - {g[:5] for g in _GENERIC}

def finalize(events, out_path):
    prev = {}
    if out_path.exists():
        try:
            for p in json.loads(out_path.read_text(encoding="utf-8")).get("events", []):
                prev[p.get("id") or key(p)] = p.get("first_seen")
        except Exception: pass
    res, t, seen, by_dl = [], today(), set(), {}
    for e in events:
        nt = re.sub(r"[^a-zа-яё]+", " ", str(e.get("title", "")).lower().replace("ё", "е"))
        nt = " ".join(w for w in nt.split() if w not in ("the", "и", "в", "of", "for", "на", "по", "для"))
        if nt and nt in seen: continue  # дубль после ИИ-переименования (ручные карточки идут первыми и побеждают)
        seen.add(nt)
        # тот же дедлайн + та же рубрика + общее «редкое» слово (Avito Bootcamp = Буткемп Авито) — тоже дубль
        if e.get("dl_iso"):
            k2 = (e.get("sec"), e["dl_iso"]); so = _stems(e.get("org", "")); st = _stems(e.get("title", "")) - so
            if any((so & o or st & o) and (st & t2 or so & t2) and (so & o) for o, t2 in by_dl.get(k2, [])): continue
            by_dl.setdefault(k2, []).append((so | (_stems(e.get("title", "")) & so), st | so))
        e.pop("score", None)
        for f in ("place", "fmt", "dates", "dl", "cost", "cond", "desc", "country"):
            if empty(e.get(f)): e[f] = ""
        if e.get("source") == "вручную" and e.get("url", "").endswith(".json"): continue
        e["sec"] = {"summit": "conf"}.get(e.get("sec"), e.get("sec")) if e.get("sec") in SECS + ("summit",) else "other"
        if e.get("region") not in ("ru", "intl"):
            ctry = str(e.get("country", ""))
            e["region"] = "ru" if ctry == "Россия" or (not ctry and RU_RX.search(e.get("title", "") + " " + e.get("place", ""))) else "intl"
        text = " ".join(str(e.get(f, "")) for f in ("title", "org", "desc", "cond", "_raw"))
        if not e.get("tags"):
            e["tags"] = [k for k, rx in RX.items() if rx.search(text)]
        # прошедшие дедлайны и события убираем
        if e.get("dl_iso"):
            try:
                if dt.date.fromisoformat(e["dl_iso"]) < t: continue
            except ValueError: e["dl_iso"] = ""
        if e.get("start_iso") and not e.get("dl_iso"):
            try:
                if dt.date.fromisoformat(e["start_iso"]) < t - dt.timedelta(days=3): continue
            except ValueError: e["start_iso"] = ""
        e["rel"] = relevance(e)
        e["id"] = key(e)
        e["first_seen"] = prev.get(e["id"]) or t.isoformat()
        e.pop("_raw", None)
        res.append(e)
    res.sort(key=lambda e: (-e["rel"], e.get("dl_iso") or "9999"))
    return res
