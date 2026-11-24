"""Freies Netz – Startidee „Overnight-Drift“: Ein großer Teil der langfristigen Aktienrendite entsteht über Nacht
(Cliff, Cooper & Gulen 2008; Lou, Polk & Skouras 2019). Kauf zum Schlusskurs, Verkauf zur Eröffnung des Folgetags
(exit="open"). Haltedauer: eine Nacht. Nur in nicht-panischen Regimen."""


def decide(ctx):
    g = ctx["genome"]
    if ctx.get("regime") in g.get("pause_in", ["Crash/Stress"]):
        return []
    held = {p["ticker"] for p in ctx["book"]["open"]}
    C, out = ctx["closes"], []
    for t in g.get("tickers", ["SPY", "QQQ"]):
        if t in held or t not in C.columns:
            continue
        px = float(C[t].dropna().iloc[-1])
        out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(px * (1 - g.get("stop", 0.03)), 2),
                    "target": None, "risk": g.get("risk", 0.03), "exit": "open", "max_days": 1})
    return out
