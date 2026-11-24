"""Agent 7 – Positionswächter und Track-Record.

Echte Positionen: Markus übernimmt sie im Dashboard per Klick („Eingegangen“). Der Lauf liest sie aus der
Freigabe-Datenbank des Dashboards und legt sie in chain_lab/positions/open.json ab. Agent 7 bewertet jede Position
mit der neuesten Kette (abends nach US-Schluss, zusätzlich untertägig) und schlägt Alarm:
  Handeln  – Gewinnziel erreicht, Stop gerissen, Short-Strike getestet/im Geld, Verfall nah, Regime gekippt,
             Earnings neu in der Laufzeit, Aktien-Stop/Ziel erreicht
  Beobachten – Kurs durch Put-/Call-Wall, Gamma-Regime gedreht, Kurs unter Gamma-Flip, Einstiegssignal erloschen,
             Trendphase gegen die Position
Papier-Trades (Track-Record): Jeder Kandidat von Agent 3 mit Strikes wird automatisch zum Fill-Preis eröffnet und
nach denselben Regeln wie im Backtest geschlossen. So lässt sich messen, ob Gamma-Strikes, Chart-Prüfung und
Regime-Filter Geld bringen (freigegeben vs. abgelehnt, Live vs. Backtest-Erwartung).
Ausgabe: chain_lab/out/a7_positions.json, chain_lab/track/paper.json
"""
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from chain.core import backtest, data
from chain.stages import a9_events
from chain.stages.common import CFG, LAB, jdump, jload

POS = os.path.join(LAB, "positions")
TRACK = os.path.join(LAB, "track")
for p in (POS, TRACK):
    os.makedirs(p, exist_ok=True)
LEVEL = {"OK": 0, "Beobachten": 1, "Handeln": 2}
SHORT_VOL = {k for k, v in backtest.STRATS.items() if v["vol"] == "short"}
BAD_REGIMES = ("Crash/Stress", "Korrektur", "Bärenmarkt")


# ------------------------------------------------------------------ Marktdaten
def market_snapshot(intraday=False):
    """Neueste Ketten: untertägig (chain_data/intraday/latest) falls vorhanden und gewünscht, sonst Abend-Kette."""
    base = None
    if intraday:
        p = os.path.join(data.D, "intraday", "latest")
        if os.path.isdir(p):
            base = p
    if base is None:
        days = data.live_days()
        base = os.path.join(data.D, "live", days[-1]) if days else None
    if base is None:
        return None, {}, pd.DataFrame()
    summ = pd.read_csv(os.path.join(base, "_summary.csv")).set_index("ticker") if os.path.exists(os.path.join(base, "_summary.csv")) else pd.DataFrame()
    stamp = os.path.basename(base)
    if os.path.exists(os.path.join(base, "_stamp.txt")):
        stamp = open(os.path.join(base, "_stamp.txt")).read().strip()
    return base, {"stamp": stamp, "intraday": base.endswith("latest")}, summ


def chain_of(base, t):
    p = os.path.join(base, f"{t}.csv.gz")
    return pd.read_csv(p) if os.path.exists(p) else None


def mark_option(pos, ch, spot, today):
    """Mid-Wert der Position je Aktie; abgelaufene Legs zum inneren Wert."""
    v, worst_spread, short_info = 0.0, 0.0, []
    for l in pos["legs"]:
        cp = 1 if l["cp"] == "C" else -1
        if pd.Timestamp(l["expiry"]) < pd.Timestamp(today):
            v += l["qty"] * max(cp * (spot - l["strike"]), 0)
            continue
        if ch is None:
            return None
        r = ch[(ch.expiry == l["expiry"]) & (ch.cp == l["cp"]) & ((ch.strike - l["strike"]).abs() < 1e-6)]
        if r.empty:
            return None
        r = r.iloc[0]
        if not r.ask > 0:
            return None   # keine Quotes (z. B. vorbörslich) – nicht bewertbar statt Schein-Gewinn
        mid = (r.bid + r.ask) / 2
        v += l["qty"] * mid
        worst_spread = max(worst_spread, (r.ask - r.bid) / mid if mid > 0 else 0)
        if l["qty"] < 0:
            short_info.append({"cp": l["cp"], "strike": l["strike"], "delta": float(r.delta), "expiry": l["expiry"]})
    return {"value": v, "shorts": short_info, "spread": worst_spread}


