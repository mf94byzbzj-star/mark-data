"""Freies Netz „Insider & Aufmerksamkeit“: kauft Aktien mit frischen aktiven Insider-Käufen (SEC Form 4),
nur wenn der Kurs nicht im freien Fall ist; Google-Trends-Sprung erhöht die Größe; Positionslimit."""
import datetime as dt
import numpy as np


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    opn = ctx["book"]["open"]
    held = {p["ticker"] for p in opn}
    room = max(0, g.get("max_open", 6) - sum(1 for p in opn if p["kind"] == "equity"))
    today = dt.date.fromisoformat(str(ctx["day"])[:10])
    out = []
    trend = {x.get("ticker") for x in ctx.get("trends") or []}

    def fresh(x):
        try:
            return (today - dt.date.fromisoformat(str(x.get("filed"))[:10])).days <= g.get("max_age_days", 14)
        except Exception:
            return False

    for t, sigs in (ctx.get("filings") or {}).items():
        if t in held or t not in C.columns:
            continue
        s = C[t].dropna()
        if len(s) < 60:
            continue
        px, sd = s.iloc[-1], s.pct_change().iloc[-20:].std()
        buy = any(x["dir"] > 0 and x["sig"] in g.get("buy_signals", ["I2", "I3", "I4"]) and fresh(x) for x in sigs)
        sell = any(x["dir"] < 0 and fresh(x) for x in sigs)
        not_falling = px > s.iloc[-50:].mean() * g.get("min_vs_sma50", 0.92)
        if buy and not sell and not_falling:
            stop = px * (1 - 2 * sd * np.sqrt(20))
            out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                        "target": round(px + 2 * (px - stop), 2),
                        "risk": min(g.get("risk", 0.04) * (1.5 if t in trend else 1), 0.10), "max_days": g.get("max_days", 40)})
        elif sell and g.get("short_sells", False):
            stop = px * (1 + 2 * sd * np.sqrt(20))
            out.append({"type": "equity", "ticker": t, "direction": "short", "stop": round(stop, 2),
                        "target": round(px - 2 * (stop - px), 2), "risk": min(g.get("risk", 0.04), 0.10), "max_days": 20})
    return out[: min(g.get("new_per_day", 2), room)]
