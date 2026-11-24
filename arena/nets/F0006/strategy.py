"""Freies Netz „Trendfolger mit Marktfilter“: kauft Aktien im intakten Aufwärtstrend mit risikobereinigtem Momentum,
aber nur bei Markt-Rückenwind (SPY über SMA200) und relativer Stärke zu SPY; verkauft bei Trendbruch (Schluss < SMA50).
Schnittstelle: decide(ctx) -> Liste von Orders (siehe arena/AGENTS.md). Nur Daten aus ctx."""
import numpy as np


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    mk = ctx.get("market") or {}
    out = []
    # 1) Trendbruch-Ausstieg für offene Positionen (Schluss unter SMA50)
    for p in ctx["book"]["open"]:
        t = p.get("ticker")
        if p.get("kind") == "equity" and t in C.columns:
            s = C[t].dropna()
            if len(s) >= 50 and s.iloc[-1] < s.iloc[-50:].mean() * (1 - g.get("exit_below_sma50", 0.0)):
                out.append({"type": "close", "id": p["id"]})
    closing = {o["id"] for o in out}
    # 2) Neue Käufe nur bei Rückenwind
    if ctx.get("regime") in g.get("pause_in", ["Crash/Stress", "Bärenmarkt"]):
        return out
    if (mk.get("spy_dist200") or 0) < g.get("min_spy_dist200", 0.0):
        return out
    held = {p["ticker"] for p in ctx["book"]["open"] if p["id"] not in closing}
    if len(held) >= g.get("max_open", 5):
        return out
    spy = C["SPY"].dropna() if "SPY" in C.columns else None
    md = g.get("mom_days", 60)
    spy_mom = (spy.iloc[-1] / spy.iloc[-md] - 1) if spy is not None and len(spy) > md else 0.0
    rows = []
    for t in C.columns:
        s = C[t].dropna()
        if len(s) < 210 or t in held:
            continue
        px, sma50, sma200 = s.iloc[-1], s.iloc[-50:].mean(), s.iloc[-200:].mean()
        mom = s.iloc[-1] / s.iloc[-md] - 1
        vol = s.pct_change().iloc[-20:].std() * np.sqrt(252)
        if px > sma50 > sma200 and mom > 0 and mom - spy_mom > g.get("min_rel_mom", 0.0):
            rows.append((mom / max(vol, 0.1), t, px, vol))
    rows.sort(reverse=True)
    for _, t, px, vol in rows[: g.get("new_per_day", 1)]:
        stop = px * (1 - g.get("stop_vol_mult", 1.5) * vol * np.sqrt(20 / 252))
        out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                    "target": round(px + g.get("target_r", 3) * (px - stop), 2), "risk": min(g.get("risk", 0.04), 0.10),
                    "max_days": g.get("max_days", 40)})
    return out
