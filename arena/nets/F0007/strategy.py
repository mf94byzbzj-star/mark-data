"""Freies Netz – Startidee „Prämienverkäufer“: verkauft Vola mit begrenztem Risiko (Credit Spreads/Condors),
nur bei positivem Gamma-Regime und positivem Backtest-Erwartungswert."""


def decide(ctx):
    g = ctx["genome"]
    held = {p["ticker"] for p in ctx["book"]["open"]}
    if ctx.get("regime") in g.get("pause_in", ["Crash/Stress"]):
        return []
    n_open = sum(1 for p in ctx["book"]["open"] if p["kind"] == "option")
    pick = []
    for c in ctx["candidates"]:
        if c["vol"] != "short" or c["ticker"] in held or (c.get("oos_mean") or 0) <= g.get("min_oos", 0.0):
            continue
        gex = (c.get("gex") or {}).get("net_gex")
        if g.get("need_pos_gamma", True) and not (gex and gex > 0):
            continue
        if c["payoff"].get("debit_fill", 0) >= 0:      # nur Credit-Strukturen
            continue
        pick.append(((c.get("oos_mean") or 0) * (c.get("oos_win") or 0), c))
    pick.sort(key=lambda x: -x[0])
    return [{"type": "option", "cid": c["cid"], "risk": g.get("risk", 0.05)}
            for _, c in pick[: max(0, min(g.get("new_per_day", 2), g.get("max_open", 6) - n_open))]]
