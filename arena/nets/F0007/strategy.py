"""Freies Netz „Prämienverkäufer, regimebewusst“: verkauft Vola mit begrenztem Risiko (Credit-Strukturen) nur bei
positivem Gamma, positivem OOS-Erwartungswert mit Mindest-t, Trigger nicht verworfen, und nie im Stress/Bärenmarkt;
im Korrektur-Regime halbe Größe und nur neutrale/bärische Strukturen (keine bullischen Put-Spreads ins fallende Messer)."""


def decide(ctx):
    g = ctx["genome"]
    reg = ctx.get("regime")
    if reg in g.get("pause_in", ["Crash/Stress", "Bärenmarkt"]):
        return []
    held = {p["ticker"] for p in ctx["book"]["open"]}
    n_open = sum(1 for p in ctx["book"]["open"] if p["kind"] == "option")
    risk = g.get("risk", 0.04) * (g.get("korrektur_scale", 0.5) if reg == "Korrektur" else 1.0)
    pick = []
    for c in ctx["candidates"]:
        if c["vol"] != "short" or c["ticker"] in held:
            continue
        if c.get("trigger_status") not in g.get("ok_status", ["bestätigt", "Kandidat", "beobachten"]):
            continue
        if (c.get("oos_mean") or 0) <= g.get("min_oos", 0.0) or (c.get("oos_t") or 0) < g.get("min_t", 1.0):
            continue
        if reg == "Korrektur" and c.get("bias") == "bull":
            continue
        gex = (c.get("gex") or {}).get("net_gex")
        if g.get("need_pos_gamma", True) and not (gex and gex > 0):
            continue
        pay = c.get("payoff") or {}
        if pay.get("debit_fill", 0) >= 0:      # nur Credit-Strukturen
            continue
        pick.append(((c.get("oos_mean") or 0) * (c.get("oos_win") or 0) * (pay.get("pop") or 0.5), c))
    pick.sort(key=lambda x: -x[0])
    k = max(0, min(g.get("new_per_day", 2), g.get("max_open", 6) - n_open))
    return [{"type": "option", "cid": c["cid"], "risk": min(risk, 0.10)} for _, c in pick[:k]]
