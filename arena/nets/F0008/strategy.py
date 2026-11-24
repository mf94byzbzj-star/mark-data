"""Freies Netz „Angst-Käufer“: nach Panik (VIX hoch, Kurve invertiert) überverkaufte Werte mit intaktem Langfristtrend
kaufen, aber erst nach Kapitulation (5-Tage-Einbruch) und mit Positionslimit; in ruhigen Phasen Long-Vola nur aus
OOS-belegten Long-Vola-Triggern."""
import numpy as np


def decide(ctx):
    g, C, mk = ctx["genome"], ctx["closes"], ctx.get("market") or {}
    opn = ctx["book"]["open"]
    held = {p["ticker"] for p in opn}
    n_eq = sum(1 for p in opn if p["kind"] == "equity")
    n_opt = sum(1 for p in opn if p["kind"] == "option")
    out = []
    if (mk.get("vix") or 0) >= g.get("vix_min", 25) and (mk.get("vix_ts") or 0) >= g.get("vix_ts_min", 1.0):
        room = max(0, g.get("max_open", 6) - n_eq)
        rows = []
        for t in C.columns:
            s = C[t].dropna()
            if len(s) < 210 or t in held:
                continue
            d = s.diff()
            rsi = 100 - 100 / (1 + d.clip(lower=0).iloc[-14:].mean() / max(-d.clip(upper=0).iloc[-14:].mean(), 1e-9))
            r5 = s.iloc[-1] / s.iloc[-6] - 1
            if (rsi < g.get("rsi_max", 30) and s.iloc[-1] > s.iloc[-200:].mean() * g.get("min_vs_sma200", 0.9)
                    and r5 <= g.get("max_ret5", -0.05)):
                rows.append((rsi, t, s.iloc[-1], s.pct_change().iloc[-20:].std()))
        rows.sort()
        for _, t, px, sd in rows[: min(g.get("new_per_day", 2), room)]:
            stop = px * (1 - 2.5 * sd * np.sqrt(10))
            out.append({"type": "equity", "ticker": t, "direction": "long", "stop": round(stop, 2),
                        "target": round(px * (1 + 2.5 * sd * np.sqrt(10) * 1.5), 2), "risk": min(g.get("risk", 0.04), 0.10),
                        "max_days": g.get("max_days", 15)})
    elif n_opt < g.get("max_open_opt", 3):
        for c in ctx["candidates"]:
            if (c["vol"] == "long" and c["ticker"] not in held and (c.get("oos_mean") or 0) > g.get("min_oos_long", 0.1)
                    and (c.get("oos_t") or 0) >= g.get("min_t_long", 1.5)):
                out.append({"type": "option", "cid": c["cid"], "risk": min(g.get("risk_opt", 0.03), 0.10)})
                break
    return out
