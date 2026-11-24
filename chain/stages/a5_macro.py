"""Agent 5 – Großer Überblick: Passt das Marktumfeld zu den Strategien? Gibt es bessere Aktien-Trades?

1. Marktregime aus VIX, VIX-Kurve, SPY-Trend/Drawdown, Marktbreite, High-Yield-Spreads, Zinskurve, NFCI
2. Historische Rendite jeder Strategie je Regime (aus allen Backtests) → Regime-Filter für die Trades von Agent 3
3. Aktien-Alternative je Optionsidee: gleicher Trigger, aber Aktie long/short mit Stop (1,5 ATR) – R-Multiple
   im Backtest gegen die Options-Rendite (Return on Risk) verglichen
4. Eigene Aktien-Signale aus Optionsdaten (chain_lab/research/equity_triggers.yaml), Walk-forward + BH
Ausgabe: chain_lab/out/a5_macro.json
"""
import os

import numpy as np
import pandas as pd

from chain.core import backtest, data, features, stats
from chain.stages.common import CACHE, CFG, all_features, all_trades, jdump, jload, with_features, yload

RC = CFG["research"]
COST = 0.001  # Aktien: Kosten + Slippage je Trade (10 bp)


def classify(m):
    """Regime je Zeile (Series oder DataFrame)."""
    def one(r):
        vix, ts, dd, d200 = r.get("vix", np.nan), r.get("vix_ts", np.nan), r.get("spy_dd52", np.nan), r.get("spy_dist200", np.nan)
        br, hy = r.get("breadth200", np.nan), r.get("hy_chg20", np.nan)
        if (vix >= 30) or (ts > 1.05 and dd < -0.08) or (hy > 1.0):
            return "Crash/Stress"
        if dd < -0.05 and (vix >= 20 or ts > 1.0):
            return "Korrektur"
        if d200 < 0 and (br < 0.4 if br == br else True):
            return "Bärenmarkt"
        if d200 > 0 and vix >= 18:
            return "Volatiler Bullenmarkt"
        if d200 > 0 and vix < 18:
            return "Ruhiger Bullenmarkt"
        return "Neutral"
    if isinstance(m, pd.DataFrame):
        return m.apply(lambda r: one(r.to_dict()), axis=1)
    return one(m.to_dict() if hasattr(m, "to_dict") else m)


def regime_perf(X):
    X = X.copy()
    X["regime"] = classify(X[["vix", "vix_ts", "spy_dd52", "spy_dist200", "breadth200", "hy_chg20"]])
    out = {}
    for k, g in X.groupby("strat"):
        out[k] = {}
        for rg, h in g.groupby("regime"):
            s = stats.summarize(h)
            out[k][rg] = {"n": s["n"], "mean": s.get("mean"), "win": s.get("win"), "t": s.get("t")}
    return out


def _equity_path(sym, entry, exit_, direction, atr_pct):
    """Aktie vom Einstieg bis zum Ausstieg des Optionstrades, Stop 1,5 ATR (Schlusskurse). Rückgabe R-Multiple."""
    px = data.prices(sym)
    if px is None or not atr_pct or atr_pct != atr_pct:
        return np.nan, np.nan
    c = px["Close"]
    seg = c[(c.index >= entry - pd.Timedelta(days=3)) & (c.index <= exit_)]
    seg = seg[seg.index <= exit_]
    if len(seg) < 2:
        return np.nan, np.nan
    seg = seg[seg.index >= seg.index[seg.index <= entry].max()] if (seg.index <= entry).any() else seg
    p0 = seg.iloc[0]
    stop = 1.5 * atr_pct
    path = direction * (seg / p0 - 1)
    hit = path[path <= -stop]
    ret = float(hit.iloc[0]) if len(hit) else float(path.iloc[-1])
    ret -= COST
    return ret, ret / stop


