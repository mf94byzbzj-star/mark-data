"""Verdichtet rohe Optionsketten zu einer Vola-Oberfläche je (Datum, Verfall).

Spalten der kompakten Oberfläche:
  date, expiry, dte, fwd (implizierter Forward/Spot), iv_p10, iv_p25, iv_atm, iv_c25, iv_c10, spr_atm (rel. Bid-Ask-Spread ATM)
Delta-Punkte in Call-Delta-Äquivalent: c10=0.10, c25=0.25, atm=0.50, p25=0.75, p10=0.90.
"""
import numpy as np
import pandas as pd

POINTS = {"iv_c10": 0.10, "iv_c25": 0.25, "iv_atm": 0.50, "iv_p25": 0.75, "iv_p10": 0.90}
COLS = ["date", "expiry", "dte", "fwd", "iv_p10", "iv_p25", "iv_atm", "iv_c25", "iv_c10", "spr_atm"]


def _parity_forward(g, r=0.04):
    """Forward/Spot aus Put-Call-Parität am Strike mit kleinster |C-P|."""
    c = g[g.cp == "C"].set_index("strike")
    p = g[g.cp == "P"].set_index("strike")
    ks = c.index.intersection(p.index)
    if len(ks) == 0:
        return np.nan
    cm = (c.loc[ks, "bid"] + c.loc[ks, "ask"]) / 2
    pm = (p.loc[ks, "bid"] + p.loc[ks, "ask"]) / 2
    ok = (c.loc[ks, "bid"] > 0) & (p.loc[ks, "bid"] > 0)
    if not ok.any():
        return np.nan
    diff = (cm - pm)[ok]
    k = diff.abs().idxmin()
    T = max(g["dte"].iloc[0], 1) / 365.0
    return float(k + diff[k] * np.exp(r * T))


def compact(raw, spot=None, max_dte=200):
    """raw: DataFrame mit date, expiry, cp('C'/'P'), strike, bid, ask, iv, delta (Put-Delta negativ)."""
    out = []
    raw = raw.copy()
    raw["dte"] = (pd.to_datetime(raw["expiry"]) - pd.to_datetime(raw["date"])).dt.days
    raw = raw[(raw.dte >= 1) & (raw.dte <= max_dte)]
    for (d, e), g in raw.groupby(["date", "expiry"], sort=True):
        mid = (g.bid + g.ask) / 2
        g = g[(g.iv > 0.01) & (g.iv < 4) & g.delta.notna() & (g.bid > 0) & (g.ask > g.bid)
              & ((g.ask - g.bid) / mid.where(mid > 0) <= 0.6)]
        if len(g) < 3:
            continue
        # OTM-Optionen: Calls mit Delta < .55, Puts mit |Delta| < .55, in Call-Delta-Äquivalent
        calls = g[(g.cp == "C") & (g.delta <= 0.55) & (g.delta >= 0.02)]
        puts = g[(g.cp == "P") & (g.delta >= -0.55) & (g.delta <= -0.02)]
        x = np.concatenate([calls.delta.values, 1.0 + puts.delta.values])
        y = np.concatenate([calls.iv.values, puts.iv.values])
        if len(x) < 2:
            continue
        o = np.argsort(x)
        x, y = x[o], y[o]
        row = {"date": d, "expiry": e, "dte": int(g.dte.iloc[0])}
        for name, tgt in POINTS.items():
            # nur Interpolation, Extrapolation max. 0.05 Delta
            if tgt < x[0] - 0.05 or tgt > x[-1] + 0.05:
                row[name] = np.nan
            else:
                row[name] = float(np.interp(tgt, x, y))
        if not np.isfinite(row["iv_atm"]):
            continue
        fw = _parity_forward(g)
        row["fwd"] = fw if np.isfinite(fw) else (spot if spot else np.nan)
        near = g.iloc[(g.delta.abs() - 0.5).abs().argsort()[:2]]
        mid = (near.bid + near.ask) / 2
        rel = ((near.ask - near.bid) / mid.where(mid > 0)).median()
        row["spr_atm"] = float(rel) if np.isfinite(rel) else np.nan
        out.append(row)
    if not out:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(out)[COLS]
    for c in COLS[4:]:
        df[c] = df[c].astype(float).round(4)
    df["fwd"] = df["fwd"].astype(float).round(3)
    return df


def clean(df, r=0.04):
    """Plausibilitätsfilter für eine verdichtete Oberfläche (DoltHub und Cboe liefern teils fehlerhafte Quotes).
    - Flügel-IVs außerhalb 0,5–3 × ATM -> NaN
    - Strike-Reihenfolge der Delta-Punkte muss stimmen (σ10 > 0,53·σ25 auf beiden Seiten)
    - Forward je Verfall gegen den Median-Spot des Tages prüfen; Abweichung > 2 % -> ersetzen
    - Spread auf 0–0,5 begrenzen"""
    if df is None or df.empty:
        return df
    df = df.copy()
    atm = df["iv_atm"]
    df.loc[(atm < 0.02) | (atm > 3), "iv_atm"] = np.nan
    df = df[df["iv_atm"].notna()]
    # Ausreißer gegenüber den anderen Verfällen desselben Tages (z. B. ATM-IV 134 % neben 17 %)
    med = df.groupby("date")["iv_atm"].transform("median")
    n = df.groupby("date")["iv_atm"].transform("count")
    df = df[~((n >= 3) & ((df["iv_atm"] > 1.8 * med) | (df["iv_atm"] < 0.55 * med)))]
    atm = df["iv_atm"]
    for c in ("iv_p10", "iv_p25", "iv_c25", "iv_c10"):
        bad = (df[c] < 0.5 * atm) | (df[c] > 3 * atm)
        df.loc[bad, c] = np.nan
    df.loc[df["iv_p10"] < 0.53 * df["iv_p25"], "iv_p10"] = np.nan
    df.loc[df["iv_c10"] < 0.53 * df["iv_c25"], "iv_c10"] = np.nan
    T = df["dte"].clip(lower=1) / 365
    spot = df["fwd"] * np.exp(-r * T)
    med = spot.groupby(df["date"]).transform("median")
    off = (spot / med - 1).abs() > 0.02
    df.loc[off, "fwd"] = (med * np.exp(r * T))[off]
    df["spr_atm"] = df["spr_atm"].clip(lower=0, upper=0.5)
    return df
