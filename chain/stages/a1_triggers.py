"""Agent 1 – Trigger-Backtests: Wann lohnt sich ein Optionstrade?

Liest chain_lab/research/triggers.yaml (von Agent 1 gepflegt), testet jeden Trigger gegen die unbedingte Strategie
im Walk-forward (Schwellen nur In-Sample gewählt), korrigiert für Mehrfachtests (Bonferroni im Grid,
Benjamini-Hochberg über alle Trigger) und schreibt chain_lab/out/a1_triggers.json.
"""
import itertools

import numpy as np
import pandas as pd

from chain.core import backtest, stats
from chain.stages.common import CFG, all_features, all_trades, jdump, with_features, yload

RC = CFG["research"]


def _mask(df, expr):
    if "fut_" in expr:
        raise ValueError("Zukunftsmerkmal (fut_*) im Trigger – Lookahead verboten")
    try:
        m = df.eval(expr)
        return m.fillna(False).astype(bool)
    except Exception as e:
        raise ValueError(f"Ausdruck '{expr}': {e}")


def _grid(tr):
    g = tr.get("grid") or {}
    keys = list(g)
    if not keys:
        return [{}]
    return [dict(zip(keys, v)) for v in itertools.product(*[g[k] for k in keys])]


def regime_table(X, mask):
    """Mittlere Rendite je Marktregime (für Agent 5)."""
    sel = X[mask]
    if len(sel) == 0:
        return {}
    out = {}
    buckets = {
        "VIX < 15": sel.vix < 15, "VIX 15–20": (sel.vix >= 15) & (sel.vix < 20),
        "VIX 20–30": (sel.vix >= 20) & (sel.vix < 30), "VIX ≥ 30": sel.vix >= 30,
        "VIX-Kurve invertiert": sel.vix_ts > 1.0, "SPY unter SMA200": sel.spy_dist200 < 0,
        "SPY > 10 % unter Hoch": sel.spy_dd52 < -0.10,
    }
    for k, m in buckets.items():
        r = sel.loc[m.fillna(False), "ret"]
        out[k] = {"n": int(len(r)), "mean": float(r.mean()) if len(r) else None,
                  "win": float((r > 0).mean()) if len(r) else None}
    return out


def curve(X, mask):
    sel = X[mask]
    if len(sel) == 0:
        return []
    wk = sel.groupby(pd.DatetimeIndex(sel["entry"]).to_period("W"))["ret"].mean()
    return [[str(p.start_time.date()), round(float(v), 4)] for p, v in wk.cumsum().items()]


def yearly(X, mask):
    sel = X[mask]
    return {str(y): {"n": int(len(g)), "mean": float(g["ret"].mean()), "win": float((g["ret"] > 0).mean())}
            for y, g in sel.groupby(pd.DatetimeIndex(sel["entry"]).year)}


def run():
    triggers = yload("triggers.yaml", [])
    feats = all_features()
    trades = all_trades()
    X = with_features(trades, feats)
    split = stats.split_date(X["entry"], RC["in_sample_frac"])
    latest = feats.sort_values("date").groupby("ticker").tail(1)
    results = []
    for tr in triggers:
        key = tr["strategy"]
        if key not in backtest.STRATS:
            results.append({**tr, "status": "Fehler", "error": f"unbekannte Strategie {key}"})
            continue
        Xs = X[X.strat == key].reset_index(drop=True)
        if len(Xs) == 0:
            results.append({**tr, "status": "zu wenig Daten"})
            continue
        isd = pd.DatetimeIndex(Xs["entry"]) < split
        best, best_t, trials = None, -np.inf, []
        for params in _grid(tr):
            expr = tr["when"].format(**params)
            try:
                m = _mask(Xs, expr)
            except ValueError as e:
                results.append({**tr, "status": "Fehler", "error": str(e)})
                best = "err"
                break
            s = stats.summarize(Xs[m & isd])
            trials.append({"params": params, "is": s})
            t = s.get("t") or -np.inf
            if s.get("n", 0) >= 30 and s["mean"] > 0 and t > best_t:
                best, best_t = (params, expr, m), t
        if best == "err":
            continue
        if best is None:  # kein positives In-Sample -> trotzdem erste Variante zeigen
            params = _grid(tr)[0]
            expr = tr["when"].format(**params)
            best = (params, expr, _mask(Xs, expr))
        params, expr, m = best
        res = stats.walk_forward(Xs, m, split)
        p = res["oos"].get("p", 1.0)
        res["p_adj"] = min(1.0, p * len(trials))  # Bonferroni über das Grid
        now = _mask(latest, expr) if len(latest) else pd.Series(dtype=bool)
        # P002: dieselbe Bedingung auf den Flügel-Varianten (Kennzahlen für den tatsächlich gehandelten Live-Trade)
        variants = {}
        for v, base_k in backtest.VARIANTS.items():
            if base_k != key:
                continue
            Xv = X[X.strat == v].reset_index(drop=True)
            if len(Xv) == 0:
                continue
            rv = stats.walk_forward(Xv, _mask(Xv, expr), split)
            variants[v] = {"name": backtest.STRATS[v]["name"], "wing": backtest.STRATS[v]["wing"],
                           **{k: rv[k] for k in ("is", "oos", "oos_stress", "base_oos")}}
        results.append({
            "id": tr["id"], "name": tr["name"], "strategy": key, "strategy_name": backtest.STRATS[key]["name"],
            "bias": backtest.STRATS[key]["bias"], "vol": backtest.STRATS[key]["vol"],
            "when": expr, "params": params, "grid_trials": trials, "source": tr.get("source"),
            "mechanism": tr.get("mechanism"), "added": str(tr.get("added", "")),
            **res, "edge_vs_base": (res["oos"].get("mean", np.nan) - res["base_oos"].get("mean", np.nan))
            if res["oos"].get("n") else None,
            "curve": curve(Xs, m), "curve_base": curve(Xs, pd.Series(True, index=Xs.index)),
            "yearly": yearly(Xs, m), "regimes": regime_table(Xs, m),
            "variants": variants,
            "firing_now": sorted(latest.loc[now.values, "ticker"].tolist()) if len(now) else [],
        })
    ok = [r for r in results if "oos" in r]
    sig = stats.bh([r["p_adj"] for r in ok], RC["fdr_q"])
    for r, s in zip(ok, sig):
        r["bh"] = bool(s)
        r["status"] = stats.verdict(r, RC)
    out = {"split": str(pd.Timestamp(split).date()), "n_trades": int(len(trades)),
           "data_start": str(pd.Timestamp(trades["entry"].min()).date()), "data_end": str(pd.Timestamp(trades["entry"].max()).date()),
           "latest_feature_date": str(pd.Timestamp(feats["date"].max()).date()),
           "strategies": {k: {"name": v["name"], "bias": v["bias"], "vol": v["vol"], "variant_of": v.get("variant_of"),
                              "base": stats.summarize(trades[trades.strat == k])} for k, v in backtest.STRATS.items()},
           "triggers": results}
    jdump(out, "a1_triggers.json")
    return out


if __name__ == "__main__":
    o = run()
    for r in o["triggers"]:
        oo = r.get("oos", {})
        print(f'{r["id"]:4} {r.get("status",""):14} {r.get("when","")[:45]:45} OOS n={oo.get("n")} mean={oo.get("mean", float("nan")):.3f} t={oo.get("t")}')
