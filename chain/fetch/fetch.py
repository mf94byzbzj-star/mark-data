#!/usr/bin/env python3
"""Datenbeschaffung der Agentenkette (läuft in GitHub Actions mit freiem Internet).

Modi:
  daily     Kurse, Vola-Indizes, FRED-Makro, Earnings, Cboe-Live-Ketten (nach US-Schluss)
  dolt      DoltHub-Historie + neue Tage (Optionen + Vola-Historie), mit Zeitbudget, setzt automatisch fort

Ablage (alles unter chain_data/):
  prices/<T>.csv                 Tageskurse (yfinance, adjustiert)
  macro/fred.csv                 Makroreihen (FRED)
  earnings/<T>.csv               Earnings-Termine
  surface/<T>.csv.gz             verdichtete Vola-Oberfläche je Datum/Verfall (DoltHub, ab 2019)
  volhist/<JAHR>.csv.gz          IV/HV aller ~1.500 DoltHub-Symbole (für IV-Rank-Screening)
  live/<DATUM>/<T>.csv.gz        komplette Kette mit OI/Greeks (Cboe, 15 min verzögert)
  live/<DATUM>/_summary.csv      Spot, IV30 je Ticker
"""
import concurrent.futures as cf
import datetime as dt
import io
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import requests
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from chain.core.surface import compact, COLS  # noqa: E402

D = os.path.join(ROOT, "chain_data")
CFG = yaml.safe_load(open(os.path.join(ROOT, "chain", "config.yaml")))
UNIV = CFG["universe"]["etfs"] + CFG["universe"]["stocks"]
DOLT = "https://www.dolthub.com/api/v1alpha1/post-no-preference/options/master"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/130 Safari/537.36",
      "Accept": "application/json", "Referer": "https://www.cboe.com/"}
VOLIDX = ["^VIX", "^VIX9D", "^VIX3M", "^VVIX", "^SKEW", "^VXN", "^MOVE"]
FRED = ["DGS10", "DGS2", "DGS3MO", "T10Y2Y", "BAMLH0A0HYM2", "BAMLC0A0CM", "DFF", "NFCI", "DTWEXBGS", "DCOILWTICO"]
T0 = time.time()
LOG = []
MODE = sys.argv[1] if len(sys.argv) > 1 else "daily"
INTRADAY = MODE == "intraday"


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def safe(sym):
    return sym.replace("^", "IDX_")


# ---------------------------------------------------------------- git (Zwischenstände sichern)
def commit(msg):
    os.makedirs(os.path.join(D, "logs"), exist_ok=True)
    open(os.path.join(D, "logs", f"fetch_{MODE}.log"), "w").write("\n".join(LOG[-400:]))
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    subprocess.run("git add chain_data", shell=True)
    r = subprocess.run("git diff --cached --quiet", shell=True)
    if r.returncode == 0:
        return
    subprocess.run(f'git commit -q -m "{msg}"', shell=True)
    for _ in range(3):
        if subprocess.run("git pull --rebase -q -X theirs && git push -q", shell=True).returncode == 0:
            return
        time.sleep(5)


# ---------------------------------------------------------------- yfinance / FRED
def fetch_prices():
    import yfinance as yf
    os.makedirs(os.path.join(D, "prices"), exist_ok=True)
    errs = []
    for s in UNIV + VOLIDX:
        try:
            df = yf.Ticker(s).history(period="max", auto_adjust=True)
            if df.empty:
                raise RuntimeError("leer")
            df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
            df = df[df.index >= "2005-01-01"]
            df[["Open", "High", "Low", "Close", "Volume"]].round(4).to_csv(
                os.path.join(D, "prices", f"{safe(s)}.csv"), index_label="Date")
        except Exception as e:
            errs.append(f"{s}: {e}")
    log("prices fehler:", errs or "keine")


def fetch_earnings():
    import yfinance as yf
    os.makedirs(os.path.join(D, "earnings"), exist_ok=True)
    for t in CFG["universe"]["stocks"]:
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=40)
            if df is not None and not df.empty:
                p = os.path.join(D, "earnings", f"{t}.csv")
                old = pd.read_csv(p, index_col=0) if os.path.exists(p) else None
                df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M")
                if old is not None:
                    df = pd.concat([old, df])
                    df = df[~df.index.duplicated(keep="last")]
                df.sort_index().to_csv(p, index_label="earnings_dt")
        except Exception as e:
            log("earnings", t, e)


