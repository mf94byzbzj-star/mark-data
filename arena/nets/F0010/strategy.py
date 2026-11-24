"""Freies Netz „Duales Sektor-Momentum“: long die stärksten Sektor-ETFs nur bei positivem Absolut-Momentum, short die
schwächsten nur bei negativem Absolut-Momentum; ohne passende Kandidaten Cash. Neu gewichtet, wenn das Depot leer ist."""
import numpy as np

SECT = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH", "XBI", "KRE"]


def decide(ctx):
    g, C = ctx["genome"], ctx["closes"]
    if ctx["book"]["open"]:
        return []
    lb, hold = g.get("lookback", 63), g.get("hold", 20)
    rows = []
    for t in SECT:
        if t in C.columns:
            s = C[t].dropna()
            if len(s) > max(130, lb + 1):
                rows.append((s.iloc[-1] / s.iloc[-lb] - 1, t, s.iloc[-1], s.pct_change().iloc[-20:].std()))
    if len(rows) < 6:
        return []
    rows.sort()
    out, n, risk = [], g.get("n_each", 2), min(g.get("risk", 0.03), 0.10)
    for r, t, px, sd in rows[-n:]:
        if r > g.get("min_abs_long", 0.0):
            stop = px * (1 - 2.5 * sd * np.sqrt(hold))
            out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2), "target": None,
                        "risk": risk, "max_days": hold})
    if g.get("short_weak", True):
        for r, t, px, sd in rows[:n]:
            if r < g.get("max_abs_short", 0.0):
                stop = px * (1 + 2.5 * sd * np.sqrt(hold))
                out.append({"type": "equity", "ticker": t, "direction": "short", "stop": round(stop, 2), "target": None,
                            "risk": risk, "max_days": hold})
    return out
