"""
Breakout scanner for US large caps (S&P 500 + Nasdaq-100), daily and weekly.

A stock is listed when it closes above its prior N-bar high on above-average volume.
Every other indicator adds to a 0-100 score so the strongest setups sort first.

Run:  python scan.py            (downloads prices with yfinance)
Out:  scan.json  +  ohlc/<TICKER>.json for listed stocks that have no chart file yet
"""
import io
import json
import datetime as dt
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
OUT = ROOT / "scan.json"
UNIVERSE_CACHE = ROOT / "universe.json"
OHLC_DIR = ROOT / "ohlc"
UA = {"User-Agent": "Mozilla/5.0 (catalyst-dashboard scanner)"}

MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5e6          # average daily $ volume (20d)
RULES = {
    "d": {"lookback": 20, "vol_avg": 20, "vol_min": 1.3, "trend": 50, "long": 200, "label": "יומי"},
    "w": {"lookback": 20, "vol_avg": 10, "vol_min": 1.3, "trend": 10, "long": 30, "label": "שבועי"},
}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- universe
def wiki_tables(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return pd.read_html(io.StringIO(r.read().decode("utf-8")))


def nasdaq100():
    """[(symbol, name)] from Nasdaq's own list; Wikipedia as a fallback."""
    try:
        req = urllib.request.Request("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                                     headers={**UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rows = json.load(r)["data"]["data"]["rows"]
        out = [(x["symbol"].strip(), x.get("companyName", x["symbol"])) for x in rows if x.get("symbol")]
        if len(out) > 90:
            return out
    except Exception as e:
        log(f"nasdaq.com list failed ({e})")
    try:
        for t in wiki_tables("https://www.slickcharts.com/nasdaq100"):
            if "Symbol" in t.columns and 90 < len(t) < 120:
                nm = "Company" if "Company" in t.columns else "Symbol"
                out = [(str(r["Symbol"]).strip(), str(r[nm]) if str(r[nm]) != "nan" else str(r["Symbol"])) for _, r in t.iterrows()]
                return [x for x in out if x[0] not in ("SPY", "QQQ", "DIA")]
    except Exception as e:
        log(f"slickcharts list failed ({e})")
    try:
        for t in wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100"):
            col = next((c for c in ("Ticker", "Symbol") if c in t.columns), None)
            if col and 90 < len(t) < 120:
                nm = "Company" if "Company" in t.columns else t.columns[0]
                return [(str(r[col]).strip(), str(r[nm])) for _, r in t.iterrows()]
    except Exception as e:
        log(f"wikipedia Nasdaq-100 failed ({e})")
    return []


def load_universe():
    uni = {}
    try:
        for t in wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"):
            if "Symbol" in t.columns and "Security" in t.columns:
                for _, r in t.iterrows():
                    uni[str(r["Symbol"]).strip()] = {"nm": str(r["Security"]), "sec": str(r.get("GICS Sector", "")), "ix": ["S&P 500"]}
                break
    except Exception as e:
        log(f"S&P 500 list failed ({e})")
    for s, nm in nasdaq100():
        if s in uni:
            uni[s]["ix"].append("Nasdaq-100")
        else:
            uni[s] = {"nm": nm, "sec": "", "ix": ["Nasdaq-100"]}
    if len(uni) > 400:
        UNIVERSE_CACHE.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
    elif UNIVERSE_CACHE.exists():
        uni = json.loads(UNIVERSE_CACHE.read_text(encoding="utf-8"))
    log(f"universe: {len(uni)} tickers")
    return uni


# ---------------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def indicators(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    out = pd.DataFrame(index=df.index)
    d = c.diff()
    rs = wilder(d.clip(lower=0), 14) / wilder((-d).clip(lower=0), 14)
    out["rsi"] = 100 - 100 / (1 + rs)
    m = ema(c, 12) - ema(c, 26)
    out["macd"], out["macd_sig"] = m, ema(m, 9)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    up, dn = h.diff(), -l.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    ndm = dn.where((dn > up) & (dn > 0), 0.0)
    pdi, ndi = 100 * wilder(pdm, 14) / atr, 100 * wilder(ndm, 14) / atr
    out["pdi"], out["ndi"] = pdi, ndi
    out["adx"] = wilder(100 * (pdi - ndi).abs() / (pdi + ndi), 14)
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    out["obv"], out["obv_ma"] = obv, obv.rolling(20).mean()
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    out["bb_w"] = 4 * sd / mid
    out["bb_w_min"] = out["bb_w"].rolling(120, min_periods=40).min()
    return out


def evaluate(df, rule, vol_adj=1.0):
    """Returns a dict for the last bar if it is a breakout, else None."""
    df = df.dropna(subset=["Close", "High", "Low"])
    n, L = len(df), rule["lookback"]
    if n < max(L + 2, rule["trend"] + 1, 40):
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    level = h.shift(1).rolling(L).max().iloc[-1]
    px, prev = c.iloc[-1], c.iloc[-2]
    vavg = v.shift(1).rolling(rule["vol_avg"]).mean().iloc[-1]
    vr = v.iloc[-1] * vol_adj / vavg if vavg else 0  # vol_adj pro-rates a week still in progress
    if not (px > level and vr >= rule["vol_min"]):
        return None

    ind = indicators(df).iloc[-1]
    sma_t = c.rolling(rule["trend"]).mean()
    sma_l = c.rolling(rule["long"]).mean()
    st, sl = sma_t.iloc[-1], sma_l.iloc[-1] if n >= rule["long"] else np.nan
    st_rising = sma_t.iloc[-1] > sma_t.iloc[-6] if n > rule["trend"] + 5 else False
    hi_all = h.iloc[-min(n, 252 if rule is RULES["d"] else 52):].max()
    rng = h.iloc[-1] - l.iloc[-1]
    checks = {
        "מגמה: מעל ממוצע " + str(rule["trend"]) + " עולה": bool(px > st and st_rising),
        "ממוצע " + str(rule["trend"]) + " מעל " + str(rule["long"]): bool(not np.isnan(sl) and st > sl),
        "RSI 55–80": bool(55 <= ind["rsi"] <= 80),
        "MACD מעל קו האות ומעל 0": bool(ind["macd"] > ind["macd_sig"] and ind["macd"] > 0),
        "ADX>20 ו-+DI מוביל": bool(ind["adx"] > 20 and ind["pdi"] > ind["ndi"]),
        "OBV מעל ממוצע 20": bool(ind["obv"] > ind["obv_ma"]),
        "סגירה ברבע העליון של הנר": bool(rng > 0 and (px - l.iloc[-1]) / rng >= 0.75),
        "נפח פי 2 ומעלה": bool(vr >= 2),
        "שיא 52 שבועות": bool(h.iloc[-1] >= hi_all),
        "יציאה מכיווץ בולינגר": bool(ind["bb_w_min"] and ind["bb_w"] <= 1.6 * ind["bb_w_min"]),
    }
    score = round(100 * sum(checks.values()) / len(checks))
    rsi = float(ind["rsi"])
    return {
        "px": round(float(px), 2),
        "chg": round(float((px / prev - 1) * 100), 2),
        "lvl": round(float(level), 2),
        "above": round(float((px / level - 1) * 100), 2),
        "vr": round(float(vr), 2),
        "rsi": round(rsi, 1) if not np.isnan(rsi) else None,
        "adx": round(float(ind["adx"]), 1) if not np.isnan(ind["adx"]) else None,
        "score": score,
        "pass": [k for k, ok in checks.items() if ok],
        "fail": [k for k, ok in checks.items() if not ok],
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }


def to_weekly(df):
    """Weekly bars ending Friday, plus how many trading days the last (maybe partial) week has."""
    g = df.resample("W-FRI")
    w = g.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    days = int(g["Close"].count().iloc[-1]) if len(w) else 5
    return w, max(days, 1)


# ---------------------------------------------------------------- data
def download(tickers):
    import yfinance as yf

    frames = {}
    for i in range(0, len(tickers), 100):
        chunk = [t.replace(".", "-") for t in tickers[i:i + 100]]
        try:
            raw = yf.download(chunk, period="3y", interval="1d", group_by="ticker", auto_adjust=True,
                              threads=True, progress=False)
        except Exception as e:
            log(f"  chunk {i}: failed ({e})")
            continue
        for t in chunk:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df.dropna(how="all")
                if len(df):
                    frames[t] = df
            except KeyError:
                pass
        log(f"  downloaded {min(i + 100, len(tickers))}/{len(tickers)}")
    return frames


def write_chart_file(tk, df):
    """Gives a listed stock a chart on chart.html. Leaves files from update_data.py alone."""
    p = OHLC_DIR / f"{tk}.json"
    if p.exists():
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("src") != "scan":
                return
        except Exception:
            pass
    OHLC_DIR.mkdir(exist_ok=True)

    def rows(d):
        return [[ts.strftime("%Y-%m-%d"), round(float(r.Open), 4), round(float(r.High), 4), round(float(r.Low), 4),
                 round(float(r.Close), 4), int(r.Volume or 0)] for ts, r in d.iterrows() if not np.isnan(r.Close)]

    m = df.resample("MS").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    p.write_text(json.dumps({"tk": tk, "src": "scan", "at": dt.date.today().isoformat(), "d": rows(df), "m": rows(m)},
                            separators=(",", ":")), encoding="utf-8")


def main():
    uni = load_universe()
    frames = download(sorted(uni))
    res = {"d": [], "w": []}
    for tk, df in frames.items():
        key = tk.replace("-", ".") if tk.replace("-", ".") in uni else tk
        meta = uni.get(key, {})
        c, v = df["Close"], df["Volume"].fillna(0)
        if len(c) < 60 or c.iloc[-1] < MIN_PRICE or (c * v).tail(20).mean() < MIN_DOLLAR_VOL:
            continue
        wk, days = to_weekly(df)
        for iv, data, adj in (("d", df, 1.0), ("w", wk, 5 / days)):
            try:
                r = evaluate(data, RULES[iv], adj)
            except Exception as e:
                log(f"  {tk} {iv}: {e}")
                r = None
            if r:
                r.update({"tk": tk, "nm": meta.get("nm", tk), "sec": meta.get("sec", ""), "ix": meta.get("ix", [])})
                res[iv].append(r)
    for iv in res:
        res[iv].sort(key=lambda x: (-x["score"], -x["vr"]))
    for r in res["d"] + res["w"]:
        write_chart_file(r["tk"], frames[r["tk"]])
    out = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "universe": len(uni),
           "scanned": len(frames), "rules": RULES, "d": res["d"], "w": res["w"]}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    log(f"breakouts: daily {len(res['d'])}, weekly {len(res['w'])} (of {len(frames)} scanned)")


if __name__ == "__main__":
    main()