# ------------------------------------------------------------------ Bewertung
def evaluate(pos, base, summ, regime, gex_now, live_feats, today):
    t = pos["ticker"]
    al = []

    def add(lvl, txt):
        al.append({"level": lvl, "text": txt})

    spot = float(summ.loc[t, "spot"]) if t in summ.index else None
    if spot is None:
        px = data.prices(t)
        spot = float(px["Close"].iloc[-1]) if px is not None else None
    if spot is None:
        return {**pos, "level": "Beobachten", "alerts": [{"level": "Beobachten", "text": "keine aktuellen Kursdaten"}]}
    out = {**pos, "spot": spot}
    if pos["kind"] == "equity":
        d = 1 if pos["direction"] == "long" else -1
        pnl_ps = d * (spot - pos["entry_price"])
        out.update(pnl_usd=round(pnl_ps * pos["shares"], 0), pnl_pct=round(pnl_ps / pos["entry_price"], 4))
        if pos.get("stop") and d * (spot - pos["stop"]) <= 0:
            add("Handeln", f"Stop {pos['stop']:g} erreicht (Kurs {spot:.2f}) – Position schließen")
        if pos.get("target") and d * (spot - pos["target"]) >= 0:
            add("Handeln", f"Kursziel {pos['target']:g} erreicht – Gewinn sichern oder Stop nachziehen")
        pa = (jload("a4_price.json", {}).get("tickers") or {}).get(t)
        if pa:
            if (d > 0 and pa["phase"] == "Abwärtstrend") or (d < 0 and pa["phase"] == "Aufwärtstrend"):
                add("Beobachten", f"Trendphase jetzt „{pa['phase']}“ – gegen die Position")
        if pos.get("regime_entry") and regime != pos["regime_entry"]:
            if (d > 0 and regime in BAD_REGIMES) or (d < 0 and regime == "Ruhiger Bullenmarkt"):
                add("Handeln", f"Marktregime gekippt: {pos['regime_entry']} → {regime}")
            else:
                add("Beobachten", f"Marktregime gewechselt: {pos['regime_entry']} → {regime}")
    else:
        ch = chain_of(base, t)
        m = mark_option(pos, ch, spot, today)
        if m is None:
            add("Beobachten", "Position nicht bewertbar (keine gültigen Quotes für alle Legs)")
            out.update(level=max((a["level"] for a in al), key=LEVEL.get), alerts=al)
            return out
        entry = pos["entry_price"]                      # >0 Debit, <0 Credit je Aktie
        pnl_ps = m["value"] - entry
        n = pos.get("contracts", 1)
        maxloss = pos.get("max_loss") or abs(entry)
        out.update(value=round(m["value"], 3), pnl_usd=round(pnl_ps * 100 * n, 0),
                   pnl_ror=round(pnl_ps / maxloss, 4) if maxloss else None)
        dte = min((pd.Timestamp(l["expiry"]) - pd.Timestamp(today)).days for l in pos["legs"])
        out["dte"] = int(dte)
        key = pos.get("strategy", "")
        if entry < 0:   # Credit
            tp = 0.25 if key in ("ifly", "earn_ifly") else 0.5
            got = pnl_ps / (-entry)
            out["pct_max_profit"] = round(got, 3)
            if got >= tp:
                add("Handeln", f"{got:.0%} der Prämie verdient (Ziel {tp:.0%}) – schließen und Gewinn sichern")
            if pnl_ps <= entry:   # Verlust >= eingenommene Prämie
                add("Handeln", f"Verlust {-pnl_ps*100*n:,.0f} $ ≥ eingenommene Prämie – Stop, schließen oder rollen")
            if dte <= 7:
                add("Handeln", f"Verfall in {dte} Tagen – Gamma-Risiko hoch, schließen oder rollen")
            elif dte <= 14:
                add("Beobachten", f"Verfall in {dte} Tagen")
        else:           # Debit
            chg = pnl_ps / entry if entry else 0
            if key.startswith("cal") or key == "dcal":
                front = min(pd.Timestamp(l["expiry"]) for l in pos["legs"])
                if (front - pd.Timestamp(today)).days <= 2:
                    add("Handeln", "Front-Verfall in ≤ 2 Tagen – Calendar schließen (wie im Backtest)")
                if chg >= 0.25:
                    add("Beobachten", f"Calendar +{chg:.0%} – Gewinnmitnahme prüfen")
            elif chg >= 0.5:
                add("Handeln", f"+{chg:.0%} auf den Einsatz – Gewinn sichern")
            if chg <= -0.5:
                add("Handeln", f"{chg:.0%} auf den Einsatz – Stop, Setup ist gekippt")
            if key == "lstrad" and (pd.Timestamp(today) - pd.Timestamp(pos["opened"])).days >= 14:
                add("Handeln", "Haltedauer 14 Tage erreicht (Backtest-Regel) – schließen")
        for s in m["shorts"]:
            k, d = s["strike"], abs(s["delta"])
            itm = (s["cp"] == "P" and spot < k) or (s["cp"] == "C" and spot > k)
            if itm:
                add("Handeln", f"Short-{'Put' if s['cp']=='P' else 'Call'} {k:g} im Geld (Kurs {spot:.2f})")
            elif d >= 0.45:
                add("Handeln", f"Short-{'Put' if s['cp']=='P' else 'Call'} {k:g} mit Delta {d:.2f} – Strike wird getestet")
            elif abs(spot / k - 1) <= 0.01:
                add("Beobachten", f"Kurs {spot:.2f} nur {abs(spot/k-1):.1%} vom Short-Strike {k:g}")
        g0, g1 = pos.get("gex_entry") or {}, gex_now.get(t) or {}
        if g1:
            shorts_p = any(l["qty"] < 0 and l["cp"] == "P" for l in pos["legs"])
            shorts_c = any(l["qty"] < 0 and l["cp"] == "C" for l in pos["legs"])
            if shorts_p and g0.get("put_wall") and spot < g0["put_wall"]:
                add("Beobachten", f"Kurs unter der Put-Wall vom Einstieg ({g0['put_wall']:g}) – Unterstützung gebrochen")
            if shorts_c and g0.get("call_wall") and spot > g0["call_wall"]:
                add("Beobachten", f"Kurs über der Call-Wall vom Einstieg ({g0['call_wall']:g}) – Widerstand gebrochen")
            if g0.get("regime") == "positiv" and g1.get("regime") == "negativ" and key in SHORT_VOL:
                add("Beobachten", "Gamma-Regime von positiv auf negativ gedreht – Händler verstärken jetzt Bewegungen")
            if g1.get("flip") and g0.get("flip") and spot < g1["flip"] <= (g0.get("spot") or spot) and key in SHORT_VOL:
                add("Beobachten", f"Kurs unter den Gamma-Flip {g1['flip']:.2f} gefallen")
        if pos.get("regime_entry") and regime != pos["regime_entry"]:
            if key in SHORT_VOL and regime in BAD_REGIMES and pos["regime_entry"] not in BAD_REGIMES:
                add("Handeln", f"Marktregime gekippt: {pos['regime_entry']} → {regime} – Short-Vola-Risiko gestiegen")
            else:
                add("Beobachten", f"Marktregime gewechselt: {pos['regime_entry']} → {regime}")
        ed = data.earnings(t)
        last_exp = max(pd.Timestamp(l["expiry"]) for l in pos["legs"])
        hit = [e for e in ed if pd.Timestamp(today) <= e <= last_exp]
        if hit and key not in ("pre_earn", "earn_ifly") and not pos.get("earnings_known"):
            add("Handeln", f"Earnings am {hit[0].date()} vor dem Verfall – Risiko neu bewerten")
        expr = pos.get("expr")
        if expr and live_feats is not None and t in set(live_feats.get("ticker", [])):
            try:
                row = live_feats[live_feats.ticker == t]
                if not bool(row.eval(expr).fillna(False).iloc[0]):
                    add("Beobachten", "Einstiegssignal ist heute nicht mehr aktiv")
            except Exception:
                pass
    # Agent 9: Termine/Nachrichten bis zum Ende der Position
    try:
        if pos["kind"] == "equity":
            end, vol = pd.Timestamp(today) + pd.Timedelta(days=10), "long"
        else:
            end = max(pd.Timestamp(l["expiry"]) for l in pos["legs"])
            vol = "short" if pos.get("strategy") in SHORT_VOL else "long"
        for s_, t_, w_ in a9_events.check(t, today, end, vol, EV["cal"], EV["news"]):
            if s_ == "✗":
                add("Handeln" if pos["kind"] != "equity" else "Beobachten", t_.replace("Short-Vola erst nach dem Termin", "Short-Vola-Position vor dem Termin verkleinern/absichern"))
            elif s_ == "~" or (s_ == "i" and pd.Timestamp(today) + pd.Timedelta(days=7) >= end):
                add("Beobachten", t_)
    except Exception:
        pass
    lvl = "OK"
    for a in al:
        if LEVEL[a["level"]] > LEVEL[lvl]:
            lvl = a["level"]
    out.update(level=lvl, alerts=al or [{"level": "OK", "text": "Setup intakt"}])
    return out


