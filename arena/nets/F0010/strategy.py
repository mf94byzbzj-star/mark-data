"""Freies Netz – Startidee „Relative Stärke Sektoren“: long die stärksten Sektor-ETFs, short die schwächsten
(marktneutral), wöchentlich neu gewichtet über die Haltedauer."""
import numpy as np

SECT = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "XBI", "KRE"]


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    if ctx["book"]["open"]:
        return []
    rows = []
    for t in SECT:
        if t in C.columns:
            s = C[t].dropna()
            if len(s) > 130:
                rows.append((s.iloc[-1] / s.iloc[-g.get("lookback", 63)] - 1, t, s.iloc[-1], s.pct_change().iloc[-20:].std()))
    if len(rows) < 6:
        return []
    rows.sort()
    out = []
    for r, t, px, sd in rows[-g.get("n_each", 2):]:
        stop = px * (1 - 2.5 * sd * np.sqrt(g.get("hold", 20)))
        out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2), "target": None,
                    "risk": g.get("risk", 0.03), "max_days": g.get("hold", 20)})
    if g.get("short_weak", True):
        for r, t, px, sd in rows[: g.get("n_each", 2)]:
            stop = px * (1 + 2.5 * sd * np.sqrt(g.get("hold", 20)))
            out.append({"type": "equity", "ticker": t, "direction": "short", "stop": round(stop, 2), "target": None,
                        "risk": g.get("risk", 0.03), "max_days": g.get("hold", 20)})
    return out
