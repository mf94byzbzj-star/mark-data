"""Statistik für die Agentenkette: geclusterte t-Werte, Benjamini-Hochberg, Walk-forward-Auswertung."""
import math

import numpy as np
import pandas as pd


def cluster_t(r, clusters):
    """t-Wert des Mittelwerts mit nach Clustern (z. B. Monaten) robusten Standardfehlern."""
    r = np.asarray(r, float)
    n = len(r)
    if n < 5:
        return np.nan
    m = r.mean()
    e = pd.Series(r - m).groupby(np.asarray(clusters)).sum()
    g = len(e)
    if g < 3:
        return np.nan
    v = (e ** 2).sum() * g / (g - 1) / n ** 2
    return m / math.sqrt(v) if v > 0 else np.nan


def p_one_sided(t):
    if t is None or not np.isfinite(t):
        return 1.0
    return 0.5 * math.erfc(t / math.sqrt(2))


def bh(pvals, q=0.10):
    """Benjamini-Hochberg: True = signifikant bei FDR q."""
    p = np.asarray(pvals, float)
    n = len(p)
    if n == 0:
        return np.array([], bool)
    o = np.argsort(p)
    thr = q * np.arange(1, n + 1) / n
    ok = p[o] <= thr
    k = np.max(np.where(ok)[0]) + 1 if ok.any() else 0
    res = np.zeros(n, bool)
    res[o[:k]] = True
    return res


def split_date(dates, frac=0.6):
    d = pd.DatetimeIndex(sorted(pd.unique(pd.DatetimeIndex(dates))))
    return d[int(len(d) * frac)] if len(d) else None


def summarize(t, col="ret"):
    """Kennzahlen einer Trade-Menge (ret = Return on Risk je Trade)."""
    if t is None or len(t) == 0:
        return {"n": 0}
    r = t[col].values
    months = pd.DatetimeIndex(t["entry"]).to_period("M").astype(str)
    wk = t.groupby(pd.DatetimeIndex(t["entry"]).to_period("W"))[col].mean()
    eq = wk.cumsum()
    dd = float((eq - eq.cummax()).min()) if len(eq) else 0.0
    yrs = max((pd.Timestamp(t["entry"].max()) - pd.Timestamp(t["entry"].min())).days / 365.25, 0.25)
    per_year = len(wk) / yrs
    sh = wk.mean() / wk.std() * math.sqrt(per_year) if len(wk) > 3 and wk.std() > 0 else np.nan
    tt = cluster_t(r, months)
    return {"n": int(len(r)), "mean": float(np.mean(r)), "median": float(np.median(r)), "win": float((r > 0).mean()),
            "t": float(tt) if tt == tt else None, "p": p_one_sided(tt), "sharpe_w": float(sh) if sh == sh else None,
            "maxdd_w": dd, "avg_win": float(r[r > 0].mean()) if (r > 0).any() else 0.0,
            "avg_loss": float(r[r <= 0].mean()) if (r <= 0).any() else 0.0,
            "tickers": int(t["ticker"].nunique()), "start": str(pd.Timestamp(t["entry"].min()).date()),
            "end": str(pd.Timestamp(t["entry"].max()).date())}


def walk_forward(trades, mask, split, base_mask=None):
    """Bewertet eine Bedingung (mask) gegen die unbedingte Strategie, In-Sample vs. Out-of-Sample."""
    e = pd.DatetimeIndex(trades["entry"])
    isd, oos = e < split, e >= split
    sel = trades[mask & isd], trades[mask & oos]
    base = trades if base_mask is None else trades[base_mask]
    eb = pd.DatetimeIndex(base["entry"])
    res = {"is": summarize(sel[0]), "oos": summarize(sel[1]),
           "oos_stress": summarize(sel[1], "ret_stress"),
           "base_oos": summarize(base[eb >= split]), "base_is": summarize(base[eb < split])}
    o = sel[1]
    if len(o) >= 10:
        mid = split_date(o["entry"], 0.5)
        h1, h2 = o[pd.DatetimeIndex(o["entry"]) < mid], o[pd.DatetimeIndex(o["entry"]) >= mid]
        res["oos_h1"], res["oos_h2"] = float(h1["ret"].mean()), float(h2["ret"].mean())
    return res


def verdict(res, cfg):
    """Status nach den festen Regeln der Kette."""
    o, s = res["oos"], res["oos_stress"]
    if o.get("n", 0) < cfg["min_oos_trades"]:
        return "zu wenig Daten"
    b = res.get("base_oos", {})
    beats_base = b.get("n", 0) == 0 or o["mean"] >= b.get("mean", -9)
    ok = (o["mean"] > 0 and (o.get("t") or 0) >= cfg["min_oos_t"] and s.get("mean", -1) > 0
          and res.get("oos_h1", -1) > 0 and res.get("oos_h2", -1) > 0 and res.get("bh", False) and beats_base)
    if ok:
        return "bestätigt"
    # P001 (freigegeben 23.11.2026): Kandidat nur bei OOS t >= cand_t (1,5) UND echtem Vorsprung vor der Strategie ohne
    # Trigger; darunter (t >= 1) Status „beobachten“ – wird angezeigt, aber nicht gehandelt.
    edge = b.get("n", 0) == 0 or o["mean"] > b.get("mean", -9)
    if o["mean"] > 0 and (o.get("t") or 0) >= cfg.get("cand_t", 1.5) and edge:
        return "Kandidat"
    if o["mean"] > 0 and (o.get("t") or 0) >= 1.0:
        return "beobachten"
    return "verworfen"