def equity_vs_option(X, a2):
    """Für jeden Trigger mit Richtung: Aktie statt Option im gleichen Zeitfenster."""
    out = {}
    split = pd.Timestamp(a2["split"]) if a2 else None
    for r in (a2 or {}).get("triggers", []):
        b = backtest.STRATS[r["strategy"]]["bias"]
        if b not in ("bull", "bear"):
            continue
        d = 1 if b == "bull" else -1
        Xs = X[X.strat == r["strategy"]]
        try:
            m = Xs.eval(r["final_expr"]).fillna(False).astype(bool)
        except Exception:
            continue
        sel = Xs[m].copy()
        if len(sel) < 20:
            continue
        rr = [_equity_path(t, e, x, d, a) for t, e, x, a in zip(sel.ticker, sel.entry, sel.exit, sel.atr_pct)]
        sel["eq_ret"] = [x[0] for x in rr]
        sel["eq_R"] = [x[1] for x in rr]
        sel = sel.dropna(subset=["eq_R"])
        oos = sel[sel.entry >= split]
        def sm(v):
            v = np.asarray(v)
            return {"n": int(len(v)), "mean": float(v.mean()) if len(v) else None, "win": float((v > 0).mean()) if len(v) else None,
                    "sr": float(v.mean() / v.std()) if len(v) > 2 and v.std() > 0 else None}
        o_opt, o_eq = sm(oos["ret"]), sm(oos["eq_R"])
        better = None
        if o_opt["n"] >= 15 and o_opt["sr"] is not None and o_eq["sr"] is not None:
            better = "Aktie" if o_eq["sr"] > o_opt["sr"] * 1.1 else ("Option" if o_opt["sr"] > o_eq["sr"] * 1.1 else "gleichwertig")
        out[r["trigger"]] = {"direction": "long" if d > 0 else "short", "option_oos": o_opt, "equity_oos_R": o_eq,
                             "equity_oos_ret": sm(oos["eq_ret"]), "better": better}
    return out


def equity_signals(feats, live):
    trig = yload("equity_triggers.yaml", [])
    F = feats.copy()
    split = stats.split_date(F["date"], RC["in_sample_frac"])
    res = []
    for e in trig:
        d = 1 if e["direction"] == "long" else -1
        col = f"fut_ret{e['hold']}"
        try:
            m = F.eval(e["when"]).fillna(False).astype(bool)
        except Exception as ex:
            res.append({**e, "error": str(ex)})
            continue
        sel = F[m & F[col].notna()].copy()
        # max. ein Einstieg je Ticker und Woche (Überlappung begrenzen)
        sel["wk"] = pd.DatetimeIndex(sel["date"]).to_period("W")
        sel = sel.groupby(["ticker", "wk"]).head(1)
        sel["ret"] = d * sel[col] - COST
        sel["ret_stress"] = d * sel[col] - 3 * COST
        sel = sel.rename(columns={"date": "entry"})
        base = F[F[col].notna()].copy()
        base["wk"] = pd.DatetimeIndex(base["date"]).to_period("W")
        base = base.groupby(["ticker", "wk"]).head(1)
        base["ret"] = d * base[col] - COST
        base["ret_stress"] = d * base[col] - 3 * COST
        base = base.rename(columns={"date": "entry"})
        wf = stats.walk_forward(sel, pd.Series(True, index=sel.index), split)
        wf["base_oos"] = stats.summarize(base[base.entry >= split])
        now = []
        if live is not None and len(live):
            try:
                lm = live.eval(e["when"]).fillna(False).astype(bool)
                fire = live.loc[lm.values, "ticker"].tolist()
                # Rangfolge nach historischer Signalgüte je Ticker (geschrumpft zum Gesamtmittel), nicht alphabetisch
                g = sel.groupby("ticker")["ret"].agg(["count", "mean"])
                pooled = sel["ret"].mean()
                score = {t: (r["count"] * r["mean"] + 8 * pooled) / (r["count"] + 8) for t, r in g.iterrows()}
                now = sorted(fire, key=lambda t: -score.get(t, pooled))
            except Exception:
                pass
        res.append({**e, **wf, "firing_now": now, "edge_vs_base":
                    (wf["oos"].get("mean", np.nan) - wf["base_oos"].get("mean", np.nan)) if wf["oos"].get("n") else None})
    ok = [r for r in res if "oos" in r]
    sig = stats.bh([r["oos"].get("p", 1) for r in ok], RC["fdr_q"])
    for r, s in zip(ok, sig):
        r["bh"] = bool(s)
        r["status"] = stats.verdict(r, {**RC, "min_oos_trades": 60})
    return res