def fetch_fred():
    os.makedirs(os.path.join(D, "macro"), exist_ok=True)
    frames = []
    for sid in FRED:
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=60)
            df = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True, na_values=".")
            frames.append(df.iloc[:, 0].rename(sid))
        except Exception as e:
            log("fred", sid, e)
    if frames:
        m = pd.concat(frames, axis=1)
        m = m[m.index >= "2000-01-01"]
        m.to_csv(os.path.join(D, "macro", "fred.csv"), index_label="date")


# ---------------------------------------------------------------- Cboe live
def _cboe_one(t):
    sym = t if t not in ("SPX", "VIX", "NDX", "RUT") else "_" + t
    j = None
    for i in range(5):
        try:
            time.sleep(0.4)
            r = requests.get(f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json", headers=UA, timeout=60)
            if r.status_code == 200:
                j = r.json()["data"]
                break
        except Exception:
            pass
        time.sleep(4 * (i + 1))
    if j is None:
        raise RuntimeError(f"{t}: cboe {r.status_code}")
    o = pd.DataFrame(j["options"])
    m = o["option"].str.extract(r"^([A-Z0-9]+?)(\d{6})([CP])(\d{8})$")
    o["root"], o["expiry"], o["cp"] = m[0], pd.to_datetime(m[1], format="%y%m%d").dt.strftime("%Y-%m-%d"), m[2]
    o["strike"] = m[3].astype(float) / 1000
    o = o[o["root"].isin([t, t + "W", "SPXW" if t == "SPX" else t])]
    spot = float(j.get("current_price") if INTRADAY else (j.get("close") or j.get("current_price")))
    dte = (pd.to_datetime(o["expiry"]) - pd.Timestamp.today().normalize()).dt.days
    o = o[(dte >= 0) & (dte <= 200) & (o.strike > spot * 0.6) & (o.strike < spot * 1.4)]
    cols = ["expiry", "cp", "strike", "bid", "ask", "iv", "delta", "gamma", "vega", "theta", "open_interest", "volume"]
    o = o[cols].sort_values(["expiry", "strike", "cp"])
    summ = {"ticker": t, "spot": spot, "iv30": j.get("iv30"), "close": j.get("close"), "prev_close": j.get("prev_day_close"),
            "volume": j.get("volume"), "last_trade_time": j.get("last_trade_time"),
            "opt_volume": float(o["volume"].sum()), "open_interest": float(o["open_interest"].sum())}
    return t, o, summ


def fetch_intraday():
    """Untertägiger Schnappschuss (für den Positionswächter): Ketten des Universums + Indizes nach chain_data/intraday/latest."""
    res, errs = [], []
    with cf.ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(_cboe_one, t) for t in UNIV + ["SPX", "VIX"]]
        for f in cf.as_completed(futs):
            try:
                res.append(f.result())
            except Exception as e:
                errs.append(str(e)[:120])
    path = os.path.join(D, "intraday", "latest")
    if os.path.isdir(path):
        for f in os.listdir(path):
            os.remove(os.path.join(path, f))
    os.makedirs(path, exist_ok=True)
    summ = []
    for t, o, s in res:
        o.to_csv(os.path.join(path, f"{t}.csv.gz"), index=False, compression="gzip")
        s["spot"] = s.get("spot")
        summ.append(s)
    pd.DataFrame(summ).sort_values("ticker").to_csv(os.path.join(path, "_summary.csv"), index=False)
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M ET")
    open(os.path.join(path, "_stamp.txt"), "w").write(stamp)
    log(f"intraday {stamp}: {len(res)} ok, fehler {len(errs)}")


def fetch_cboe():
    res, errs = [], []
    with cf.ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(_cboe_one, t) for t in UNIV + ["SPX", "VIX"]]
        for f in cf.as_completed(futs):
            try:
                res.append(f.result())
            except Exception as e:
                errs.append(str(e)[:120])
    if not res:
        log("cboe: keine Daten", errs[:5])
        return
    # Handelstag = Datum des letzten Trades (nicht das Kalenderdatum des Laufs)
    day = max(str(s.get("last_trade_time") or "")[:10] for _, _, s in res)
    path = os.path.join(D, "live", day)
    os.makedirs(path, exist_ok=True)
    summ = []
    for t, o, s in res:
        o.to_csv(os.path.join(path, f"{t}.csv.gz"), index=False, compression="gzip")
        summ.append(s)
    pd.DataFrame(summ).sort_values("ticker").to_csv(os.path.join(path, "_summary.csv"), index=False)
    log(f"cboe {day}: {len(summ)} ok, fehler {len(errs)}", errs[:5])
    # nur die letzten 30 Tage vollständige Ketten behalten, ältere auf GEX-relevante Spalten kürzen
    days = sorted(os.listdir(os.path.join(D, "live")))
    for old in days[:-30]:
        p = os.path.join(D, "live", old)
        for f in os.listdir(p):
            if f.endswith(".csv.gz") and not f.startswith("_"):
                df = pd.read_csv(os.path.join(p, f))
                if "theta" in df.columns:
                    df = df[["expiry", "cp", "strike", "iv", "gamma", "open_interest"]]
                    df = df[df.open_interest > 0]
                    df.to_csv(os.path.join(p, f), index=False, compression="gzip")


