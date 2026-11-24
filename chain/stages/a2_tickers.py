"""Agent 2 – Ticker-Regeln: Auf welchen Tickern funktionieren die Trigger von Agent 1?

Für jeden bestätigten Trigger bzw. Kandidaten:
  1. Literatur-Regeln (chain_lab/research/ticker_rules.yaml) Out-of-Sample gegen den reinen Trigger testen
  2. datengetriebene Regeln: je Merkmal das beste Terzil In-Sample wählen, Out-of-Sample prüfen (BH-korrigiert)
  3. angenommene Regeln kombinieren (gierig, nur wenn OOS besser)
  4. Ticker-Ranking mit Schrumpfung zum Gesamtmittel (empirisches Bayes)
Ausgabe: chain_lab/out/a2_tickers.json
"""
import numpy as np
import pandas as pd

from chain.core import stats
from chain.stages.common import CFG, all_features, all_trades, jdump, jload, with_features, yload

RC = CFG["research"]
DATA_FEATS = ["spr_atm", "iv_rank", "ivrv", "rv20", "atr_pct", "adx14", "dist200", "dist50", "skew25", "slope",
              "ff", "iv30", "bbw_rank", "d2e", "vix", "rsi14", "ret20"]
SHRINK_K = 8


def _m(df, expr):
    return df.eval(expr).fillna(False).astype(bool)


def _cmp(Xs, base_m, rule_m, split):
    e = pd.DatetimeIndex(Xs["entry"])
    isd, oos = e < split, e >= split
    a_is, a_oos = Xs.loc[base_m & isd, "ret"], Xs.loc[base_m & oos, "ret"]
    b = base_m & rule_m
    r_is, r_oos = stats.summarize(Xs[b & isd]), stats.summarize(Xs[b & oos])
    return {"is": r_is, "oos": r_oos,
            "is_gain": (r_is.get("mean", np.nan) - a_is.mean()) if r_is.get("n") else None,
            "oos_gain": (r_oos.get("mean", np.nan) - a_oos.mean()) if r_oos.get("n") else None,
            "kept_share": float(b.sum() / max(base_m.sum(), 1))}


def _accept(c, min_n):
    return (c["oos"].get("n", 0) >= min_n and (c["is_gain"] or -1) > 0 and (c["oos_gain"] or -1) > 0
            and c["oos"].get("mean", -1) > 0)


def ticker_rank(Xs, m):
    sel = Xs[m]
    if len(sel) == 0:
        return []
    pooled = sel["ret"].mean()
    g = sel.groupby("ticker")["ret"].agg(["count", "mean", lambda x: (x > 0).mean()])
    g.columns = ["n", "mean", "win"]
    g["score"] = (g["n"] * g["mean"] + SHRINK_K * pooled) / (g["n"] + SHRINK_K)
    g = g.sort_values("score", ascending=False)
    return [{"ticker": t, "n": int(r.n), "mean": float(r["mean"]), "win": float(r.win), "score": float(r.score)}
            for t, r in g.iterrows()]


def run():
    a1 = jload("a1_triggers.json")
    rules = yload("ticker_rules.yaml", [])
    feats = all_features()
    X = with_features(all_trades(), feats)
    split = pd.Timestamp(a1["split"])
    out = []
    for tr in a1["triggers"]:
        if tr.get("status") not in ("bestätigt", "Kandidat"):
            continue
        Xs = X[X.strat == tr["strategy"]].reset_index(drop=True)
        base = _m(Xs, tr["when"])
        min_n = max(15, RC["min_oos_trades"] // 2)
        lit, data_r = [], []
        for r in rules:
            ap = r.get("applies", "all")
            if ap != "all" and tr["strategy"] not in ap:
                continue
            try:
                rm = _m(Xs, r["rule"])
            except Exception as e:
                lit.append({**r, "error": str(e)})
                continue
            c = _cmp(Xs, base, rm, split)
            lit.append({"id": r["id"], "name": r["name"], "rule": r["rule"], "source": r.get("source"),
                        **c, "accepted": _accept(c, min_n)})
        # datengetrieben: bestes Terzil je Merkmal (Grenzen nur In-Sample)
        isd = pd.DatetimeIndex(Xs["entry"]) < split
        pv = []
        for f in DATA_FEATS:
            if f not in Xs:
                continue
            v = Xs.loc[base & isd, f].dropna()
            if len(v) < 60 or v.nunique() < 5:
                continue
            q1, q2 = v.quantile([1 / 3, 2 / 3])
            opts = {f"{f} <= {q1:.4g}": f"{f} <= {q1:.6g}", f"{f} > {q1:.4g} and {f} <= {q2:.4g}": f"{f} > {q1:.6g} and {f} <= {q2:.6g}",
                    f"{f} > {q2:.4g}": f"{f} > {q2:.6g}"}
            best = None
            for label, expr in opts.items():
                c = _cmp(Xs, base, _m(Xs, expr), split)
                if (c["is"].get("t") or -9) > (best[1]["is"].get("t") or -9 if best else -9):
                    best = (expr, c, label)
            if best:
                expr, c, label = best
                data_r.append({"feature": f, "rule": expr, "label": label, **c})
                pv.append(c["oos"].get("p", 1.0) if c["oos"].get("n") else 1.0)
        sig = stats.bh(pv, RC["fdr_q"]) if pv else []
        for d, s in zip(data_r, sig):
            d["bh"] = bool(s)
            d["accepted"] = bool(s) and _accept(d, min_n)
        # gierig kombinieren: Literatur zuerst, dann Daten; nur behalten, wenn OOS-Mittel steigt
        cand = [r for r in lit if r.get("accepted")] + [d for d in data_r if d.get("accepted")]
        cand.sort(key=lambda r: -(r["oos_gain"] or 0))
        final_m, final_rules = base.copy(), []
        cur = stats.summarize(Xs[final_m & ~isd])
        for r in cand:
            m2 = final_m & _m(Xs, r["rule"])
            s2 = stats.summarize(Xs[m2 & ~isd])
            if s2.get("n", 0) >= min_n and s2["mean"] > cur.get("mean", -9) and stats.summarize(Xs[m2 & isd]).get("mean", -1) > 0:
                final_m, cur = m2, s2
                final_rules.append(r["rule"])
        final = stats.walk_forward(Xs, final_m, split)
        out.append({
            "trigger": tr["id"], "trigger_name": tr["name"], "strategy": tr["strategy"], "when": tr["when"],
            "trigger_status": tr["status"], "lit_rules": lit, "data_rules": data_r, "final_rules": final_rules,
            "final_expr": " and ".join([f"({tr['when']})"] + [f"({r})" for r in final_rules]),
            "final": final, "tickers": ticker_rank(Xs, final_m),
        })
    jdump({"split": a1["split"], "triggers": out}, "a2_tickers.json")
    return out


if __name__ == "__main__":
    for r in run():
        f = r["final"]["oos"]
        print(r["trigger"], r["final_rules"], "OOS n", f.get("n"), "mean", f.get("mean"), "t", f.get("t"))
