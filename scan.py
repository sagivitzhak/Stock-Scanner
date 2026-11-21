"""
Scanner for all US-listed common stocks (Nasdaq, NYSE, NYSE American), daily and weekly.

Two scans on the same data:
  breakout  - close above the prior N-bar high on above-average volume.
  reversal  - a falling trend that just turned up: price crossed above a moving average
              that was still declining, confirmed by momentum, structure and volume.
Every other indicator adds to a 0-100 score so the strongest setups sort first.

Run:  python scan.py            (downloads prices with yfinance)
Out:  scan.json  +  ohlc/<TICKER>.json for listed stocks that have no chart file yet
"""
import io
import json
import re
import datetime as dt
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
OUT = ROOT / "scan.json"
UNIVERSE_CACHE = ROOT / "universe.json"
OHLC_DIR = ROOT / "ohlc"
CHART_FILES_PER_LIST = 60     # chart files written for the top names of each list
UA = {"User-Agent": "Mozilla/5.0 (catalyst-dashboard scanner)"}

MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5e6          # average daily $ volume (20d)
RULES = {
    "d": {"lookback": 20, "vol_avg": 20, "vol_min": 1.3, "trend": 50, "long": 200, "label": "יומי"},
    "w": {"lookback": 20, "vol_avg": 10, "vol_min": 1.3, "trend": 10, "long": 30, "label": "שבועי"},
}
# reversal: ma = trend average the price must cross; slope_lb/lag = how we prove it was falling
REV = {
    "d": {"ma": 50, "fast": 20, "long": 200, "slope_lb": 20, "lag": 10, "recent": 3, "swing": 10, "vol_avg": 20},
    "w": {"ma": 30, "fast": 10, "long": 40, "slope_lb": 10, "lag": 5, "recent": 2, "swing": 6, "vol_avg": 10},
}
SKIP_NAME = re.compile(r"warrant|\bunits?\b|\brights?\b|preferred|\bnotes?\b|debenture|%|acquisition corp|\bspac\b"
                       r"|\betns?\b|leveraged|inverse|\bfund\b|\bindex\b|\bdue \d{4}", re.I)


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


def clean_name(nm):
    adr = "depositary" in nm.lower()
    nm = re.split(r" - | American Depositary| Ordinary Shares| Common Stock| Common Shares| Class [A-C] ", nm)[0]
    return (nm.strip(" ,.") + (" (ADR)" if adr else "")).strip()


def us_listed():
    """All US-listed common stocks from Nasdaq Trader's symbol directory (ETFs, tests, warrants etc. removed)."""
    out = {}
    for url, sym_col, etf_col, test_col in (
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol", "ETF", "Test Issue"),
        ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol", "ETF", "Test Issue"),
    ):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                df = pd.read_csv(io.StringIO(r.read().decode("utf-8", "replace")), sep="|", dtype=str)
            df = df[df[sym_col].notna() & ~df[sym_col].str.startswith("File Creation")]
            df = df[(df[etf_col] == "N") & (df[test_col] == "N")]
            if "Financial Status" in df.columns:
                df = df[df["Financial Status"].fillna("N").isin(["N", ""])]
            for _, r in df.iterrows():
                sym, nm = r[sym_col].strip(), str(r["Security Name"])
                if "$" in sym or SKIP_NAME.search(nm) or len(sym) > 6:
                    continue
                out[sym] = clean_name(nm)
        except Exception as e:
            log(f"symbol directory failed: {url} ({e})")
    return out


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
    for s, nm in us_listed().items():
        uni.setdefault(s, {"nm": nm, "sec": "", "ix": []})
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