def equity_idea(t, direction, live_row, pa, reason, evidence):
    px = data.prices(t)
    if px is None:
        return None
    c = float(px["Close"].iloc[-1])
    atr = float(live_row.get("atr_pct") or 0.02) * c
    d = 1 if direction == "long" else -1
    stop = c - d * 1.5 * atr
    if pa:
        if d > 0 and pa.get("supports"):
            stop = min(stop, pa["supports"][0][0] - 0.25 * atr) if pa["supports"][0][0] > c - 3 * atr else stop
        if d < 0 and pa.get("resistances"):
            stop = max(stop, pa["resistances"][0][0] + 0.25 * atr) if pa["resistances"][0][0] < c + 3 * atr else stop
    risk_ps = abs(c - stop)
    acct = CFG["account_size"]
    shares = int(min(acct * CFG["risk"]["equity_per_trade"] / risk_ps, acct * CFG["risk"]["equity_max_position"] / c))
    target = c + d * 2 * risk_ps
    if pa:
        lv = pa.get("resistances") if d > 0 else pa.get("supports")
        if lv:
            target = lv[0][0] if abs(lv[0][0] - c) >= risk_ps else target
    return {"ticker": t, "direction": direction, "entry": round(c, 2), "stop": round(stop, 2), "target": round(target, 2),
            "shares": shares, "position_usd": round(shares * c, 0), "risk_usd": round(shares * risk_ps, 0),
            "crv": round(abs(target - c) / risk_ps, 2), "reason": reason, "evidence": evidence}


def finalize_equity(ideas, regime):
    """Aktien-Ideen bereinigen: Widersprüche, Regime, Rangfolge, Portfolio-Grenzen."""
    dropped = []
    by = {}
    for i in ideas:
        by.setdefault(i["ticker"], []).append(i)
    clean = []
    for t, lst in by.items():
        dirs = {i["direction"] for i in lst}
        if len(dirs) > 1:
            dropped.append({"ticker": t, "why": "widersprüchliche Signale (long und short)"})
            continue
        best = sorted(lst, key=lambda i: (i.get("status") != "bestätigt", i.get("source") != "option"))[0]
        if len(lst) > 1:
            best = {**best, "reason": " + ".join(sorted({i["reason"] for i in lst}))}
        clean.append(best)
    out = []
    for i in clean:
        if regime in ("Crash/Stress", "Bärenmarkt") and i["direction"] == "long":
            dropped.append({"ticker": i["ticker"], "why": f"Long im Regime „{regime}“ gesperrt"})
            continue
        if regime == "Ruhiger Bullenmarkt" and i["direction"] == "short":
            dropped.append({"ticker": i["ticker"], "why": f"Short im Regime „{regime}“ gesperrt"})
            continue
        out.append(i)
    out.sort(key=lambda i: (i.get("status") != "bestätigt", -(i.get("crv") or 0)))
    acct = CFG["account_size"]
    cap = acct * CFG["risk"].get("max_total_equity_exposure", 1.0)
    final, used = [], 0.0
    for i in out:
        if len(final) >= CFG["risk"]["max_ideas"] or used + i["position_usd"] > cap:
            dropped.append({"ticker": i["ticker"], "why": "Portfolio-Grenze erreicht (Anzahl/Gesamtposition)"})
            continue
        final.append(i)
        used += i["position_usd"]
    return final, dropped


