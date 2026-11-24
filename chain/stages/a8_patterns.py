"""Agent 8 – Chartmuster: erkennen, backtesten, auf „Richtung & Aktien“ anwenden.

1. Alle Detektoren (chain/core/patterns.py) über die gesamte Kurshistorie aller Ticker
2. Jedes Muster als Trade simuliert (Stop = Invalidierung, Ziel = Kursziel/2R, max. 20 Tage, Kosten 0,1 %)
3. Walk-forward (60/40), geclusterter t-Wert, 3× Kosten, BH über alle Muster, Vergleich mit demselben Mechanismus
   ohne Muster in gleicher Richtung → Status bestätigt / Kandidat / verworfen
4. Aktuelle Lage je Ticker: aktive Trendlinien, offene Gaps, frische Angebots-/Nachfragezonen/FVGs, Muster der
   letzten 5 Kerzen (mit Status) → Zeichenobjekte für den Kerzenchart, Signale für Agent 4 und 5
Ausgabe: chain_lab/out/a8_patterns.json
"""
import os
import time

import numpy as np
import pandas as pd

from chain.core import data, patterns, stats
from chain.stages.common import CACHE, CFG, jdump, yload

RC = CFG["research"]
RECENT = 5


def _date(idx, i):
    return str(idx[min(max(int(round(i)), 0), len(idx) - 1)].date())


def _draw(objs, idx):
    out = []
    n = len(idx)
    for d in objs or []:
        if d["type"] == "line":
            # zusätzlich Abstand zur letzten Kerze (in Handelstagen) – damit der Chart Linien auch außerhalb
            # des sichtbaren Zeitraums korrekt anschneidet
            out.append({**d, "p": [[_date(idx, x), round(float(y), 3)] for x, y in d["p"]],
                        "o": [int(n - 1 - round(x)) for x, _ in d["p"]]})
        elif d["type"] == "zone":
            out.append({**d, "d": _date(idx, d["i"]), "o": int(n - 1 - d["i"]), "lo": round(float(d["lo"]), 3), "hi": round(float(d["hi"]), 3)})
        elif d["type"] == "mark":
            out.append({**d, "d": _date(idx, d["i"]), "y": round(float(d["y"]), 3)})
    for d in out:
        d.pop("i", None)
    return out


def scan_all():
    p = os.path.join(CACHE, "patterns.pkl")
    stamp = str(int(os.path.getmtime(os.path.join(data.D, "prices", "SPY.csv")))) + "|" + \
        str(int(os.path.getmtime(os.path.join(data.ROOT, "chain", "core", "patterns.py"))))
    if os.path.exists(p):
        obj = pd.read_pickle(p)
        if obj.get("stamp") == stamp:
            return obj
    t0 = time.time()
    trades, base, now = [], [], {}
    for t in data.UNIV:
        px = data.prices(t)
        if px is None or len(px) < 300:
            continue
        px = px[px.index >= "2005-01-01"]
        try:
            ev, ctx, ohlca = patterns.detect(px)
        except Exception as e:
            print("patterns", t, e)
            continue
        idx = px.index
        for r in patterns.simulate(ev, ohlca):
            r.update(ticker=t, entry=idx[r["i"]])
            trades.append(r)
        for r in patterns.baseline(ohlca):
            r.update(ticker=t, entry=idx[r["i"]])
            base.append(r)
        n = len(px)
        rec = [e for e in ev if e["i"] >= n - RECENT]
        o, h, l, c, a = ohlca
        sim = {}
        for e in rec:
            d = e["dir"]
            risk = abs(c[e["i"]] - e["stop"]) if e.get("stop") is not None else 2 * a[e["i"]]
            risk = min(max(risk, 0.5 * a[e["i"]]), 4 * a[e["i"]])
            tgt = e.get("target")
            if tgt is None or d * (tgt - c[e["i"]]) <= 0.5 * risk:
                tgt = c[e["i"]] + d * 2 * risk
            sim[id(e)] = (c[e["i"]] - d * risk, tgt)
        now[t] = {"recent": [{"p": e["p"], "label": e["label"], "dir": e["dir"], "date": _date(idx, e["i"]),
                              "age": int(n - 1 - e["i"]), "entry": round(float(c[e["i"]]), 3),
                              "stop": round(float(sim[id(e)][0]), 3), "target": round(float(sim[id(e)][1]), 3),
                              "draw": _draw(e.get("draw"), idx)} for e in rec],
                  "trendlines": _draw(ctx["trendlines"], idx), "gaps": _draw(ctx["gaps"], idx), "zones": _draw(ctx["zones"], idx),
                  "last": str(idx[-1].date())}
    obj = {"stamp": stamp, "trades": pd.DataFrame(trades), "base": pd.DataFrame(base), "now": now}
    pd.to_pickle(obj, p)
    print(f"Chartmuster: {len(trades)} Ereignisse in {time.time()-t0:.0f}s")
    return obj


