"""Gemeinsame Helfer der Stufen: Pfade, Trade-/Merkmals-Cache, JSON-Ausgabe."""
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import yaml

from chain.core import backtest, data, features

LAB = data.LAB
OUT = os.path.join(LAB, "out")
CACHE = os.path.join(LAB, "cache")
RES = os.path.join(LAB, "research")
CFG = data.CFG
for p in (OUT, CACHE, RES):
    os.makedirs(p, exist_ok=True)


def jdump(obj, name):
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if not np.isfinite(o) else round(float(o), 6)
        if isinstance(o, (pd.Timestamp, np.datetime64)):
            return str(pd.Timestamp(o).date())
        if isinstance(o, float) and not np.isfinite(o):
            return None
        return str(o)

    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        if isinstance(x, float):
            return None if not np.isfinite(x) else round(x, 6)
        return x
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(clean(obj), f, default=conv, ensure_ascii=False, indent=1)


def jload(name, default=None):
    p = os.path.join(OUT, name)
    return json.load(open(p)) if os.path.exists(p) else default


def yload(name, default=None):
    p = os.path.join(RES, name)
    return yaml.safe_load(open(p)) if os.path.exists(p) else default


def _feat_one(t):
    try:
        return features.ticker_features(t, _MKT)
    except Exception as e:
        print("features", t, e)
        return None


_MKT = None


def all_features(refresh=False):
    """Merkmale aller Ticker (Cache: chain_lab/cache/features.pkl, neu bei neuen Daten)."""
    global _MKT
    p = os.path.join(CACHE, "features.pkl")
    stamp = _data_stamp()
    if not refresh and os.path.exists(p):
        f = pd.read_pickle(p)
        if f.attrs.get("stamp") == stamp:
            return f
    _MKT = features.market()
    frames = [x for x in map(_feat_one, data.UNIV) if x is not None]
    f = pd.concat(frames)
    f.index.name = "date"
    f = f.reset_index()
    f.attrs["stamp"] = stamp
    f.to_pickle(p)
    return f


def _code_stamp():
    import hashlib
    h = hashlib.md5()
    for f in ("core/backtest.py", "core/features.py", "core/surface.py", "core/data.py"):
        h.update(open(os.path.join(data.ROOT, "chain", f), "rb").read())
    return h.hexdigest()[:10]


def _data_stamp():
    d = os.path.join(data.D, "surface")
    st = os.path.join(d, "_state.json")
    a = json.load(open(st)).get("updated", "") if os.path.exists(st) else ""
    px = os.path.join(data.D, "prices", "SPY.csv")
    b = str(int(os.path.getmtime(px))) if os.path.exists(px) else ""
    return a + "|" + b + "|" + str(len(os.listdir(d)) if os.path.isdir(d) else 0) + "|" + _code_stamp()


def _trades_one(t):
    try:
        closed, _ = backtest.run_ticker(t)
        return closed
    except Exception as e:
        print("backtest", t, e)
        return pd.DataFrame()


def all_trades(refresh=False, workers=None):
    """Alle Strategien × Ticker × Einstiege (unbedingt). Trigger filtern daraus nur noch."""
    p = os.path.join(CACHE, "trades.pkl")
    stamp = _data_stamp() + "|" + str(sorted(backtest.STRATS))
    if not refresh and os.path.exists(p):
        t = pd.read_pickle(p)
        if t.attrs.get("stamp") == stamp:
            return t
    t0 = time.time()
    with ProcessPoolExecutor(workers or max(os.cpu_count() or 2, 2)) as ex:
        frames = list(ex.map(_trades_one, data.UNIV))
    t = pd.concat([f for f in frames if len(f)], ignore_index=True)
    t["entry"] = pd.to_datetime(t["entry"])
    t["exit"] = pd.to_datetime(t["exit"])
    t.attrs["stamp"] = stamp
    t.to_pickle(p)
    print(f"Backtests: {len(t)} Trades in {time.time()-t0:.0f}s")
    return t


def with_features(trades, feats):
    f = feats.rename(columns={"date": "entry"})
    return trades.merge(f, on=["ticker", "entry"], how="left")