def run():
    a2 = jload("a2_tickers.json", {"triggers": []})
    a3 = jload("a3_live.json", {"candidates": []})
    a4 = jload("a4_price.json", {"tickers": {}, "judgements": []})
    feats = all_features()
    X = with_features(all_trades(), feats)
    mkt = features.market()
    now = mkt.dropna(subset=["vix"]).iloc[-1]
    regime = classify(now)
    rp = regime_perf(X)
    live_p = os.path.join(CACHE, "live_features.pkl")
    live = pd.read_pickle(live_p) if os.path.exists(live_p) else None
    # Regime-Filter für jede Optionsidee
    gates = []
    for c in a3["candidates"]:
        if not c.get("legs"):
            continue
        h = rp.get(c["strategy"], {}).get(regime, {})
        vol = c["vol"]
        status, why = "ok", []
        if h.get("n", 0) >= 30 and (h.get("mean") or 0) < 0:
            status = "blockiert"
            why.append(f"historisch negativ im Regime „{regime}“ (n={h['n']}, Ø {h['mean']:+.1%})")
        if regime == "Crash/Stress" and vol == "short" and not (h.get("n", 0) >= 30 and (h.get("t") or 0) >= 2):
            status = "blockiert"
            why.append("Crash/Stress: Short-Vola nur mit nachgewiesenem Edge in diesem Regime")
        elif regime in ("Korrektur", "Bärenmarkt") and vol == "short" and c["bias"] == "bull":
            status = "halbe Größe"
            why.append(f"{regime}: bullische Short-Vola nur mit halber Größe")
        if not why:
            why.append(f"Regime „{regime}“: historisch Ø {h.get('mean', 0) or 0:+.1%} je Trade (n={h.get('n', 0)})")
        gates.append({"ticker": c["ticker"], "trigger": c["trigger"], "strategy": c["strategy"], "status": status, "why": why})
    evo = equity_vs_option(X, a2)
    eq_sig = equity_signals(feats, live)
    # Aktien-Ideen: (a) aus Optionsideen mit Richtung, wenn die Aktie im Backtest besser/gleichwertig ist
    ideas = []
    lrow = {r["ticker"]: r for r in (live.to_dict("records") if live is not None else [])}
    for c in a3["candidates"]:
        if c.get("verdict") == "abgelehnt" or c["bias"] not in ("bull", "bear"):
            continue
        cmp_ = evo.get(c["trigger"])
        if not cmp_ or cmp_["better"] not in ("Aktie", "gleichwertig"):
            continue
        pa = a4["tickers"].get(c["ticker"])
        idea = equity_idea(c["ticker"], cmp_["direction"], lrow.get(c["ticker"], {}), pa,
                           f"Optionssignal {c['trigger']} ({c['trigger_name']}) → {'bullisch' if cmp_['direction']=='long' else 'bärisch'}",
                           f"Backtest OOS: Aktie {cmp_['equity_oos_R']['mean']:+.2f} R/Trade vs. Option {cmp_['option_oos']['mean']:+.1%} auf Risiko ({cmp_['better']})")
        if idea:
            idea["source"] = "option"
            ideas.append(idea)
    # (b) eigene Aktien-Signale aus Optionsdaten
    for e in eq_sig:
        if e.get("status") not in ("bestätigt", "Kandidat"):
            continue
        for t in e["firing_now"][:6]:
            idea = equity_idea(t, e["direction"], lrow.get(t, {}), a4["tickers"].get(t),
                               f"{e['id']} {e['name']}", f"OOS Ø {e['oos']['mean']:+.2%} je {e['hold']} T, Trefferquote {e['oos']['win']:.0%} ({e['status']})")
            if idea:
                idea["source"] = e["id"]
                idea["status"] = e["status"]
                ideas.append(idea)
    # (c) Chartmuster-Signale von Agent 8 (nur Kandidat/bestätigt, Signal von heute)
    a8 = jload("a8_patterns.json", {"signals": []})
    for sg in a8.get("signals", []):
        t = sg["ticker"]
        px = data.prices(t)
        if px is None:
            continue
        c = float(px["Close"].iloc[-1])
        risk_ps = abs(sg["entry"] - sg["stop"])
        if risk_ps <= 0:
            continue
        acct = CFG["account_size"]
        shares = int(min(acct * CFG["risk"]["equity_per_trade"] / risk_ps, acct * CFG["risk"]["equity_max_position"] / c))
        ideas.append({"ticker": t, "direction": "long" if sg["dir"] > 0 else "short", "entry": round(sg["entry"], 2),
                      "stop": round(sg["stop"], 2), "target": round(sg["target"], 2), "shares": shares,
                      "position_usd": round(shares * c, 0), "risk_usd": round(shares * risk_ps, 0),
                      "crv": round(abs(sg["target"] - sg["entry"]) / risk_ps, 2),
                      "reason": f"Chartmuster {sg['label']}",
                      "evidence": f"OOS Ø {sg['oos_R']:+.2f} R je Trade, Trefferquote {sg['oos_win']:.0%} ({sg['status']}, Mehrwert gegenüber Einstieg ohne Muster)",
                      "source": "Muster:" + sg["p"], "status": sg["status"]})
    # (d) Google-Trends-Signale von Agent 10 (nur Kandidat/bestätigt)
    from chain.stages import a10_social, a11_filings
    for sg in a10_social.active_signals(jload("a10_social.json", {})):
        idea = equity_idea(sg["ticker"], sg["direction"], lrow.get(sg["ticker"], {}), a4["tickers"].get(sg["ticker"]), sg["reason"], sg["evidence"])
        if idea:
            idea.update(source=sg["source"], status=sg["status"])
            ideas.append(idea)
    # Agent 11: SEC-Filings bestätigen/schwächen jede Aktien-Idee (Horizont passt: Aktien-Ideen laufen Wochen bis Monate)
    a11 = jload("a11_filings.json", {})
    for i in ideas:
        conf = a11_filings.confirm(i["ticker"], 1 if i["direction"] == "long" else -1, None, a11)
        i["sec"] = [{"s": s_, "text": t_} for s_, t_, _ in conf]
        w = sum(w_ for _, _, w_ in conf)
        if w < 0:
            i["shares"] = int(i["shares"] / 2)
            i["position_usd"] = round(i["position_usd"] / 2, 0)
            i["risk_usd"] = round(i["risk_usd"] / 2, 0)
            i["reason"] += " · SEC-Filings dagegen → halbe Größe"
        elif w > 0:
            i["reason"] += " · von SEC-Filings bestätigt"
    # Lernmodul: Live-Leistung je Aktien-Signalquelle → Größenfaktor (0,5 = halbiert, 0 = pausiert)
    srcw = (yload("weights.yaml", {}) or {}).get("equity_sources", {})
    kept = []
    for i in ideas:
        m = (srcw.get(i.get("source", "")) or {}).get("size_mult", 1.0)
        if m == 0:
            continue
        if m < 1:
            for k in ("shares", "position_usd", "risk_usd"):
                i[k] = int(i[k] * m) if k == "shares" else round(i[k] * m, 0)
            i["reason"] += " · Lernmodul: Quelle live schwach → halbe Größe"
        kept.append(i)
    ideas = kept
    ideas, dropped = finalize_equity(ideas, regime)
    # Chartanalyse für Aktien-Ideen, die Agent 4 noch nicht hatte
    from chain.stages.a4_price import analyze
    price_extra = {}
    for i in ideas:
        t = i["ticker"]
        if t not in a4["tickers"] and t not in price_extra:
            try:
                r = analyze(t)
                if r:
                    price_extra[t] = r
            except Exception as e:
                print("pa", t, e)
    regime_hist = classify(mkt.dropna(subset=["vix"]).iloc[-260:][["vix", "vix_ts", "spy_dd52", "spy_dist200", "breadth200", "hy_chg20"]])
    out = {"day": a3.get("day"), "regime": regime,
           "market": {k: (None if pd.isna(v) else float(v)) for k, v in now.items()},
           "market_date": str(now.name.date()),
           "regime_history": [[str(d.date()), r] for d, r in regime_hist.items()][-120:],
           "regime_perf": rp, "gates": gates, "equity_vs_option": evo, "equity_signals": eq_sig, "equity_ideas": ideas, "equity_dropped": dropped, "price_extra": price_extra,
           "series": {k: [[str(d.date()), round(float(v), 4)] for d, v in mkt[k].dropna().iloc[-260:].items()]
                      for k in ("vix", "vix_ts", "breadth200", "spy_dd52", "hy_spread") if k in mkt}}
    jdump(out, "a5_macro.json")
    return out


if __name__ == "__main__":
    o = run()
    print("Regime:", o["regime"])
    for g in o["gates"][:10]:
        print(g)
    for i in o["equity_ideas"][:10]:
        print(i)