def evaluate(T, B):
    T = T.copy()
    B = B.copy()
    for df in (T, B):
        df["ret"] = df["R"]                                    # Kennzahl = R-Multiple
        df["ret_stress"] = df["R"] - 2 * 0.001 / df["rf"]     # zusätzliche 2× Kosten in R
    split = stats.split_date(T["entry"], RC["in_sample_frac"])
    lib = {x["id"]: x for x in (yload("chart_patterns.yaml", []) or [])}
    res = []
    for p, g in T.groupby("p"):
        if p in lib and lib[p].get("enabled") is False:
            continue
        d = int(g["dir"].iloc[0])
        b = B[B["p"] == ("base_long" if d > 0 else "base_short")]
        g = g.reset_index(drop=True)
        r = stats.walk_forward(g, pd.Series(True, index=g.index), split)
        r["base_oos"] = stats.summarize(b[pd.DatetimeIndex(b["entry"]) >= split])
        r["base_is"] = stats.summarize(b[pd.DatetimeIndex(b["entry"]) < split])
        oo = g[pd.DatetimeIndex(g["entry"]) >= split]
        r["fill_or_target_rate"] = float((oo["reason"] == "Ziel").mean()) if len(oo) else None
        r["fwd10_oos"] = float(oo["fwd10"].mean()) if len(oo) else None
        meta = lib.get(p, {})
        res.append({"id": p, "name": meta.get("name", p), "family": meta.get("family", ""), "source": meta.get("source", ""),
                    "mechanism": meta.get("mechanism", ""), "dir": d, **r,
                    "edge_vs_base": (r["oos"].get("mean", np.nan) - r["base_oos"].get("mean", np.nan)) if r["oos"].get("n") else None,
                    "yearly": {str(y): float(x["R"].mean()) for y, x in g.groupby(pd.DatetimeIndex(g["entry"]).year)}})
    # Status über den MEHRWERT gegenüber demselben Mechanismus ohne Muster (Long-Muster profitieren sonst nur von der
    # allgemeinen Aufwärtsdrift): t-Wert der Differenz, BH über alle Muster, Absolutwert muss ebenfalls > 0 sein.
    for x in res:
        g = T[T["p"] == x["id"]]
        oo = g[pd.DatetimeIndex(g["entry"]) >= pd.Timestamp(split)]
        bm = x["base_oos"].get("mean", 0) or 0
        if len(oo) >= 10:
            diff = oo["R"].values - bm
            te = stats.cluster_t(diff, pd.DatetimeIndex(oo["entry"]).to_period("M").astype(str))
            mid = stats.split_date(oo["entry"], 0.5)
            h1 = oo[pd.DatetimeIndex(oo["entry"]) < mid]["R"].mean() - bm
            h2 = oo[pd.DatetimeIndex(oo["entry"]) >= mid]["R"].mean() - bm
        else:
            te, h1, h2 = np.nan, np.nan, np.nan
        x["edge_t"] = float(te) if te == te else None
        x["edge_p"] = stats.p_one_sided(te)
        x["edge_h1"], x["edge_h2"] = (float(h1) if h1 == h1 else None), (float(h2) if h2 == h2 else None)
    sig = stats.bh([x["edge_p"] for x in res], RC["fdr_q"])
    for x, s in zip(res, sig):
        x["bh"] = bool(s)
        o = x["oos"]
        e, et = x.get("edge_vs_base") or -1, x.get("edge_t") or 0
        if o.get("n", 0) < 60:
            x["status"] = "zu wenig Daten"
        elif o["mean"] > 0 and e > 0 and et >= RC["min_oos_t"] and s and (x["edge_h1"] or -1) > 0 and (x["edge_h2"] or -1) > 0 \
                and x["oos_stress"].get("mean", -1) > 0:
            x["status"] = "bestätigt"
        elif o["mean"] > 0 and e > 0 and et >= 1.0:
            x["status"] = "Kandidat"
        else:
            x["status"] = "verworfen"
        # P004 (freigegeben 23.11.2026): handelbar erst ab absolutem OOS-Ø ≥ +0,10 R je Trade, sonst nur Anzeige im Chart
        if x["status"] in ("bestätigt", "Kandidat") and o["mean"] < RC.get("pattern_min_R", 0.10):
            x["status_note"] = f"Mehrwert vorhanden, aber OOS Ø {o['mean']:+.3f} R < 0,10 R (P004) – nur Anzeige"
            x["status"] = "beobachten"
    order = {"bestätigt": 0, "Kandidat": 1, "beobachten": 2, "zu wenig Daten": 3, "verworfen": 4}
    res.sort(key=lambda x: (order.get(x["status"], 9), -(x.get("edge_vs_base") or -9)))
    return res, str(pd.Timestamp(split).date())


def run():
    obj = scan_all()
    res, split = evaluate(obj["trades"], obj["base"])
    status = {x["id"]: x["status"] for x in res}
    stat_by = {x["id"]: x for x in res}
    now = obj["now"]
    signals = []
    for t, v in now.items():
        for e in v["recent"]:
            e["status"] = status.get(e["p"], "–")
            s = stat_by.get(e["p"], {})
            e["oos_R"] = s.get("oos", {}).get("mean")
            e["oos_win"] = s.get("oos", {}).get("win")
            if e["age"] == 0 and e["status"] in ("bestätigt", "Kandidat"):
                signals.append({"ticker": t, **{k: e[k] for k in ("p", "label", "dir", "entry", "stop", "target", "status", "oos_R", "oos_win")}})
    out = {"split": split, "n_events": int(len(obj["trades"])), "patterns": res, "now": now, "signals": signals,
           "counts": {k: sum(1 for x in res if x["status"] == k) for k in ("bestätigt", "Kandidat", "beobachten", "verworfen", "zu wenig Daten")}}
    jdump(out, "a8_patterns.json")
    return out


if __name__ == "__main__":
    o = run()
    print(o["counts"], "Signale heute:", len(o["signals"]))
    for x in o["patterns"]:
        oo, b = x["oos"], x["base_oos"]
        print(f'{x["id"]:22} {x["status"]:14} OOS n={oo.get("n")} R={oo.get("mean", float("nan")):+.3f} base={b.get("mean", float("nan")):+.3f} Mehrwert={x.get("edge_vs_base") or 0:+.3f} t={x.get("edge_t")}')
