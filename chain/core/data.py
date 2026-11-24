"""Lader für alle Datenquellen der Agentenkette (liest nur chain_data/, schreibt nie dorthin)."""
import functools
import glob
import os

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = os.environ.get("CHAIN_DATA", os.path.join(ROOT, "chain_data"))
LAB = os.environ.get("CHAIN_LAB", os.path.join(ROOT, "chain_lab"))
CFG = yaml.safe_load(open(os.path.join(ROOT, "chain", "config.yaml")))
ETFS = set(CFG["universe"]["etfs"])
UNIV = CFG["universe"]["etfs"] + CFG["universe"]["stocks"]


def safe(sym):
    return sym.replace("^", "IDX_")


@functools.lru_cache(maxsize=None)
def prices(sym):
    p = os.path.join(D, "prices", f"{safe(sym)}.csv")
    if not os.path.exists(p):
        # Fallback: Kursdaten des bestehenden Options-Quant-Workflows (nur lesen)
        p = os.path.join(ROOT, "data", "prices", f"{safe(sym)}.csv")
        if not os.path.exists(p):
            return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


@functools.lru_cache(maxsize=None)
def surface(sym):
    p = os.path.join(D, "surface", f"{sym}.csv.gz")
    if not os.path.exists(p):
        return None
    from .surface import clean
    df = pd.read_csv(p, parse_dates=["date", "expiry"])
    return clean(df).sort_values(["date", "expiry"]).reset_index(drop=True)


@functools.lru_cache(maxsize=None)
def earnings(sym):
    p = os.path.join(D, "earnings", f"{sym}.csv")
    if not os.path.exists(p):
        p = os.path.join(ROOT, "data", "earnings", f"{sym}.csv")
        if not os.path.exists(p):
            return pd.DatetimeIndex([])
    df = pd.read_csv(p)
    s = pd.to_datetime(df.iloc[:, 0].astype(str).str[:19], errors="coerce", utc=True).dt.tz_localize(None)
    # Nachbörsliche Termine (>= 16 Uhr) wirken am Folgetag
    hrs = s.dt.hour
    eff = s.dt.normalize() + pd.to_timedelta((hrs >= 16).astype(int), unit="D")
    return pd.DatetimeIndex(sorted(set(eff.dropna())))


@functools.lru_cache(maxsize=None)
def fred():
    p = os.path.join(D, "macro", "fred.csv")
    if not os.path.exists(p):
        return pd.DataFrame()
    return pd.read_csv(p, index_col=0, parse_dates=True).sort_index()


def live_days():
    return sorted(d for d in os.listdir(os.path.join(D, "live")) if d[:2] == "20") if os.path.isdir(os.path.join(D, "live")) else []


@functools.lru_cache(maxsize=None)
def live_chain(sym, day=None):
    days = live_days()
    if not days:
        return None, None
    day = day or days[-1]
    p = os.path.join(D, "live", day, f"{sym}.csv.gz")
    if not os.path.exists(p):
        return None, day
    return pd.read_csv(p), day


@functools.lru_cache(maxsize=None)
def live_summary(day=None):
    days = live_days()
    if not days:
        return pd.DataFrame()
    p = os.path.join(D, "live", day or days[-1], "_summary.csv")
    return pd.read_csv(p).set_index("ticker") if os.path.exists(p) else pd.DataFrame()


@functools.lru_cache(maxsize=None)
def volhist():
    fs = sorted(glob.glob(os.path.join(D, "volhist", "*.csv.gz")))
    if not fs:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(f, parse_dates=["date"]) for f in fs])


def close_on(sym, dates):
    """Schlusskurs am letzten Handelstag <= Datum (DoltHub-Daten sind teils Samstage)."""
    px = prices(sym)
    if px is None:
        return pd.Series(np.nan, index=dates)
    return px["Close"].reindex(px.index.union(dates)).ffill().reindex(dates)
