"""Gamma Exposure (GEX), Call-/Put-Walls und Gamma-Flip aus einer Kette mit Open Interest.

Konvention (SpotGamma/SqueezeMetrics-Näherung): Händler sind long Calls und short Puts der Kunden-Gegenseite,
d. h. Call-Gamma zählt positiv, Put-Gamma negativ. GEX in $ je 1 % Kursbewegung.
"""
import numpy as np
import pandas as pd

from .bs import gamma as bs_gamma


def gex_profile(chain, spot, day, max_dte=60):
    c = chain.copy()
    c["dte"] = (pd.to_datetime(c["expiry"]) - pd.Timestamp(day)).dt.days
    c = c[(c.dte >= 0) & (c.dte <= max_dte) & (c.open_interest > 0)]
    if c.empty:
        return None
    sign = np.where(c.cp == "C", 1.0, -1.0)
    T = np.maximum(c.dte.values, 0.5) / 365
    iv = c.iv.where(c.iv > 0.01).fillna(c.iv[c.iv > 0.01].median() if (c.iv > 0.01).any() else 0.3).values
    g_now = bs_gamma(spot, c.strike.values, T, iv)
    per = sign * g_now * c.open_interest.values * 100 * spot * spot * 0.01
    c["gex"] = per
    by_k = c.groupby("strike")["gex"].sum()
    calls = c[c.cp == "C"].groupby("strike")["gex"].sum()
    puts = c[c.cp == "P"].groupby("strike")["gex"].sum()
    call_wall = float(calls[calls.index >= spot * 0.98].idxmax()) if (calls.index >= spot * 0.98).any() else None
    put_wall = float(puts[puts.index <= spot * 1.02].idxmin()) if (puts.index <= spot * 1.02).any() else None
    # Flip: Gesamt-GEX bei hypothetischem Kurs
    grid = np.linspace(spot * 0.85, spot * 1.15, 61)
    tot = []
    for s in grid:
        g = bs_gamma(s, c.strike.values, T, iv)
        tot.append(float((sign * g * c.open_interest.values * 100 * s * s * 0.01).sum()))
    tot = np.array(tot)
    flip = None
    cross = np.where(np.diff(np.sign(tot)) != 0)[0]
    if len(cross):
        i = cross[np.argmin(np.abs(grid[cross] - spot))]
        x0, x1, y0, y1 = grid[i], grid[i + 1], tot[i], tot[i + 1]
        flip = float(x0 - y0 * (x1 - x0) / (y1 - y0))
    total = float(per.sum())
    top = by_k.abs().sort_values(ascending=False).head(8)
    return {"spot": spot, "net_gex": total, "call_wall": call_wall, "put_wall": put_wall, "flip": flip,
            "regime": "positiv" if total > 0 else "negativ",
            "above_flip": (spot > flip) if flip else None,
            "profile": [[round(float(k), 2), round(float(by_k[k]) / 1e6, 3)] for k in sorted(by_k.index)
                        if spot * 0.85 <= k <= spot * 1.15],
            "key_levels": [round(float(k), 2) for k in top.index],
            "pc_oi": float(c.loc[c.cp == "P", "open_interest"].sum() / max(c.loc[c.cp == "C", "open_interest"].sum(), 1))}
