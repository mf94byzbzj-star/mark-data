"""Freies Netz – Startidee „Insider & Aufmerksamkeit“: kauft Aktien mit aktiven Insider-Käufen (SEC Form 4),
meidet/shortet bei Insider-Verkaufswellen; Google-Trends-Suchsprünge als Zusatzsignal."""
import numpy as np


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    held = {p["ticker"] for p in ctx["book"]["open"]}
    out = []
    trend = {x["ticker"] for x in ctx.get("trends") or []}
    for t, sigs in (ctx.get("filings") or {}).items():
        if t in held or t not in C.columns:
            continue
        s = C[t].dropna()
        if len(s) < 30:
            continue
        px, sd = s.iloc[-1], s.pct_change().iloc[-20:].std()
        buy = any(x["dir"] > 0 and x["sig"] in g.get("buy_signals", ["I2", "I3", "I4"]) for x in sigs)
        sell = any(x["dir"] < 0 for x in sigs)
        if buy and not sell:
            stop = px * (1 - 2 * sd * np.sqrt(20))
            out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                        "target": round(px + 2 * (px - stop), 2), "risk": g.get("risk", 0.04) * (1.5 if t in trend else 1),
                        "max_days": g.get("max_days", 40)})
        elif sell and g.get("short_sells", False):
            stop = px * (1 + 2 * sd * np.sqrt(20))
            out.append({"type": "equity", "ticker": t, "direction": "short", "stop": round(stop, 2),
                        "target": round(px - 2 * (stop - px), 2), "risk": g.get("risk", 0.04), "max_days": 20})
    return out[: g.get("new_per_day", 2)]