def evaluate_reversal(df, rule, vol_adj=1.0):
    """Last bars turned a falling trend up. Returns a dict, or None."""
    df = df.dropna(subset=["Close", "High", "Low"])
    n, M = len(df), rule["ma"]
    if n < M + rule["slope_lb"] + rule["lag"] + 5:
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"].fillna(0)
    ma = c.rolling(M).mean()
    lag, lb, k = rule["lag"], rule["slope_lb"], rule["recent"]
    # 1) the average was falling before the turn
    was_falling = ma.iloc[-1 - lag] < ma.iloc[-1 - lag - lb]
    # 2) price crossed above it within the last k bars and is still above
    cross_at = None
    for j in range(k):
        i = -1 - j
        if c.iloc[i] > ma.iloc[i] and c.iloc[i - 1] <= ma.iloc[i - 1]:
            cross_at = j
            break
    if not (was_falling and cross_at is not None and c.iloc[-1] > ma.iloc[-1]):
        return None

    ind = indicators(df)
    last = ind.iloc[-1]
    fast = ema(c, rule["fast"])
    longma = c.rolling(rule["long"]).mean()
    sw = rule["swing"]
    vavg = v.shift(1).rolling(rule["vol_avg"]).mean()
    xi = -1 - cross_at
    vr = (v.iloc[xi] * (vol_adj if cross_at == 0 else 1)) / vavg.iloc[xi] if vavg.iloc[xi] else 0
    macd_x = any(ind["macd"].iloc[i] > ind["macd_sig"].iloc[i] and ind["macd"].iloc[i - 1] <= ind["macd_sig"].iloc[i - 1]
                 for i in range(-5, 0))
    rsi_x = last["rsi"] > 50 and ind["rsi"].iloc[-6:-1].min() < 50
    di_x = last["pdi"] > last["ndi"] and (ind["pdi"].iloc[-6:-1] <= ind["ndi"].iloc[-6:-1]).any()
    checks = {
        "MACD חצה את קו האות (5 נרות אחרונים)": bool(macd_x),
        "RSI עלה מעל 50": bool(rsi_x),
        "+DI חצה מעל -DI": bool(di_x),
        "שפל עולה (מבנה מחירים)": bool(l.iloc[-sw:].min() > l.iloc[-3 * sw:-sw].min()),
        "פריצת השיא המקומי האחרון": bool(c.iloc[-1] > h.iloc[-sw - 1:-1].max()),
        "ממוצע מהיר מעל הממוצע המרכזי": bool(fast.iloc[-1] > ma.iloc[-1]),
        "הממוצע המרכזי כבר מתיישר": bool(ma.iloc[-1] >= ma.iloc[-4]),
        "נפח פי 1.2+ ביום החצייה": bool(vr >= 1.2),
        "OBV מעל ממוצע 20": bool(last["obv"] > last["obv_ma"]),
        "מעל ממוצע ארוך (" + str(rule["long"]) + ")": bool(not np.isnan(longma.iloc[-1]) and c.iloc[-1] > longma.iloc[-1]),
    }
    score = round(100 * sum(checks.values()) / len(checks))
    px = float(c.iloc[-1])
    hi52 = float(h.iloc[-min(n, 252 if rule is REV["d"] else 52):].max())
    rsi = float(last["rsi"])
    return {
        "px": round(px, 2),
        "chg": round(float((px / c.iloc[-2] - 1) * 100), 2),
        "ma": round(float(ma.iloc[-1]), 2),
        "above": round(float((px / ma.iloc[-1] - 1) * 100), 2),
        "bars": int(cross_at),
        "dd": round((px / hi52 - 1) * 100, 1),
        "vr": round(float(vr), 2),
        "rsi": round(rsi, 1) if not np.isnan(rsi) else None,
        "adx": round(float(last["adx"]), 1) if not np.isnan(last["adx"]) else None,
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
def download_chunks(tickers, size=100):
    """Yields {ticker: DataFrame} per chunk so the whole market never sits in memory at once."""
    import yfinance as yf

    for i in range(0, len(tickers), size):
        chunk = [t.replace(".", "-") for t in tickers[i:i + size]]
        frames = {}
        try:
            raw = yf.download(chunk, period="2y", interval="1d", group_by="ticker", auto_adjust=True,
                              threads=True, progress=False)
            for t in chunk:
                try:
                    df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                    df = df.dropna(how="all")
                    if len(df):
                        frames[t] = df
                except KeyError:
                    pass
        except Exception as e:
            log(f"  chunk {i}: failed ({e})")
        log(f"  downloaded {min(i + size, len(tickers))}/{len(tickers)}")
        yield frames


def chart_rows(df):
    return [[ts.strftime("%Y-%m-%d"), round(float(r.Open), 4), round(float(r.High), 4), round(float(r.Low), 4),
             round(float(r.Close), 4), int(r.Volume or 0)] for ts, r in df.iterrows() if not np.isnan(r.Close)]


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
    m = df.resample("MS").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
    p.write_text(json.dumps({"tk": tk, "src": "scan", "at": dt.date.today().isoformat(), "d": chart_rows(df), "m": chart_rows(m)},
                            separators=(",", ":")), encoding="utf-8")


def prune_chart_files(keep):
    """Removes chart files this scanner wrote for stocks that are no longer listed."""
    if not OHLC_DIR.exists():
        return
    for p in OHLC_DIR.glob("*.json"):
        if p.stem in keep:
            continue
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("src") == "scan":
                p.unlink()
        except Exception:
            pass


def main():
    uni = load_universe()
    res = {"d": [], "w": [], "rd": [], "rw": []}
    keep_frames, scanned, liquid = {}, 0, 0
    for frames in download_chunks(sorted(uni)):
        for tk, df in frames.items():
            scanned += 1
            key = tk.replace("-", ".") if tk.replace("-", ".") in uni else tk
            meta = uni.get(key, {})
            c, v = df["Close"], df["Volume"].fillna(0)
            if len(c) < 60 or c.iloc[-1] < MIN_PRICE or (c * v).tail(20).mean() < MIN_DOLLAR_VOL:
                continue
            liquid += 1
            wk, days = to_weekly(df)
            jobs = (("d", evaluate, df, RULES["d"], 1.0), ("w", evaluate, wk, RULES["w"], 5 / days),
                    ("rd", evaluate_reversal, df, REV["d"], 1.0), ("rw", evaluate_reversal, wk, REV["w"], 5 / days))
            hit = False
            for key_, fn, data, rule, adj in jobs:
                try:
                    r = fn(data, rule, adj)
                except Exception as e:
                    log(f"  {tk} {key_}: {e}")
                    r = None
                if r:
                    r.update({"tk": tk, "nm": meta.get("nm", tk), "sec": meta.get("sec", ""), "ix": meta.get("ix", [])})
                    res[key_].append(r)
                    hit = True
            if hit:
                keep_frames[tk] = df
    for k in res:
        res[k].sort(key=lambda x: (-x["score"], -x["vr"]))
    charted = set()
    for k in res:
        for r in res[k][:CHART_FILES_PER_LIST]:
            if r["tk"] not in charted:
                write_chart_file(r["tk"], keep_frames[r["tk"]])
                charted.add(r["tk"])
    prune_chart_files(charted)
    out = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "universe": len(uni),
           "scanned": scanned, "liquid": liquid, "rules": RULES, "rev": REV, "charted": sorted(charted), **res}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    log(f"universe {len(uni)}, scanned {scanned}, liquid {liquid}; breakouts d{len(res['d'])} w{len(res['w'])}; "
        f"reversals d{len(res['rd'])} w{len(res['rw'])}")


if __name__ == "__main__":
    main()
