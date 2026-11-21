"""
Builds data.js for the catalyst dashboard.

Sources:
  catalysts.json  - hand-written events and analysis (source of truth)
  yfinance        - next earnings date, estimates, price, valuation, analyst targets
  FOMC schedule   - federalreserve.gov (hard-coded below, update once a year)
  openFDA         - checks whether an FDA event that already passed shows an approval

Run:  python update_data.py            (live)
      python update_data.py --offline  (no network, rebuild from catalysts.json only)
"""
import json
import sys
import datetime as dt
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "catalysts.json"
OUT = ROOT / "data.js"
OFFLINE = "--offline" in sys.argv
KEEP_PAST_DAYS = 21      # passed events stay visible (faded) this long
HORIZON_DAYS = 75        # auto-added earnings up to this far ahead

# FOMC decision days (second day of each meeting). Source: federalreserve.gov,
# 2027 schedule as revised on 12.11.2026. October 2027 is strategy-only, no decision.
FOMC = [
    ("2026-12-09", "החלטת ריבית (8–9.12)"),
    ("2027-01-27", "החלטת ריבית (26–27.1)"),
    ("2027-03-17", "החלטת ריבית (16–17.3)"),
    ("2027-04-28", "החלטת ריבית (27–28.4)"),
    ("2027-06-09", "החלטת ריבית (8–9.6)"),
    ("2027-07-28", "החלטת ריבית (27–28.7)"),
    ("2027-09-15", "החלטת ריבית (14–15.9)"),
    ("2027-11-17", "החלטת ריבית (16–17.11)"),
]

NOW = dt.datetime.now(dt.timezone.utc)


def log(*a):
    print(*a, flush=True)


def parse_date(s):
    if len(s) <= 10:
        s += "T12:00:00Z"
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_previous():
    """Live values from the last successful run, so a failed fetch never blanks a card."""
    if not OUT.exists():
        return {}
    txt = OUT.read_text(encoding="utf-8")
    try:
        data = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        return data.get("live", {})
    except Exception:
        return {}


def fmt_money(v):
    if v is None:
        return None
    a = abs(v)
    if a >= 1e12:
        return f"${v/1e12:.2f} טריליון"
    if a >= 1e9:
        return f"${v/1e9:.1f} מיליארד"
    if a >= 1e6:
        return f"${v/1e6:.0f} מיליון"
    return f"${v:,.2f}"


def num(v, nd=2):
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return round(f, nd)
    except (TypeError, ValueError):
        return None


def fetch_yf(tickers):
    import yfinance as yf

    live = {}
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
            info = t.info or {}
            cal = t.calendar or {}
            ed = cal.get("Earnings Date") or []
            ed = [d for d in ed if d] if isinstance(ed, list) else [ed]
            nxt = None
            for d in sorted(ed):
                d = dt.datetime.combine(d, dt.time(12)) if isinstance(d, dt.date) and not isinstance(d, dt.datetime) else d
                d = d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d
                if d > NOW - dt.timedelta(days=1):
                    nxt = d
                    break
            price = num(info.get("currentPrice") or info.get("regularMarketPrice"))
            target = num(info.get("targetMeanPrice"))
            live[tk] = {
                "name": info.get("shortName") or info.get("longName") or tk,
                "next": nxt.strftime("%Y-%m-%d") if nxt else None,
                "eps": num(cal.get("Earnings Average")),
                "epsLo": num(cal.get("Earnings Low")),
                "epsHi": num(cal.get("Earnings High")),
                "rev": num(cal.get("Revenue Average"), 0),
                "price": price,
                "mcap": num(info.get("marketCap"), 0),
                "pe": num(info.get("trailingPE"), 1),
                "fpe": num(info.get("forwardPE"), 1),
                "target": target,
                "upside": round((target / price - 1) * 100, 1) if price and target else None,
                "rating": info.get("recommendationKey"),
                "analysts": info.get("numberOfAnalystOpinions"),
                "chg52": num((info.get("52WeekChange") or 0) * 100, 1) if info.get("52WeekChange") is not None else None,
                "at": NOW.strftime("%Y-%m-%dT%H:%MZ"),
            }
            log(f"  {tk}: next={live[tk]['next']} price={price}")
        except Exception as e:  # Yahoo blocks or changes fields from time to time
            log(f"  {tk}: failed ({e.__class__.__name__}: {e})")
    return live


def fda_approved(drug):
    """openFDA lists approvals after they happen (Drugs@FDA). It has no future PDUFA dates,
    and CBER biologics (cell and gene therapies) are often missing, so 'not found' means unknown."""
    q = f'openfda.generic_name:"{drug}"+AND+submissions.submission_status:"AP"'
    url = "https://api.fda.gov/drug/drugsfda.json?search=" + urllib.parse.quote(q, safe=':+"') + "&limit=1"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return bool(json.load(r).get("results"))
    except Exception:
        return False


