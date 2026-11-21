"""
Checks price alerts stored as open GitHub issues titled "ALERT <TICKER> >= <PRICE>" or "<=".
When a price is reached it comments on the issue, mentioning the owner (GitHub emails the
comment), optionally sends an SMTP email too, and closes the issue.

Env: GITHUB_TOKEN, GITHUB_REPOSITORY (both set by Actions);
     optional SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL (same secrets as notify.py).
"""
import json
import os
import re
import smtplib
import urllib.request
from email.mime.text import MIMEText

REPO = os.environ.get("GITHUB_REPOSITORY", "sagivitzhak/Stock-Scanner")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
SITE = "https://sagivitzhak.github.io/Stock-Scanner/"
PAT = re.compile(r"^ALERT\s+([A-Z0-9.\-]+)\s*(>=|<=)\s*([\d.]+)", re.I)


def gh(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r) if r.status != 204 else None


def open_alerts():
    out = []
    for i in gh("GET", "/issues?state=open&per_page=100"):
        if i.get("pull_request") or i.get("author_association") != "OWNER":
            continue
        m = PAT.match(i["title"].strip())
        if m:
            out.append({"n": i["number"], "tk": m.group(1).upper(), "op": m.group(2), "px": float(m.group(3)),
                        "user": i["user"]["login"], "url": i["html_url"], "created": i["created_at"]})
    return out


def prices(tickers):
    import yfinance as yf

    res = {}
    for tk in tickers:
        try:
            t = yf.Ticker(tk.replace(".", "-"))
            h = t.history(period="1d", interval="1m")
            if len(h):
                res[tk] = h
        except Exception as e:
            print(f"{tk}: price failed ({e})")
    return res


def window(h, created):
    """Last price and the high/low since the alert was created (so an earlier spike doesn't fire it)."""
    import pandas as pd

    since = pd.Timestamp(created).tz_convert(h.index.tz) if h.index.tz is not None else pd.Timestamp(created).tz_localize(None)
    w = h[h.index >= since]
    if not len(w):
        w = h.tail(1)
    return {"last": float(h["Close"].iloc[-1]), "hi": float(w["High"].max()), "lo": float(w["Low"].min())}


def send_email(subject, text):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL"))
    if not (host and user and pw and to):
        return False
    m = MIMEText(text, "plain", "utf-8")
    m["Subject"], m["From"], m["To"] = subject, user, to
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(m)
    return True


def main():
    alerts = open_alerts()
    print(f"open alerts: {len(alerts)}")
    if not alerts:
        return
    px = prices(sorted({a["tk"] for a in alerts}))
    for a in alerts:
        h = px.get(a["tk"])
        if h is None:
            continue
        p = window(h, a["created"])
        # intraday high/low catch a touch between checks, not only the last print
        hit = p["hi"] >= a["px"] if a["op"] == ">=" else p["lo"] <= a["px"]
        print(f"{a['tk']} {a['op']} {a['px']}: last {p['last']:.2f} (hi {p['hi']:.2f} lo {p['lo']:.2f}) -> {'HIT' if hit else 'wait'}")
        if not hit:
            continue
        word = "עלתה מעל" if a["op"] == ">=" else "ירדה מתחת ל-"
        text = (f"🔔 @{a['user']} {a['tk']} {word} ${a['px']:g}.\n\n"
                f"מחיר אחרון: ${p['last']:.2f} (טווח היום: ${p['lo']:.2f}–${p['hi']:.2f}).\n\n"
                f"גרף: {SITE}chart.html?tk={a['tk']}\n\n"
                "ההתראה נסגרה. מחירים מ-Yahoo Finance, עשויים להתעכב עד 15 דקות.")
        gh("POST", f"/issues/{a['n']}/comments", {"body": text})
        gh("PATCH", f"/issues/{a['n']}", {"state": "closed", "state_reason": "completed"})
        try:
            send_email(f"התראת מחיר: {a['tk']} {word} ${a['px']:g}", text.replace(f"@{a['user']} ", "") + f"\n\n{a['url']}")
        except Exception as e:
            print(f"email failed: {e}")


if __name__ == "__main__":
    main()
