"""Утренняя сводка в Telegram (по желанию).
Нужны секреты TELEGRAM_BOT_TOKEN (создай бота у @BotFather) и TELEGRAM_CHAT_ID (свой id узнай у @userinfobot,
потом напиши своему боту /start). Без них скрипт молча ничего не делает."""
import json, os, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).parent
SITE = os.getenv("SITE_URL", "")
SEC = {"conf": "Конференция", "grant": "Стипендия", "case": "Кейс", "intern": "Стажировка", "school": "Школа", "other": "Возможность"}

def main():
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat): print("Telegram не настроен, пропускаю"); return
    data = json.loads((ROOT / "docs" / "events.json").read_text(encoding="utf-8"))
    ev, today = data["events"], dt.date.today()
    new = [e for e in ev if e.get("first_seen") == today.isoformat() and e.get("rel", 0) >= int(os.getenv("NOTIFY_MIN_REL", "55"))]
    soon = [e for e in ev if e.get("dl_iso") and (dt.date.fromisoformat(e["dl_iso"]) - today).days in (1, 3, 7)]
    if not new and not soon: print("Нечего присылать"); return
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [f"<b>Радар будущего · {today.strftime('%d.%m')}</b>"]
    if soon:
        lines.append("\n⏰ <b>Скоро дедлайн</b>")
        for e in sorted(soon, key=lambda e: e["dl_iso"]):
            d = (dt.date.fromisoformat(e["dl_iso"]) - today).days
            lines.append(f"• через {d} дн.: <a href=\"{e['url']}\">{esc(e['title'])}</a>")
    if new:
        lines.append(f"\n✨ <b>Новое ({len(new)})</b>")
        for e in sorted(new, key=lambda e: -e.get("rel", 0))[:12]:
            dl = f" · до {e['dl']}" if e.get("dl") else ""
            lines.append(f"• {SEC.get(e['sec'], '')}: <a href=\"{e['url']}\">{esc(e['title'])}</a> — {e.get('rel', 0)}%{esc(dl)}")
    if SITE: lines.append(f"\n<a href=\"{SITE}\">Открыть радар</a>")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": "\n".join(lines)[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    print("Telegram:", r.status_code)

if __name__ == "__main__":
    main()