# ------------------------------------------------------------------ Papier-Trades
def paper_update(base, summ, today, regime):
    p = os.path.join(TRACK, "paper.json")
    book = json.load(open(p)) if os.path.exists(p) else {"open": [], "closed": []}
    a3 = jload("a3_live.json", {"candidates": []})
    from chain.stages.a6_dashboard import build_ideas
    a5 = jload("a5_macro.json", {})
    top, allideas = build_ideas(a3, jload("a4_price.json", {}), a5)
    ideas = {(i["ticker"], i["trigger"]): i for i in allideas}
    top_keys = {(i["ticker"], i["trigger"]) for i in top}
    keys_open = {(x["ticker"], x["trigger"], x["strategy"]) for x in book["open"]}
    # neue Papier-Trades (einmal je Ticker/Trigger/Strategie, solange offen)
    if a3.get("day") and a3["day"] != book.get("last_open_day"):
        for c in a3["candidates"]:
            if not c.get("legs"):
                continue
            k = (c["ticker"], c["trigger"], c["strategy"])
            if k in keys_open:
                continue
            fin = ideas.get((c["ticker"], c["trigger"]), {})
            book["open"].append({
                "id": f"{a3['day']}-{c['ticker']}-{c['trigger']}", "kind": "paper", "ticker": c["ticker"],
                "trigger": c["trigger"], "strategy": c["strategy"], "opened": a3["day"],
                "legs": [{k2: l[k2] for k2 in ("expiry", "cp", "strike", "qty")} for l in c["legs"]],
                "entry_price": c["payoff"]["debit_fill"], "max_loss": c["payoff"]["max_loss"], "contracts": 1,
                "verdict": fin.get("final", c["verdict"]), "a3_verdict": c["verdict"],
                "top": (c["ticker"], c["trigger"]) in top_keys, "status_trigger": c["trigger_status"],
                "gamma_strikes": any(x["s"] == "◆" and "gesetzt" in x["text"] for x in c.get("checks", [])),
                "exp_ror": c.get("oos_mean"), "regime_entry": regime, "spot_entry": c["spot"], "expr": c.get("expr"),
                "score": c.get("score"), "factors": c.get("factors", {}), "vol": c.get("vol"),
                "ctx": {"chart": (fin.get("price_action") or {}).get("verdict"), "macro": (fin.get("macro") or {}).get("status"), "regime": regime}})
            keys_open.add(k)
        for e in a5.get("equity_ideas", []):
            k = (e["ticker"], e.get("source", ""), "equity")
            if k in keys_open:
                continue
            book["open"].append({
                "id": f"{a3['day']}-{e['ticker']}-{e.get('source','')}-eq", "kind": "paper_equity", "ticker": e["ticker"],
                "trigger": e.get("source", ""), "strategy": "equity", "direction": e["direction"], "opened": a3["day"],
                "entry_price": e["entry"], "stop": e["stop"], "target": e["target"], "shares": e["shares"],
                "risk_ps": abs(e["entry"] - e["stop"]), "verdict": "angezeigt", "top": True, "regime_entry": regime,
                "max_days": 20})
            keys_open.add(k)
        book["last_open_day"] = a3["day"]
    # schließen nach Backtest-Regeln
    still = []
    for x in book["open"]:
        t = x["ticker"]
        if x.get("kind") == "paper_equity":
            px = data.prices(t)
            if px is None:
                still.append(x)
                continue
            seg = px[px.index > pd.Timestamp(x["opened"])]
            d = 1 if x["direction"] == "long" else -1
            reason, exit_px = None, None
            for dd, r in seg.iterrows():
                lo, hi = (r.Low, r.High) if d > 0 else (r.High, r.Low)
                if d * (lo - x["stop"]) <= 0:
                    reason, exit_px = "Stop", x["stop"]
                elif d * (hi - x["target"]) >= 0:
                    reason, exit_px = "Ziel", x["target"]
                elif len(seg.loc[:dd]) >= x["max_days"]:
                    reason, exit_px = f"{x['max_days']} Tage", r.Close
                if reason:
                    pnl = d * (exit_px - x["entry_price"]) - 0.001 * x["entry_price"]
                    book["closed"].append({**x, "closed": str(dd.date()), "reason": reason, "pnl": round(pnl, 4),
                                           "ror": round(pnl / x["risk_ps"], 4) if x["risk_ps"] else None,
                                           "pnl_usd": round(pnl * x["shares"], 0)})
                    break
            if not reason:
                last = seg["Close"].iloc[-1] if len(seg) else x["entry_price"]
                x["last_pnl"], x["last_mark"] = (round(d * (last - x["entry_price"]) / x["risk_ps"], 4) if x.get("risk_ps") else None), str(today)   # in R
                still.append(x)
            continue
        spot = float(summ.loc[t, "spot"]) if t in summ.index else None
        ch = chain_of(base, t) if base else None
        if spot is None:
            still.append(x)
            continue
        m = mark_option(x, ch, spot, today)
        if m is None:
            still.append(x)
            continue
        hs = 0.0
        if ch is not None:   # halber Spread beim Ausstieg (Slippage wie im Backtest)
            for l in x["legs"]:
                if pd.Timestamp(l["expiry"]) >= pd.Timestamp(today):
                    r = ch[(ch.expiry == l["expiry"]) & (ch.cp == l["cp"]) & ((ch.strike - l["strike"]).abs() < 1e-6)]
                    if len(r):
                        hs += 0.5 * (r.iloc[0].ask - r.iloc[0].bid) * CFG["research"]["slippage_frac"]
        pnl = m["value"] - x["entry_price"] - hs
        key = x["strategy"]
        mode = backtest.STRATS.get(key, {}).get("exit", "hold")
        front = min(pd.Timestamp(l["expiry"]) for l in x["legs"])
        days = (front - pd.Timestamp(today)).days
        reason = None
        if mode in ("tp50", "tp25") and x["entry_price"] < 0 and (m["value"] - x["entry_price"]) >= (0.5 if mode == "tp50" else 0.25) * (-x["entry_price"]):
            reason = mode
        elif mode == "front" and days <= 1:
            reason = "front-verfall"
        elif mode.startswith("days") and (pd.Timestamp(today) - pd.Timestamp(x["opened"])).days >= int(mode[4:]):
            reason = mode
        elif mode in ("pre", "post"):
            ed = data.earnings(t)
            E = [e for e in ed if e >= pd.Timestamp(x["opened"])]
            if E and ((mode == "pre" and (E[0] - pd.Timestamp(today)).days <= 1) or (mode == "post" and pd.Timestamp(today) >= E[0])):
                reason = mode
        if days < 0 or (reason is None and pd.Timestamp(today) >= front):
            reason = reason or "verfall"
            pnl = m["value"] - x["entry_price"]
        if reason:
            book["closed"].append({**x, "closed": str(today), "reason": reason, "pnl": round(pnl, 4),
                                   "ror": round(pnl / x["max_loss"], 4) if x["max_loss"] else None})
        else:
            x["last_pnl"], x["last_mark"] = round(pnl, 4), str(today)
            still.append(x)
    book["open"] = still
    json.dump(book, open(p, "w"), ensure_ascii=False, indent=0, default=str)
    return book


