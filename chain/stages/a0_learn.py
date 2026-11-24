"""Supervisor (Agent 0) – Lernmodul: das Modell lernt automatisch aus den Papier-Trades.

Jede Options-Idee wird beim Einstieg mit den Faktoren gespeichert, die ihre Bewertung (Score von Agent 3) ausgemacht
haben (Liquidität, Trigger-Stufe, Gamma-Regime, Wall-Lage, Termine, SEC, Earnings, je Trigger, je Strategie …).
Nach dem Schließen ist bekannt, was der Trade gebracht hat (Rendite auf Risiko). Daraus kalibriert das Lernmodul die
Gewichte der Faktoren – so steigt mit der Zeit die Treffsicherheit der Freigaben und die Rendite.

Regeln (fest, damit Lernen nicht in Überanpassung kippt):
  - Erst ab 40 geschlossenen Options-Papier-Trades; je Faktor ≥ 15 Trades mit und ≥ 15 ohne Faktor.
  - Wirkung je Faktor = Ø Rendite mit − ohne Faktor, Welch-t. Nur |t| ≥ 1,5 verändert ein Gewicht.
  - Neues Gewicht = Startwert + 0,5 × t (begrenzt auf ±3) × n/(n+30); Grenzen Startwert ± 2.
  - Automatische Übernahme nur, wenn die neuen Gewichte auf den jüngsten 30 % der Trades (nicht zum Lernen benutzt)
    besser sortieren: höhere Rangkorrelation Score↔Rendite UND Ø Rendite der dann freigegebenen Trades ≥ vorher.
  - Feste Sperren lernt das Modul NIE weg: schwache Liquidität, Erwartungswert ≤ 0, Agent-9-Sperren, Risiko-Budget,
    Edge-Regeln (bestätigt/Kandidat), Regime-Sperren. Strukturelle Änderungen bleiben Vorschläge an Markus.
  - Aktien-Ideen: Quelle (Signal) mit Live-Ø < 0 und t ≤ −2 bei ≥ 20 Trades → automatisch halbe Größe; t ≤ −3 → pausiert.
Ausgabe: chain_lab/research/weights.yaml (Gewichte + Verlauf), chain_lab/out/a0_learn.json (Lernkurve, Wirkung je Faktor)
"""
import datetime as dt
import math

import numpy as np
import pandas as pd
import yaml

from chain.stages.common import RES, jdump, jload, yload

PRIORS = {"liq_ok": 1, "trig_A": 2, "trig_B": 0, "gex_neg_sv": -1, "gex_pos_sv": 1, "pwall_ok": 0.5, "pwall_above": 0,
          "cwall_ok": 0.5, "cwall_below": 0, "gex_neg_lv": 1, "gex_pos_lv": 0, "event_warn": -1, "event_in_life": 0,
          "event_near": 0, "sec_confirm": 1, "sec_against": -1, "earn_in": -2}
MIN_TOTAL, MIN_EACH, T_MIN = 40, 15, 1.5


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return np.nan
    va, vb = np.var(a, ddof=1) / len(a), np.var(b, ddof=1) / len(b)
    return (np.mean(a) - np.mean(b)) / math.sqrt(va + vb) if va + vb > 0 else np.nan


def spearman(x, y):
    if len(x) < 5:
        return np.nan
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def frame(closed):
    rows = []
    for x in closed:
        if x.get("kind") != "paper" or x.get("ror") is None or not isinstance(x.get("factors"), dict):
            continue
        f = dict(x["factors"])
        f[f"trig:{x['trigger']}"] = f.get(f"trig:{x['trigger']}", 0)
        f[f"strat:{x['strategy']}"] = f.get(f"strat:{x['strategy']}", 0)
        rows.append({"closed": x["closed"], "opened": x["opened"], "ror": float(x["ror"]), "score": x.get("score") or 0,
                     "verdict": x.get("a3_verdict") or x.get("verdict"), "top": bool(x.get("top")), "f": f})
    return pd.DataFrame(rows).sort_values("closed").reset_index(drop=True) if rows else pd.DataFrame()


