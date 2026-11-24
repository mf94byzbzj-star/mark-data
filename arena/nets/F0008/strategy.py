"""Freies Netz – Startidee „Angst-Käufer“: nach Panik (VIX hoch, Kurve invertiert) überverkaufte Qualitätswerte kaufen,
in ruhigen Phasen Long-Vola (Straddles) auf Werte mit sehr billiger Vola."""
import numpy as np


def decide(ctx):
    g, C, mk = ctx["genome"], ctx["closes"], ctx.get("market") or {}
    held = {p["ticker"] for p in ctx["book"]["open"]}
    out = []
    if (mk.get("vix") or 0) >= g.get("vix_min", 25) and (mk.get("vix_ts") or 0) >= g.get("vix_ts_min", 1.0):
        rows = []
        for t in C.columns:
            s = C[t].dropna()
            if len(s) < 210 or t in held:
                continue
            d = s.diff()
            rsi = 100 - 100 / (1 + d.clip(lower=0).iloc[-14:].mean() / max(-d.clip(upper=0).iloc[-14:].mean(), 1e-9))
            if rsi < g.get("rsi_max", 30) and s.iloc[-1] > s.iloc[-200:].mean() * g.get("min_vs_sma200", 0.9):
                rows.append((rsi, t, s.iloc[-1], s.pct_change().iloc[-20:].std()))
        rows.sort()
        for _, t, px, sd in rows[: g.get("new_per_day", 2)]:
            stop = px * (1 - 2.5 * sd * np.sqrt(10))
            out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                        "target": round(px * (1 + 2.5 * sd * np.sqrt(10) * 1.5), 2), "risk": g.get("risk", 0.04), "max_days": 15})
    else:
        for c in ctx["candidates"]:
            if c["vol"] == "long" and c["ticker"] not in held and (c.get("oos_mean") or 0) > g.get("min_oos_long", 0.1):
                out.append({"type": "option", "cid": c["cid"], "risk": g.get("risk_opt", 0.03)})
                break
    return out