def track_stats(book):
    c = pd.DataFrame(book["closed"])
    out = {"n_open": len(book["open"]), "n_closed": int(len(c)), "n_open_shown": sum(1 for x in book["open"] if x.get("top"))}
    out["open_shown"] = [{"ticker": x["ticker"], "kind": x.get("kind"), "strategy": x.get("strategy_name") or x.get("strategy"),
                          "direction": x.get("direction"), "opened": x.get("opened"), "entry": x.get("entry_price") or x.get("fill"),
                          "stop": x.get("stop"), "target": x.get("target"), "last_pnl": x.get("last_pnl"), "last_mark": x.get("last_mark")}
                         for x in book["open"] if x.get("top")]
    if c.empty:
        return out

    if "pnl_usd" not in c:
        c["pnl_usd"] = np.nan
    c["usd"] = np.where(c["strategy"] == "equity", c["pnl_usd"], c["pnl"] * 100)
    if "top" not in c:
        c["top"] = False

    def grp(df):
        r = df["ror"].dropna()
        return {"n": int(len(r)), "mean": float(r.mean()) if len(r) else None, "win": float((r > 0).mean()) if len(r) else None,
                "pnl_usd": float(df["usd"].sum())}
    opt = c[c["strategy"] != "equity"]
    out["all"] = grp(opt)
    out["top_options"] = grp(opt[opt["top"] == True])
    out["equity"] = grp(c[c["strategy"] == "equity"])
    out["shown_all"] = grp(c[c["top"] == True])
    out["by_verdict"] = {k: grp(g) for k, g in opt.groupby("verdict")}
    out["by_strategy"] = {k: grp(g) for k, g in c.groupby("strategy")}
    if "gamma_strikes" in opt:
        out["by_gamma"] = {("Gamma-Strike" if k else "Delta-Strike"): grp(g) for k, g in opt.groupby("gamma_strikes")}
    exp = opt["exp_ror"].dropna() if "exp_ror" in opt else pd.Series(dtype=float)
    out["vs_backtest"] = {"live": float(opt["ror"].mean()) if len(opt) else None, "backtest_erwartung": float(exp.mean()) if len(exp) else None}
    c = c.sort_values("closed")
    shown = c[c["top"] == True]
    out["curve"] = [[d, round(float(v), 2)] for d, v in shown.groupby("closed")["usd"].sum().cumsum().items()]
    out["curve_all"] = [[d, round(float(v), 2)] for d, v in opt.groupby("closed")["usd"].sum().cumsum().items()]
    if "direction" not in c.columns:
        c["direction"] = None
    out["recent"] = c.tail(30).iloc[::-1][["ticker", "strategy", "direction", "trigger", "verdict", "top", "opened", "closed", "reason", "ror", "usd"]].replace({np.nan: None}).to_dict("records")
    return out


