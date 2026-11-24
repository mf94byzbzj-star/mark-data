"""Freies Netz – Startidee „Trendfolger“: kauft Aktien im intakten Aufwärtstrend mit frischem Momentum.
Supervisor darf alles ändern. Schnittstelle: decide(ctx) -> Liste von Orders (siehe arena/AGENTS.md)."""
import numpy as np


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    if ctx.get("regime") in g.get("pause_in", ["Crash/Stress", "Bärenmarkt"]):
        return []
    held = {p["ticker"] for p in ctx["book"]["open"]}
    if len(held) >= g.get("max_open", 5):
        return []
    rows = []
    for t in C.columns:
        s = C[t].dropna()
        if len(s) < 210 or t in held:
            continue
        px, sma50, sma200 = s.iloc[-1], s.iloc[-50:].mean(), s.iloc[-200:].mean()
        mom = s.iloc[-1] / s.iloc[-g.get("mom_days", 60)] - 1
        vol = s.pct_change().iloc[-20:].std() * np.sqrt(252)
        if px > sma50 > sma200 and mom > 0:
            rows.append((mom / max(vol, 0.1), t, px, vol))
    rows.sort(reverse=True)
    out = []
    for _, t, px, vol in rows[: g.get("new_per_day", 1)]:
        stop = px * (1 - g.get("stop_vol_mult", 1.5) * vol * np.sqrt(20 / 252))
        out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                    "target": round(px + 2 * (px - stop), 2), "risk": g.get("risk", 0.05), "max_days": g.get("max_days", 30)})
    return out
