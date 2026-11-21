"""
Morning alert for tickers listed under "alerts" in catalysts.json.
Sends events of today and tomorrow (Israel time) to Telegram and/or email.

GitHub secrets (Settings > Secrets and variables > Actions):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID           -> Telegram
  SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL   -> email (e.g. smtp.gmail.com + Gmail app password)
Either channel is optional; with neither set the script only prints the message.
"""
import json
import os
import smtplib
import urllib.parse
import urllib.request
import datetime as dt
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
IL = ZoneInfo("Asia/Jerusalem")
SITE = "https://sagivitzhak.github.io/Stock-Scanner/"


def load():
    txt = (ROOT / "data.js").read_text(encoding="utf-8")
    data = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
    src = json.loads((ROOT / "catalysts.json").read_text(encoding="utf-8"))
    return data, {t.upper() for t in src.get("alerts", [])}


def il_date(s):
    if len(s) <= 10:
        return dt.date.fromisoformat(s)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(IL).date()


def build_message():
    data, alerts = load()
    if not alerts:
        return None
    today = dt.datetime.now(IL).date()
    tomorrow = today + dt.timedelta(days=1)
    items = [e for e in data["events"] + data.get("extra", []) if e["tk"].upper() in alerts and il_date(e["d"]) in (today, tomorrow)]
    if not items:
        return None
    lines = [f"📅 קטליזטורים ל-{today.strftime('%d.%m')}–{tomorrow.strftime('%d.%m')}"]
    for when in (today, tomorrow):
        day = [e for e in items if il_date(e["d"]) == when]
        if not day:
            continue
        lines.append("")
        lines.append("היום:" if when == today else "מחר:")
        for e in sorted(day, key=lambda x: x["d"]):
            kind = {"e": "דוח", "f": "FDA", "m": "מאקרו"}.get(e["t"], "")
            lines.append(f"• {e['tk']} ({kind}) – {e.get('tm') or e.get('nm', '')}")
            if e.get("exp"):
                lines.append(f"  מה מצפים: {e['exp']}")
            if e.get("em"):
                lines.append(f"  תנועה צפויה באופציות: ±{e['em']['pct']}% (פקיעה {e['em']['exp']})")
    lines += ["", SITE]
    return "\n".join(lines)


def send_telegram(msg):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    body = urllib.parse.urlencode({"chat_id": chat, "text": msg, "disable_web_page_preview": "true"}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=body, timeout=20) as r:
        return json.load(r).get("ok", False)


def send_email(msg):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL"))
    if not (host and user and pw and to):
        return False
    m = MIMEText(msg, "plain", "utf-8")
    m["Subject"] = "קטליזטורים להיום ומחר"
    m["From"], m["To"] = user, to
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(m)
    return True


if __name__ == "__main__":
    msg = build_message()
    if not msg:
        print("Nothing to send today.")
    else:
        print(msg)
        print("telegram:", send_telegram(msg), "| email:", send_email(msg))