EV = {"cal": [], "news": {}}


def run(intraday=False):
    EV["cal"], EV["news"] = a9_events.calendar(), a9_events.news()
    today = dt.date.today()
    base, meta, summ = market_snapshot(intraday)
    a5 = jload("a5_macro.json", {})
    regime = a5.get("regime", "Neutral")
    if meta.get("intraday") and a5.get("market"):
        # Regime untertägig neu bestimmen: VIX und SPY aus dem Schnappschuss, übrige Größen vom Vorabend
        from chain.stages.a5_macro import classify
        m = dict(a5["market"])
        if "VIX" in summ.index:
            m["vix"] = float(summ.loc["VIX", "spot"])
        px = data.prices("SPY")
        if "SPY" in summ.index and px is not None:
            spy = float(summ.loc["SPY", "spot"])
            c = px["Close"]
            m["spy_dist200"] = spy / c.iloc[-200:].mean() - 1
            m["spy_dd52"] = spy / max(c.iloc[-252:].max(), spy) - 1
        if m.get("vix") and a5["market"].get("vix") and a5["market"].get("vix_ts"):
            m["vix_ts"] = a5["market"]["vix_ts"] * m["vix"] / a5["market"]["vix"]   # Näherung: VIX3M unverändert
        regime = classify(m)
    a3 = jload("a3_live.json", {})
    gex_now = a3.get("gex", {})
    if meta.get("intraday"):
        from chain.core.gex import gex_profile
        for t in summ.index:
            ch = chain_of(base, t)
            if ch is not None and "open_interest" in ch:
                try:
                    gex_now[t] = gex_profile(ch, float(summ.loc[t, "spot"]), today)
                except Exception:
                    pass
    lf_p = os.path.join(LAB, "cache", "live_features.pkl")
    live_feats = pd.read_pickle(lf_p) if os.path.exists(lf_p) else None
    open_p = os.path.join(POS, "open.json")
    positions = json.load(open(open_p)) if os.path.exists(open_p) else []
    evals = [evaluate(p, base, summ, regime, gex_now, live_feats, today) for p in positions if p.get("status", "offen") == "offen"]
    evals.sort(key=lambda e: -LEVEL[e["level"]])
    tp = os.path.join(TRACK, "paper.json")
    if not meta.get("intraday"):
        book = paper_update(base, summ, today, regime)
    else:
        book = json.load(open(tp)) if os.path.exists(tp) else {"open": [], "closed": []}
    out = {"stamp": meta.get("stamp"), "intraday": bool(meta.get("intraday")), "regime": regime, "positions": evals,
           "n_handeln": sum(1 for e in evals if e["level"] == "Handeln"),
           "n_beobachten": sum(1 for e in evals if e["level"] == "Beobachten"),
           "track": track_stats(book)}
    jdump(out, "a7_positions.json")
    return out


if __name__ == "__main__":
    import sys
    o = run(intraday="--intraday" in sys.argv)
    print(o["stamp"], "Handeln:", o["n_handeln"], "Beobachten:", o["n_beobachten"], "Track:", o["track"].get("n_open"), o["track"].get("n_closed"))
    for e in o["positions"]:
        print(e["ticker"], e.get("strategy"), e["level"], [a["text"] for a in e["alerts"]])
