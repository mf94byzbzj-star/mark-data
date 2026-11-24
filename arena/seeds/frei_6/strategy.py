"""Freies Netz – Startidee „Intraday-Momentum“ (Gao, Han, Li & Zhou 2018, JFE): Die Rendite der ersten halben Stunde
(und der restlichen Sitzung bis ~15:30) sagt die Richtung der letzten halben Stunde voraus. Einstieg ~15:30 New York,
Ausstieg zum Schlusskurs (exit="close"). Haltedauer: ca. 30 Minuten."""


def decide(ctx):
    return []          # handelt nur im Intraday-Fenster


def decide_intraday(ctx):
    g, out = ctx["genome"], []
    for t in g.get("tickers", ["SPY", "QQQ", "IWM"]):
        b = ctx["bars"].get(t)
        if b is None or len(b) < 12:
            continue
        o = float(b["Open"].iloc[0])
        r1 = float(b["Close"].iloc[5]) / o - 1          # 9:30–10:00
        r_rest = float(b["Close"].iloc[-1]) / float(b["Close"].iloc[5]) - 1
        sig = r1 + (r_rest if g.get("use_rest", True) else 0)
        if abs(sig) < g.get("min_move", 0.002):
            continue
        d = "long" if sig > 0 else "short"
        px = float(b["Close"].iloc[-1])
        stop = px * (1 - g.get("stop", 0.006)) if d == "long" else px * (1 + g.get("stop", 0.006))
        out.append({"type": "equity", "ticker": t, "direction": d, "stop": round(stop, 2), "target": None,
                    "risk": g.get("risk", 0.02), "exit": "close", "max_days": 0})
    return out