def fit(df, cur):
    """Neue Gewichte aus df (Trades) ausgehend von den aktuellen Gewichten."""
    keys = sorted({k for f in df["f"] for k in f})
    new, eff = dict(cur), {}
    for k in keys:
        has = df["f"].map(lambda f: k in f).values
        a, b = df.loc[has, "ror"].values, df.loc[~has, "ror"].values
        t = welch(a, b)
        eff[k] = {"n_with": int(has.sum()), "n_without": int((~has).sum()), "mean_with": float(a.mean()) if len(a) else None,
                  "mean_without": float(b.mean()) if len(b) else None, "t": None if t != t else round(float(t), 2)}
        prior = PRIORS.get(k, 0)
        if len(a) >= MIN_EACH and len(b) >= MIN_EACH and t == t and abs(t) >= T_MIN:
            w = prior + 0.5 * max(-3, min(3, t)) * len(a) / (len(a) + 30)
            new[k] = round(max(prior - 2, min(prior + 2, w)), 2)
    return new, eff


def rescore(df, w_new):
    """Score neu berechnen: alter Score − alte Gewichte + neue Gewichte der beim Einstieg vorhandenen Faktoren."""
    out = []
    for _, r in df.iterrows():
        s = r["score"]
        for k, w_old in r["f"].items():
            s += w_new.get(k, PRIORS.get(k, 0)) - (w_old or 0)
        out.append(s)
    return np.array(out)


def curve(closed):
    """Lernkurve: Trefferquote und Ø Rendite je Monat (alle Papier-Trades, freigegebene, angezeigte Top-Ideen, Aktien)."""
    df = pd.DataFrame([x for x in closed if x.get("ror") is not None])
    if df.empty:
        return []
    df["m"] = pd.to_datetime(df["closed"]).dt.to_period("M").astype(str)
    out = []
    for m, g in df.groupby("m"):
        o = g[g["kind"] == "paper"]
        fr = o[o["verdict"] == "freigegeben"] if "verdict" in o.columns else o.iloc[0:0]
        tp = o[o["top"].fillna(False).astype(bool)] if "top" in o.columns else o.iloc[0:0]
        e = g[g["kind"] == "paper_equity"]

        def st(x):
            return {"n": int(len(x)), "win": round(float((x["ror"] > 0).mean()), 3) if len(x) else None,
                    "ror": round(float(x["ror"].mean()), 4) if len(x) else None}
        out.append({"month": m, "all": st(o), "freigegeben": st(fr), "top": st(tp), "equity": st(e)})
    return out