# ---------------------------------------------------------------- DoltHub
def dolt(q, retries=4, timeout=90):
    err = None
    for i in range(retries):
        try:
            r = requests.get(DOLT, params={"q": q}, timeout=timeout)
            j = r.json()
            st = j.get("query_execution_status")
            if st in ("Success", "RowLimit"):
                return j.get("rows", []), st
            err = j.get("query_execution_message")
        except Exception as e:
            err = str(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"dolt: {err} | {q[:120]}")


def dolt_dates(days=14):
    """Neue DoltHub-Tage der letzten `days` Kalendertage (Mo–Sa). Die Historie kommt aus dem Klon (Modus clone);
    die API ist für Bereichsabfragen zu langsam und wird nur für die täglichen Ergänzungen genutzt."""
    today = dt.date.today()
    cands = [(today - dt.timedelta(days=k)).isoformat() for k in range(days) if (today - dt.timedelta(days=k)).weekday() != 6]

    def has(ds):
        try:
            rows, _ = dolt(f"SELECT strike FROM option_chain WHERE date='{ds}' AND act_symbol='SPY' LIMIT 1", retries=2, timeout=60)
            return ds, bool(rows)
        except Exception:
            return ds, False

    with cf.ThreadPoolExecutor(4) as ex:
        w = sorted(ds for ds, ok in ex.map(has, cands) if ok)
    log(f"DoltHub: {len(w)} Tage mit Daten in den letzten {days} Tagen: {w[-3:]}")
    return w


def _load_surfaces():
    surf = {}
    for t in UNIV:
        p = os.path.join(D, "surface", f"{t}.csv.gz")
        surf[t] = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame(columns=COLS)
    return surf


def _fetch_date_batch(date, syms):
    q = ("SELECT act_symbol, expiration, call_put, strike, bid, ask, vol, delta FROM option_chain "
         f"WHERE date='{date}' AND act_symbol IN ({','.join(repr(s) for s in syms)}) "
         "AND ABS(delta) BETWEEN 0.015 AND 0.985")
    rows, st = dolt(q)
    if st == "RowLimit":  # Batch zu groß -> teilen
        if len(syms) == 1:
            return rows
        h = len(syms) // 2
        return _fetch_date_batch(date, syms[:h]) + _fetch_date_batch(date, syms[h:])
    return rows