def live_rows(L):
    rows = []
    if L.get("price") is not None:
        rows.append(["מחיר", f"${L['price']:,.2f}"])
    if L.get("mcap"):
        rows.append(["שווי שוק", fmt_money(L["mcap"])])
    pe = " / ".join(x for x in [f"{L['pe']}" if L.get("pe") else None, f"{L['fpe']} צפוי" if L.get("fpe") else None] if x)
    if pe:
        rows.append(["מכפיל רווח", pe])
    if L.get("target"):
        up = f" ({L['upside']:+.1f}%)" if L.get("upside") is not None else ""
        rows.append(["יעד אנליסטים ממוצע", f"${L['target']:,.2f}{up}"])
    if L.get("rating"):
        n = f", {L['analysts']} אנליסטים" if L.get("analysts") else ""
        rows.append(["המלצה", f"{L['rating']}{n}"])
    if L.get("chg52") is not None:
        rows.append(["שינוי ב-52 שבועות", f"{L['chg52']:+.1f}%"])
    return rows


def exp_text(L):
    parts = []
    if L.get("eps") is not None:
        rng = f" (טווח {L['epsLo']}–{L['epsHi']})" if L.get("epsLo") is not None and L.get("epsHi") is not None else ""
        parts.append(f"EPS ${L['eps']}{rng}")
    if L.get("rev"):
        parts.append(f"הכנסות {fmt_money(L['rev'])}")
    return ", ".join(parts) or "אין עדיין קונצנזוס"


def build():
    src = json.loads(SRC.read_text(encoding="utf-8"))
    events = src["events"]
    extra = src.get("extra", [])
    watch = sorted(set(src.get("watchlist", [])) | {e["tk"] for e in events if e["t"] == "e"})

    prev = load_previous()
    live = dict(prev)
    if not OFFLINE:
        log(f"yfinance: {len(watch)} tickers")
        try:
            live.update(fetch_yf(watch))
        except ImportError:
            log("yfinance not installed, keeping previous live data")

    cutoff = NOW - dt.timedelta(days=KEEP_PAST_DAYS)
    horizon = NOW + dt.timedelta(days=HORIZON_DAYS)
    out = []

    for e in events:
        e = dict(e)
        d = parse_date(e["d"])
        if d < cutoff:
            continue
        L = live.get(e["tk"])
        if L:
            e["live"] = live_rows(L)
            e["liveAt"] = L.get("at")
        if e["t"] == "f" and d < NOW and e.get("drug") and not OFFLINE and not e.get("status"):
            if fda_approved(e["drug"]):
                e["status"] = "אושר לפי openFDA"
        out.append(e)

    have = {(e["tk"], e["t"]) for e in out if parse_date(e["d"]) > NOW}
    for tk in watch:
        L = live.get(tk) or {}
        if not L.get("next") or (tk, "e") in have:
            continue
        d = parse_date(L["next"])
        if not (NOW - dt.timedelta(days=1) < d < horizon):
            continue
        out.append({
            "id": tk.lower() + "-" + L["next"].replace("-", ""),
            "t": "e", "tk": tk, "nm": L.get("name", tk), "d": L["next"],
            "tm": d.strftime("%-d.%-m") + " (תאריך מ-Yahoo Finance, השעה לא ידועה)",
            "ev": "דוח רבעוני. נוסף אוטומטית מרשימת המעקב.",
            "exp": exp_text(L), "risk": 2, "riskTx": "לא נותח", "auto": True,
            "m": [], "b": [],
            "s": [["Yahoo Finance", f"https://finance.yahoo.com/quote/{tk}/analysis"]],
            "live": live_rows(L), "liveAt": L.get("at"),
        })

    macro = [{"tk": "FOMC", "t": "m", "d": d, "nm": n} for d, n in FOMC if cutoff < parse_date(d) < horizon]
    carded = {(e["tk"], e["d"][:10]) for e in out}
    extra = [x for x in extra if cutoff < parse_date(x["d"]) and (x["tk"], x["d"][:10]) not in carded] + macro

    data = {"updated": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "events": out, "extra": extra, "live": live}
    OUT.write_text("window.DATA=" + json.dumps(data, ensure_ascii=False, indent=0) + ";\n", encoding="utf-8")
    log(f"wrote {OUT.name}: {len(out)} events, {len(extra)} calendar-only")


if __name__ == "__main__":
    build()