def run():
    book = jload("../track/paper.json", None) or {}
    closed = book.get("closed", [])
    W = yload("weights.yaml", {}) or {}
    weights = W.get("weights") or {k: {"w": v, "prior": v} for k, v in PRIORS.items()}
    hist = W.get("history", [])
    cur = {k: v["w"] for k, v in weights.items()}
    df = frame(closed)
    today = dt.date.today().isoformat()
    res = {"date": today, "n_closed_options": int(len(df)), "n_closed_all": len(closed), "min_total": MIN_TOTAL,
           "applied": False, "note": ""}
    eff = {}
    if len(df) < MIN_TOTAL:
        res["note"] = f"Lernen startet ab {MIN_TOTAL} geschlossenen Options-Papier-Trades (aktuell {len(df)})."
    else:
        cut = int(len(df) * 0.7)
        train, hold = df.iloc[:cut], df.iloc[cut:]
        w_try, _ = fit(train, cur)
        s_old, s_new = rescore(hold, cur), rescore(hold, w_try)
        rc_old, rc_new = spearman(s_old, hold["ror"]), spearman(s_new, hold["ror"])
        fr_old = hold["ror"][s_old >= 2].mean() if (s_old >= 2).any() else np.nan
        fr_new = hold["ror"][s_new >= 2].mean() if (s_new >= 2).any() else np.nan
        better = (rc_new == rc_new and rc_old == rc_old and rc_new > rc_old + 0.02 and
                  (fr_new != fr_new or fr_old != fr_old or fr_new >= fr_old))
        w_all, eff = fit(df, cur)
        changed = {k: (cur.get(k, PRIORS.get(k, 0)), w_all[k]) for k in w_all if abs(w_all[k] - cur.get(k, PRIORS.get(k, 0))) >= 0.05}
        val = {"holdout_n": int(len(hold)), "rank_corr_old": None if rc_old != rc_old else round(rc_old, 3),
               "rank_corr_new": None if rc_new != rc_new else round(rc_new, 3),
               "freigegeben_ror_old": None if fr_old != fr_old else round(float(fr_old), 4),
               "freigegeben_ror_new": None if fr_new != fr_new else round(float(fr_new), 4)}
        res["validation"] = val
        if changed and better:
            for k, (a, b) in changed.items():
                weights.setdefault(k, {"prior": PRIORS.get(k, 0)})["w"] = b
            hist.insert(0, {"date": today, "n": int(len(df)), "changes": {k: [a, b] for k, (a, b) in changed.items()}, **val})
            res["applied"] = True
            res["note"] = f"{len(changed)} Gewicht(e) automatisch angepasst (Validierung auf {len(hold)} jüngsten Trades bestanden)."
        elif changed:
            res["note"] = "Neue Gewichte hätten die jüngsten Trades nicht besser sortiert – nicht übernommen."
        else:
            res["note"] = "Keine Faktorwirkung stark genug (|t| ≥ 1,5) – Gewichte unverändert."
    for k, e in eff.items():
        if k in weights:
            weights[k].update({"n": e["n_with"], "t": e["t"]})
    # Aktien-Quellen: Live-Leistung → Größenfaktor
    eq = pd.DataFrame([x for x in closed if x.get("kind") == "paper_equity" and x.get("ror") is not None])
    src = {}
    if len(eq):
        for s, g in eq.groupby("trigger"):
            r = g["ror"].values
            t = r.mean() / (r.std(ddof=1) / math.sqrt(len(r))) if len(r) > 2 and r.std(ddof=1) > 0 else np.nan
            mult = 1.0
            if len(r) >= 20 and r.mean() < 0 and t == t:
                mult = 0.0 if t <= -3 else (0.5 if t <= -2 else 1.0)
            src[s] = {"n": int(len(r)), "mean_R": round(float(r.mean()), 3), "win": round(float((r > 0).mean()), 3),
                      "t": None if t != t else round(float(t), 2), "size_mult": mult}
    W = {"updated": today, "weights": weights, "equity_sources": src, "history": hist[:50]}
    yaml.safe_dump(W, open(f"{RES}/weights.yaml", "w"), allow_unicode=True, sort_keys=False)
    # Verbesserung seit Lernbeginn messbar?
    first = hist[-1]["date"] if hist else None
    imp = None
    if first and len(df):
        b, a = df[df["opened"] < first], df[df["opened"] >= first]
        fb, fa = b[b["verdict"] == "freigegeben"], a[a["verdict"] == "freigegeben"]
        if len(fb) >= 10 and len(fa) >= 10:
            t = welch(fa["ror"].values, fb["ror"].values)
            imp = {"since": first, "before": {"n": len(fb), "win": round(float((fb["ror"] > 0).mean()), 3), "ror": round(float(fb["ror"].mean()), 4)},
                   "after": {"n": len(fa), "win": round(float((fa["ror"] > 0).mean()), 3), "ror": round(float(fa["ror"].mean()), 4)},
                   "t": None if t != t else round(float(t), 2)}
    res.update(effects=eff, weights={k: v for k, v in weights.items()}, equity_sources=src, history=hist[:20],
               curve=curve(closed), improvement=imp)
    jdump(res, "a0_learn.json")
    return res


if __name__ == "__main__":
    o = run()
    print(o["note"], "| Trades:", o["n_closed_options"])
