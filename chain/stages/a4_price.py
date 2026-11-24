"""Agent 4 – Preisaktion: Passt die Optionsstruktur zum Chartbild?

Für jeden Ticker mit Kandidaten aus Agent 3:
  Trend (SMA20/50/200, Steigung, ADX), Momentum (RSI14, 20-T-Rendite), Volatilität (ATR, Bollinger-Squeeze),
  Unterstützungen/Widerstände (Swing-Hochs/-Tiefs, gebündelt), Ausbrüche (Donchian 20/55).
Daraus: Marktphase (Aufwärtstrend / Abwärtstrend / Seitwärts / Squeeze) und eine Eignungsnote je Struktur.
Agent 4 bestätigt die Struktur von Agent 3 oder schlägt eine besser passende vor (nur aus aktiven Triggern).
Ausgabe: chain_lab/out/a4_price.json
"""
import numpy as np
import pandas as pd

from chain.core import backtest, data
from chain.stages.common import jdump, jload

# Eignung je Phase (−2 … +2)
FIT = {
    "Aufwärtstrend":  {"pcs": 2, "bcs": 2, "ccs": -2, "bps": -2, "ic": -1, "ifly": -1, "cal_atm": 0, "cal_put": 0, "dcal": 0, "lstrad": -1, "pre_earn": 0, "earn_ifly": 0},
    "Abwärtstrend":   {"pcs": -2, "bcs": -2, "ccs": 2, "bps": 2, "ic": -1, "ifly": -1, "cal_atm": 0, "cal_put": 0, "dcal": 0, "lstrad": 0, "pre_earn": 0, "earn_ifly": -1},
    "Seitwärts":      {"pcs": 1, "bcs": -1, "ccs": 1, "bps": -1, "ic": 2, "ifly": 2, "cal_atm": 2, "cal_put": 2, "dcal": 2, "lstrad": -2, "pre_earn": 0, "earn_ifly": 1},
    "Squeeze":        {"pcs": -1, "bcs": 0, "ccs": -1, "bps": 0, "ic": -2, "ifly": -2, "cal_atm": -1, "cal_put": -1, "dcal": -1, "lstrad": 2, "pre_earn": 1, "earn_ifly": -2},
}


def _pivots(h, l, w=5):
    hi = h[(h == h.rolling(2 * w + 1, center=True).max())]
    lo = l[(l == l.rolling(2 * w + 1, center=True).min())]
    return hi.dropna(), lo.dropna()


def _cluster(levels, tol):
    lv = sorted(levels)
    out = []
    for x in lv:
        if out and abs(x - out[-1][0]) <= tol:
            n = out[-1][1] + 1
            out[-1] = ((out[-1][0] * out[-1][1] + x) / n, n)
        else:
            out.append((x, 1))
    return out