def backfill(budget_h=5.2, threads=8):
    os.makedirs(os.path.join(D, "surface"), exist_ok=True)
    state_p = os.path.join(D, "surface", "_state.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {"nodata": {}}
    dates = dolt_dates()
    log("DoltHub-Tage:", len(dates), dates[0], "…", dates[-1])
    if not dates:
        return
    surf = _load_surfaces()
    have = {t: set(surf[t]["date"].astype(str)) for t in UNIV}
    nod = {t: set(state["nodata"].get(t, [])) for t in UNIV}
    absent = set(state.get("absent", []))   # Ticker, die DoltHub nicht führt (z. B. QQQ, IWM)
    todo = []
    for d in dates:
        need = [t for t in UNIV if t not in absent and d not in have[t] and d not in nod[t]]
        if need:
            todo.append((d, need))
    log("offene Tage:", len(todo))
    # neueste zuerst (aktuelle Daten sind am wertvollsten), dann rückwärts
    todo = todo[::-1]
    new = {t: [] for t in UNIV}
    last_save = time.time() - 900   # erster Zwischenstand nach ~5 min

    def work(item):
        d, need = item
        rows = []
        for i in range(0, len(need), 5):
            rows += _fetch_date_batch(d, need[i:i + 5])
        return d, need, rows

    done = 0
    with cf.ThreadPoolExecutor(threads) as ex:
        it = iter(todo)
        running = set()
        for _ in range(threads * 2):
            try:
                running.add(ex.submit(work, next(it)))
            except StopIteration:
                break
        while running:
            fin, running = cf.wait(running, return_when=cf.FIRST_COMPLETED)
            for f in fin:
                try:
                    d, need, rows = f.result()
                except Exception as e:
                    log("fehler", str(e)[:200])
                    continue
                df = pd.DataFrame(rows)
                got = set()
                if len(df):
                    df = df.rename(columns={"act_symbol": "sym", "expiration": "expiry", "vol": "iv"})
                    df["date"] = d
                    df["cp"] = df["call_put"].str[0]
                    for c in ("strike", "bid", "ask", "iv", "delta"):
                        df[c] = pd.to_numeric(df[c], errors="coerce")
                    for t, g in df.groupby("sym"):
                        c = compact(g)
                        if len(c):
                            new[t].append(c)
                            got.add(t)
                for t in need:
                    if t not in got:
                        nod[t].add(d)
                done += 1
            if time.time() - T0 < budget_h * 3600:
                for _ in range(len(fin)):
                    try:
                        running.add(ex.submit(work, next(it)))
                    except StopIteration:
                        break
            if time.time() - last_save > 1200 or not running:
                _save(surf, new, nod, state, state_p)
                commit(f"Kette: DoltHub-Historie ({done}/{len(todo)} Tage)")
                log(f"gesichert: {done}/{len(todo)} Tage, {(time.time()-T0)/60:.0f} min")
                last_save = time.time()
    _save(surf, new, nod, state, state_p)
    log(f"backfill fertig: {done}/{len(todo)}")


def _save(surf, new, nod, state, state_p):
    for t in UNIV:
        if new[t]:
            surf[t] = pd.concat([surf[t]] + new[t]).drop_duplicates(["date", "expiry"], keep="last").sort_values(["date", "expiry"])
            new[t] = []
            if len(surf[t]):
                surf[t].to_csv(os.path.join(D, "surface", f"{t}.csv.gz"), index=False, compression="gzip")
    state["nodata"] = {t: sorted(v)[-60:] for t, v in nod.items() if v}
    state["updated"] = dt.datetime.utcnow().isoformat(timespec="minutes")
    json.dump(state, open(state_p, "w"))


# ---------------------------------------------------------------- DoltHub lokal (Klon) – für den großen Backfill
def _dolt_local(sql, repo):
    r = subprocess.run(["dolt", "sql", "-r", "csv", "-q", sql], cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    return pd.read_csv(io.StringIO(r.stdout)) if r.stdout.strip() else pd.DataFrame()


def backfill_local(repo, budget_h=5.0):
    """Komplette Historie aus einem lokalen DoltHub-Klon (dolt clone --depth 1) – Monat für Monat."""
    os.makedirs(os.path.join(D, "surface"), exist_ok=True)
    state_p = os.path.join(D, "surface", "_state.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {"nodata": {}}
    surf = _load_surfaces()
    have = {t: set(surf[t]["date"].astype(str)) for t in UNIV}
    nod = {t: set(state["nodata"].get(t, [])) for t in UNIV}
    new = {t: [] for t in UNIV}
    syms = ",".join(repr(s) for s in UNIV)
    months = pd.period_range("2019-01", dt.date.today().strftime("%Y-%m"), freq="M")[::-1]
    last_save = time.time()
    spy_have = have.get("SPY", set())
    recent = set(months[:2])
    broke = False
    for m in months:
        a, b = m.start_time.date().isoformat(), m.end_time.date().isoformat()
        if m not in recent and any(x[:7] == str(m) for x in spy_have):
            continue  # Monat bereits geladen
        t1 = time.time()
        df = _dolt_local("SELECT date, act_symbol, expiration, call_put, strike, bid, ask, vol, delta FROM option_chain "
                         f"WHERE date BETWEEN '{a}' AND '{b}' AND act_symbol IN ({syms}) AND ABS(delta) BETWEEN 0.015 AND 0.985", repo)
        n_new = 0
        if len(df):
            df = df.rename(columns={"act_symbol": "sym", "expiration": "expiry", "vol": "iv"})
            df["cp"] = df["call_put"].str[0]
            df["date"] = df["date"].astype(str).str[:10]
            df["expiry"] = df["expiry"].astype(str).str[:10]
            for c in ("strike", "bid", "ask", "iv", "delta"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            for (t, d), g in df.groupby(["sym", "date"]):
                if t not in have or d in have[t]:
                    continue
                c = compact(g)
                if len(c):
                    new[t].append(c)
                    n_new += 1
        log(f"{m}: {len(df)} Zeilen, {n_new} neue Ticker-Tage ({time.time()-t1:.0f}s)")
        if time.time() - last_save > 900:
            _save(surf, new, nod, state, state_p)
            commit(f"Kette: DoltHub-Historie bis {m}")
            last_save = time.time()
        if time.time() - T0 > budget_h * 3600:
            break
    complete = not broke
    _save(surf, new, nod, state, state_p)
    state["absent"] = sorted(t for t in UNIV if surf[t].empty and not new[t])
    state["clone_complete"] = bool(complete)
    json.dump(state, open(state_p, "w"))
    log(f"Klon-Backfill {'vollständig' if complete else 'unvollständig (Zeitbudget)'}; ohne DoltHub-Daten: {state['absent']}")
    commit("Kette: DoltHub-Historie (Klon)")
    # Vola-Historie aller Symbole, jahresweise
    os.makedirs(os.path.join(D, "volhist"), exist_ok=True)
    for y in range(2019, dt.date.today().year + 1):
        df = _dolt_local("SELECT date, act_symbol, iv_current, iv_week_ago, iv_month_ago, iv_year_high, iv_year_low, "
                         f"hv_current, hv_year_high, hv_year_low FROM volatility_history WHERE date BETWEEN '{y}-01-01' AND '{y}-12-31'", repo)
        if len(df):
            df.to_csv(os.path.join(D, "volhist", f"{y}.csv.gz"), index=False, compression="gzip")
            log(f"volhist {y}: {len(df)} Zeilen")
    commit("Kette: Vola-Historie (Klon)")


def volhist(budget_h=5.2):
    """IV/HV-Historie aller DoltHub-Symbole (Cross-Section für Screening). Pro Tag max. 2 Seiten à 1000."""
    os.makedirs(os.path.join(D, "volhist"), exist_ok=True)
    dates = dolt_dates()
    by_year = {}
    for d in dates:
        by_year.setdefault(d[:4], []).append(d)
    for y, ds in sorted(by_year.items(), reverse=True):
        p = os.path.join(D, "volhist", f"{y}.csv.gz")
        old = pd.read_csv(p) if os.path.exists(p) else pd.DataFrame(columns=["date"])
        have = set(old["date"].astype(str))
        miss = [d for d in ds if d not in have]
        if not miss:
            continue
        log("volhist", y, "fehlend", len(miss))

        def one(d):
            out = []
            for off in range(0, 4000, 1000):
                rows, st = dolt("SELECT date, act_symbol, iv_current, iv_week_ago, iv_month_ago, iv_year_high, iv_year_low, "
                                f"hv_current, hv_year_high, hv_year_low FROM volatility_history WHERE date='{d}' "
                                f"ORDER BY act_symbol LIMIT 1000 OFFSET {off}")
                out += rows
                if len(rows) < 1000:
                    break
            return out

        frames = []
        with cf.ThreadPoolExecutor(6) as ex:
            for rows in ex.map(one, miss):
                if rows:
                    frames.append(pd.DataFrame(rows))
                if time.time() - T0 > budget_h * 3600:
                    break
        if frames:
            new = pd.concat(frames)
            for c in new.columns[2:]:
                new[c] = pd.to_numeric(new[c], errors="coerce")
            pd.concat([old, new]).drop_duplicates(["date", "act_symbol"]).sort_values(["date", "act_symbol"]).to_csv(
                p, index=False, compression="gzip")
            commit(f"Kette: Vola-Historie {y}")
        if time.time() - T0 > budget_h * 3600:
            break


def main():
    mode = MODE
    if mode == "daily":
        for fn in (fetch_cboe, fetch_prices, fetch_fred, fetch_earnings):
            try:
                fn()
            except Exception as e:
                log("FEHLER", fn.__name__, e)
            commit(f"Kette: Daten {fn.__name__}")
    elif mode == "clone":
        backfill_local(sys.argv[2], budget_h=5.0)
    elif mode in ("dolt", "backfill"):
        backfill(budget_h=4.3)
        volhist(budget_h=5.4)
    elif mode == "cboe":
        fetch_cboe()
    elif mode == "intraday":
        fetch_intraday()
    os.makedirs(os.path.join(D, "logs"), exist_ok=True)
    open(os.path.join(D, "logs", f"fetch_{mode}.log"), "w").write("\n".join(LOG[-400:]))
    commit(f"Kette: Daten {mode} {dt.date.today()}")


if __name__ == "__main__":
    main()