def analyze(t):
    px = data.prices(t)
    if px is None or len(px) < 220:
        return None
    px = px.iloc[-400:]
    c, h, l = px["Close"], px["High"], px["Low"]
    s20, s50, s200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=c.index).ewm(alpha=1 / 14, adjust=False).mean()
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=c.index).ewm(alpha=1 / 14, adjust=False).mean()
    pdi, ndi = 100 * pdm / atr, 100 * ndm / atr
    adx = (100 * (pdi - ndi).abs() / (pdi + ndi)).ewm(alpha=1 / 14, adjust=False).mean()
    d = c.diff()
    rsi = 100 - 100 / (1 + d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean() / (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean())
    bbw = 4 * c.rolling(20).std() / s20
    bbw_rank = float((bbw.iloc[-252:] < bbw.iloc[-1]).mean())
    last = float(c.iloc[-1])
    a = float(atr.iloc[-1])
    slope50 = float(s50.iloc[-1] / s50.iloc[-11] - 1)
    hi, lo = _pivots(h.iloc[-180:], l.iloc[-180:])
    levels = _cluster(list(hi.values) + list(lo.values), 0.6 * a)
    sup = sorted([(x, n) for x, n in levels if x < last * 0.997], key=lambda z: -z[0])[:3]
    res = sorted([(x, n) for x, n in levels if x > last * 1.003], key=lambda z: z[0])[:3]
    don20h, don20l = float(h.iloc[-21:-1].max()), float(l.iloc[-21:-1].min())
    don55h, don55l = float(h.iloc[-56:-1].max()), float(l.iloc[-56:-1].min())
    adxv = float(adx.iloc[-1])
    if bbw_rank <= 0.15 and adxv < 20:
        phase = "Squeeze"
    elif last > s50.iloc[-1] and last > s200.iloc[-1] and slope50 > 0 and adxv >= 18:
        phase = "Aufwärtstrend"
    elif last < s50.iloc[-1] and last < s200.iloc[-1] and slope50 < 0 and adxv >= 18:
        phase = "Abwärtstrend"
    else:
        phase = "Seitwärts"
    notes = []
    if last > don55h:
        notes.append("Ausbruch über 55-Tage-Hoch")
    elif last > don20h:
        notes.append("Ausbruch über 20-Tage-Hoch")
    if last < don55l:
        notes.append("Bruch unter 55-Tage-Tief")
    elif last < don20l:
        notes.append("Bruch unter 20-Tage-Tief")
    if rsi.iloc[-1] > 70:
        notes.append(f"überkauft (RSI {rsi.iloc[-1]:.0f})")
    if rsi.iloc[-1] < 30:
        notes.append(f"überverkauft (RSI {rsi.iloc[-1]:.0f})")
    hist = px.iloc[-250:]
    return {"ticker": t, "close": last, "phase": phase, "adx": adxv, "rsi14": float(rsi.iloc[-1]),
            "sma20": float(s20.iloc[-1]), "sma50": float(s50.iloc[-1]), "sma200": float(s200.iloc[-1]),
            "slope50_10d": slope50, "atr": a, "atr_pct": a / last, "bbw_rank": bbw_rank,
            "ret20": float(c.iloc[-1] / c.iloc[-21] - 1), "supports": [[round(x, 2), n] for x, n in sup],
            "resistances": [[round(x, 2), n] for x, n in res], "don20": [don20l, don20h], "don55": [don55l, don55h],
            "notes": notes,
            "chart": {"d": [str(x.date()) for x in hist.index],
                      "o": [round(float(x), 2) for x in hist["Open"].values], "h": [round(float(x), 2) for x in hist["High"].values],
                      "l": [round(float(x), 2) for x in hist["Low"].values], "c": [round(float(x), 2) for x in hist["Close"].values],
                      "s50": [round(float(x), 2) if x == x else None for x in s50.iloc[-250:].values],
                      "s200": [round(float(x), 2) if x == x else None for x in s200.iloc[-250:].values]}}


def judge(c, pa, active_strats, pat=None):
    """Eignung der Struktur + Strike-Lage zu Unterstützung/Widerstand + Chartmuster (Agent 8); ggf. Alternative."""
    fit = FIT[pa["phase"]].get(c["strategy"], 0)
    reasons = [f"Phase: {pa['phase']} (ADX {pa['adx']:.0f}, RSI {pa['rsi14']:.0f})"]
    bias = {"bull": 1, "bear": -1}.get(c.get("bias"), 0)
    for e in (pat or {}).get("recent", []):
        if e["status"] in ("bestätigt", "Kandidat") and bias:
            w = 1.0 if e["status"] == "bestätigt" else 0.5
            if e["dir"] == bias:
                fit += w
                reasons.append(f"Muster {e['label']} ({e['status']}) stützt die Richtung")
            else:
                fit -= w
                reasons.append(f"Muster {e['label']} ({e['status']}) spricht dagegen")
        elif e["status"] in ("bestätigt", "Kandidat") and not bias and e["age"] <= 1:
            fit -= 0.5
            reasons.append(f"Frisches Richtungsmuster {e['label']} ({e['status']}) – Gefahr für neutrale Struktur")
    sup = pa["supports"][0][0] if pa["supports"] else None
    res = pa["resistances"][0][0] if pa["resistances"] else None
    for l in c.get("legs") or []:
        if l["qty"] >= 0:
            continue
        if l["cp"] == "P" and sup:
            if l["strike"] <= sup:
                fit += 0.5
                reasons.append(f"Short-Put {l['strike']:g} unter Unterstützung {sup:g}")
            else:
                fit -= 0.5
                reasons.append(f"Short-Put {l['strike']:g} über der nächsten Unterstützung {sup:g}")
        if l["cp"] == "C" and res:
            if l["strike"] >= res:
                fit += 0.5
                reasons.append(f"Short-Call {l['strike']:g} über Widerstand {res:g}")
            else:
                fit -= 0.5
                reasons.append(f"Short-Call {l['strike']:g} unter dem nächsten Widerstand {res:g}")
    reasons += pa["notes"]
    alt = None
    best = max(active_strats, key=lambda s: FIT[pa["phase"]].get(s, -9)) if active_strats else None
    if best and best != c["strategy"] and FIT[pa["phase"]].get(best, 0) - FIT[pa["phase"]].get(c["strategy"], 0) >= 2:
        alt = {"strategy": best, "name": backtest.STRATS[best]["name"],
               "why": f"passt besser zur Phase „{pa['phase']}“"}
    verdict = "passt" if fit >= 1 else ("neutral" if fit >= -0.5 else "passt nicht")
    return {"fit": fit, "verdict": verdict, "reasons": reasons, "alternative": alt}


def run():
    a3 = jload("a3_live.json", {"candidates": []})
    a8 = jload("a8_patterns.json", {"now": {}})
    a1 = jload("a1_triggers.json", {"triggers": []})
    active = sorted({t["strategy"] for t in a1["triggers"] if t.get("status") in ("bestätigt", "Kandidat")})
    tick = sorted({c["ticker"] for c in a3["candidates"] if c.get("legs")} | {"SPY", "QQQ", "IWM"})
    pa = {}
    for t in tick:
        try:
            r = analyze(t)
            if r:
                pa[t] = r
        except Exception as e:
            print("pa", t, e)
    judged = []
    for c in a3["candidates"]:
        if c["ticker"] in pa and c.get("legs"):
            judged.append({"ticker": c["ticker"], "trigger": c["trigger"], "strategy": c["strategy"],
                           **judge(c, pa[c["ticker"]], active, a8["now"].get(c["ticker"]))})
    out = {"day": a3.get("day"), "tickers": pa, "judgements": judged}
    jdump(out, "a4_price.json")
    return out


if __name__ == "__main__":
    o = run()
    for j in o["judgements"][:20]:
        print(j["ticker"], j["strategy"], j["verdict"], j["fit"], j["alternative"])
