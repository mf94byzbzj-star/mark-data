"""Agent 3 – Live-Trades: Regeln von Agent 1+2 auf das heutige Marktumfeld anwenden und verifizieren.

1. Heutige Merkmale je Ticker aus der Cboe-Kette (IV-Oberfläche, Forward Factor, Skew …) + Historie
2. Welche Trigger (inkl. Ticker-Regeln von Agent 2) feuern heute?
3. Konkreten Trade aus der echten Kette bauen: Verfälle, Strikes, Bid/Ask, Fill, max. Verlust/Gewinn, Breakeven,
   Gewinnwahrscheinlichkeit, Greeks, Positionsgröße (2 % Risiko)
4. Verifikation: Liquidität (OI, Spread), Gamma-Exposure (Regime, Call-/Put-Wall, Flip), erwartete Bewegung,
   Earnings im Zeitfenster, Belegstufe, Erwartungswert
Ausgabe: chain_lab/out/a3_live.json
"""
import math
import os

import numpy as np
import pandas as pd

from chain.core import backtest, data, features
from chain.core.bs import price as bsprice
from chain.core.gex import gex_profile
from chain.core.surface import compact
from chain.stages import a9_events, a11_filings
from chain.stages.common import yload, CACHE, CFG, jdump, jload

LC = CFG["live"]


def live_surface(t, chain, day):
    c = chain.rename(columns={})
    c = c[(c.bid > 0) | (c.ask > 0)].copy()
    c["date"] = pd.Timestamp(day)
    c["expiry"] = pd.to_datetime(c["expiry"])
    s = compact(c[["date", "expiry", "cp", "strike", "bid", "ask", "iv", "delta"]])
    if len(s):
        s["date"] = pd.to_datetime(s["date"])
        s["expiry"] = pd.to_datetime(s["expiry"])
    return s


def live_features(mkt):
    day = data.live_days()[-1]
    rows, chains = [], {}
    for t in data.UNIV:
        ch, _ = data.live_chain(t, day)
        if ch is None or ch.empty:
            continue
        chains[t] = ch
        try:
            s = live_surface(t, ch, day)
            f = features.ticker_features(t, mkt, extra=s, since=pd.Timestamp(day) - pd.Timedelta(days=420))
        except Exception as e:
            print("live", t, e)
            continue
        if f is None or pd.Timestamp(day) not in f.index:
            continue
        r = f.loc[pd.Timestamp(day)].to_dict()
        r["ticker"] = t
        rows.append(r)
    return day, pd.DataFrame(rows), chains


# ------------------------------------------------------------------ Trade aus echter Kette
def _pick_exp(ch, day, target, lo, hi):
    e = pd.Series(pd.to_datetime(ch["expiry"].unique()))
    d = (e - pd.Timestamp(day)).dt.days
    ok = e[(d >= lo) & (d <= hi)]
    if ok.empty:
        return None
    return ok.iloc[(((ok - pd.Timestamp(day)).dt.days) - target).abs().argsort().iloc[0]]


BAND = 0.10   # zulässige Abweichung vom Ziel-Delta, um an ein Gamma-Level zu rücken


def _gamma_short(c, cp, target, g, spot, key):
    """Short-Strike an Gamma-Levels ausrichten (innerhalb ±BAND um das Ziel-Delta, nur liquide Strikes).
    Put: höchster Strike an/unter der Put-Wall (Händler-Absicherung = Unterstützung).
    Call: niedrigster Strike an/über der Call-Wall (Widerstand).
    Bei Debit-Spreads (bcs/bps) umgekehrt: Gewinnziel an der Wall begrenzen."""
    band = c[(c.delta.abs() >= abs(target) - BAND) & (c.delta.abs() <= abs(target) + BAND)
             & (c.open_interest >= LC["min_open_interest"])]
    base = c.iloc[(c.delta - target).abs().argsort().iloc[0]]
    if g is None or band.empty:
        return base, None
    pw, cw = g.get("put_wall"), g.get("call_wall")
    if key in ("bcs",) and cp > 0 and cw and cw > spot:
        x = band[band.strike <= cw]
        if len(x):
            r = x.iloc[(x.strike - cw).abs().argsort().iloc[0]]
            return r, f"Short-Call {r.strike:g} an der Call-Wall {cw:g} (Gewinnziel = Widerstand)"
    if key in ("bps",) and cp < 0 and pw and pw < spot:
        x = band[band.strike >= pw]
        if len(x):
            r = x.iloc[(x.strike - pw).abs().argsort().iloc[0]]
            return r, f"Short-Put {r.strike:g} an der Put-Wall {pw:g} (Gewinnziel = Unterstützung)"
    if cp < 0 and pw and pw < spot:
        x = band[band.strike <= pw]
        if len(x):
            r = x.sort_values("strike").iloc[-1]
            if r.strike == base.strike:
                return r, f"Short-Put {r.strike:g} ({r.delta:+.2f}Δ) liegt an/unter der Put-Wall {pw:g}"
            return r, f"Short-Put auf {r.strike:g} ({r.delta:+.2f}Δ) gesetzt, an/unter Put-Wall {pw:g} (Delta-Ziel wäre {base.strike:g})"
    if cp > 0 and cw and cw > spot:
        x = band[band.strike >= cw]
        if len(x):
            r = x.sort_values("strike").iloc[0]
            if r.strike == base.strike:
                return r, f"Short-Call {r.strike:g} ({r.delta:+.2f}Δ) liegt an/über der Call-Wall {cw:g}"
            return r, f"Short-Call auf {r.strike:g} ({r.delta:+.2f}Δ) gesetzt, an/über Call-Wall {cw:g} (Delta-Ziel wäre {base.strike:g})"
    return base, "kein Gamma-Level im Delta-Band – Strike nach Delta"


def _gamma_atm(c, spot, g):
    """ATM-Strike: bei positivem Gamma an den Strike mit dem größten Gamma (Pinning) innerhalb ±1,5 %."""
    base = c.iloc[(c.strike - spot).abs().argsort().iloc[0]]
    if not g or g.get("regime") != "positiv" or not g.get("profile"):
        return base, None
    prof = [(k, v) for k, v in g["profile"] if abs(k / spot - 1) <= 0.015 and v > 0]
    if not prof:
        return base, None
    k = max(prof, key=lambda x: x[1])[0]
    x = c[(c.strike - k).abs() < 1e-6]
    if x.empty:
        return base, None
    return x.iloc[0], f"Mittel-Strike {k:g} am Gamma-Maximum (Pinning bei positivem Gamma, Ni/Pearson/Poteshman 2005)"


def build_live(t, key, ch, day, spot, g=None):
    spec = backtest.STRATS[key]
    ef = _pick_exp(ch, day, 30, 20, 42)   # wie im Backtest
    if ef is None:
        return None, "kein passender Front-Verfall"
    eb = None
    if any(l[0] == "b" for l in spec["legs"]):
        eb = _pick_exp(ch, day, 60, 45, 80)
        if eb is None or eb <= ef:
            return None, "kein passender Back-Verfall"
    legs, notes = [], []
    shift = {}   # Verschiebung je Seite (C/P), damit Flügel die Breite behalten
    for exp_k, cp, ks, qty in spec["legs"]:
        e = ef if exp_k == "f" else eb
        c = ch[(pd.to_datetime(ch.expiry) == e) & (ch.cp == ("C" if cp > 0 else "P"))]
        c = c[(c.bid > 0) & (c.ask > 0)]
        if c.empty:
            return None, f"Kette für Leg {len(legs)+1} leer"
        note = None
        if isinstance(ks, tuple):
            same = legs[ks[1]]["strike"]
            x = c[(c.strike - same).abs() < 1e-6]
            if x.empty:
                return None, f"Strike {same:g} im zweiten Verfall fehlt"
            r = x.iloc[0]
        elif ks == "atm":
            r, note = _gamma_atm(c, spot, g)
        elif qty < 0:
            r, note = _gamma_short(c, cp, ks, g, spot, key)
            base = c.iloc[(c.delta - ks).abs().argsort().iloc[0]]
            shift[cp] = r.strike - base.strike
        else:
            base = c.iloc[(c.delta - ks).abs().argsort().iloc[0]]
            k = base.strike + shift.get(cp, 0.0)
            if key in ("bcs", "bps"):   # Long-Leg der Debit-Spreads bleibt am Ziel-Delta
                k = base.strike
            r = c.iloc[(c.strike - k).abs().argsort().iloc[0]]
        if note:
            notes.append(note)
        mid = (r.bid + r.ask) / 2
        legs.append({"expiry": str(pd.Timestamp(e).date()), "dte": int((pd.Timestamp(e) - pd.Timestamp(day)).days),
                     "cp": "C" if cp > 0 else "P", "strike": float(r.strike), "qty": qty,
                     "bid": float(r.bid), "ask": float(r.ask), "mid": float(mid), "iv": float(r.iv),
                     "delta": float(r.delta), "gamma": float(r.gamma), "theta": float(r.theta), "vega": float(r.vega),
                     "oi": float(r.open_interest), "volume": float(r.volume),
                     "rel_spread": float((r.ask - r.bid) / mid) if mid > 0 else 9.0})
    if len({(l["expiry"], l["cp"], l["strike"]) for l in legs}) < len(legs):
        return None, "Strikes fallen zusammen (Kette zu grob)"
    # Struktur-Plausibilität nach der Gamma-Verschiebung
    ks = [l["strike"] for l in legs]
    if key == "pcs" and not ks[0] > ks[1] or key == "ccs" and not ks[0] < ks[1] \
            or key == "ic" and not (ks[1] < ks[0] < ks[2] < ks[3]):
        return None, "Struktur nach Gamma-Anpassung ungültig"
    return (legs, notes), None


def pick_variant(c, tr, legs, spot, notes):
    """P002: Kennzahlen der Backtest-Variante, deren Flügelbreite (in % des Kurses) dem Live-Trade am nächsten liegt.
    Breiter als ~4,5 % -> Basisstruktur (Delta-Flügel) bleibt maßgeblich."""
    vs = tr.get("variants") or {}
    if not vs or not spot:
        return
    w = 0.0
    for l in legs:
        if l["qty"] > 0:
            sh = [x for x in legs if x["qty"] < 0 and x["cp"] == l["cp"] and x["expiry"] == l["expiry"]]
            if sh:
                w = max(w, min(abs(l["strike"] - x["strike"]) for x in sh) / spot)
    if w <= 0 or w > 0.045:
        c["wing_pct"] = round(w, 4)
        return
    k = min(vs, key=lambda v: abs(vs[v]["wing"] - w))
    o = vs[k].get("oos") or {}
    if not o.get("n"):
        return
    c.update(wing_pct=round(w, 4), bt_variant=k, bt_variant_name=vs[k]["name"],
             oos_mean_base=c.get("oos_mean"), oos_win_base=c.get("oos_win"), oos_n_base=c.get("oos_n"),
             oos_mean=o.get("mean"), oos_win=o.get("win"), oos_n=o.get("n"), oos_t=o.get("t"))
    notes.append(f"Backtest-Kennzahlen aus Variante „{vs[k]['name']}“ (Live-Flügel {w*100:.1f} % des Kurses): "
                 f"OOS Ø {o.get('mean', 0)*100:+.1f} % je Risiko, Treffer {o.get('win', 0)*100:.0f} %, n = {o.get('n')}")


def fit_wings(legs, ch, budget, spot, day):
    """Long-Flügel schrittweise an die Short-Strikes heranrücken, bis das Risiko je Kontrakt ins Budget passt."""
    legs = [dict(l) for l in legs]
    longs = [i for i, l in enumerate(legs) if l["qty"] > 0]
    for _ in range(40):
        ps = payoff_stats(legs, spot, day)
        if ps["max_loss"] * 100 <= budget:
            ws = ", ".join(f"{legs[i]['cp']} {legs[i]['strike']:g}" for i in longs)
            return legs, f"Flügel verengt auf {ws}, damit das Risiko je Kontrakt ins Budget ({budget:,.0f} $) passt"
        # breitesten Flügel um einen Strike nach innen
        best = None
        for i in longs:
            l = legs[i]
            sh = [x for x in legs if x["qty"] < 0 and x["cp"] == l["cp"] and x["expiry"] == l["expiry"]]
            if not sh:
                continue
            w = abs(l["strike"] - sh[0]["strike"])
            if best is None or w > best[1]:
                best = (i, w, sh[0]["strike"])
        if best is None:
            return None, None
        i, w, ks = best
        l = legs[i]
        c = ch[(ch.expiry == l["expiry"]) & (ch.cp == l["cp"]) & (ch.bid > 0) & (ch.ask > 0)]
        inner = c[(c.strike - ks).abs() < w - 1e-9]
        inner = inner[(inner.strike < ks) if l["strike"] < ks else (inner.strike > ks)]
        if inner.empty:
            return None, None
        r = inner.iloc[(inner.strike - ks).abs().argsort().iloc[-1]]   # nächstweiterer Strike nach innen
        mid = (r.bid + r.ask) / 2
        l.update(strike=float(r.strike), bid=float(r.bid), ask=float(r.ask), mid=float(mid), iv=float(r.iv),
                 delta=float(r.delta), gamma=float(r.gamma), theta=float(r.theta), vega=float(r.vega),
                 oi=float(r.open_interest), volume=float(r.volume), rel_spread=float((r.ask - r.bid) / mid) if mid > 0 else 9.0)
    return None, None


def payoff_stats(legs, spot, day):
    """Max. Verlust/Gewinn, Breakevens, Gewinnwahrscheinlichkeit (lognormal mit ATM-IV) am ersten Verfall."""
    debit = sum(l["qty"] * l["mid"] for l in legs)
    fill_cost = sum(0.5 * (l["ask"] - l["bid"]) * CFG["research"]["slippage_frac"] for l in legs)
    debit_fill = debit + fill_cost
    e1 = min(l["expiry"] for l in legs)
    T1 = max((pd.Timestamp(e1) - pd.Timestamp(day)).days, 1) / 365
    atm_iv = np.median([l["iv"] for l in legs if l["iv"] > 0.01] or [0.3])
    grid = np.linspace(spot * math.exp(-4 * atm_iv * math.sqrt(T1)), spot * math.exp(4 * atm_iv * math.sqrt(T1)), 801)
    val = np.zeros_like(grid)
    for l in legs:
        cp = 1 if l["cp"] == "C" else -1
        if l["expiry"] == e1:
            val += l["qty"] * np.maximum(cp * (grid - l["strike"]), 0)
        else:  # längerer Verfall: BS-Wert am ersten Verfall mit heutiger IV
            T2 = (pd.Timestamp(l["expiry"]) - pd.Timestamp(e1)).days / 365
            val += l["qty"] * bsprice(grid, l["strike"], T2, l["iv"], cp)
    pnl = val - debit_fill
    multi_exp = len({l["expiry"] for l in legs}) > 1
    max_loss = float(-pnl.min()) if not multi_exp else float(max(debit_fill, -pnl.min()))
    if multi_exp:
        max_loss = max(debit_fill, 0.01)
    max_gain = float(pnl.max())
    sgn = np.sign(pnl)
    be = [float(grid[i] - pnl[i] * (grid[i + 1] - grid[i]) / (pnl[i + 1] - pnl[i])) for i in np.where(np.diff(sgn) != 0)[0]]
    # lognormale Verteilung (risikoneutral, ohne Drift)
    z = (np.log(grid / spot) + 0.5 * atm_iv ** 2 * T1) / (atm_iv * math.sqrt(T1))
    w = np.exp(-0.5 * z ** 2)
    w /= w.sum()
    pop = float(w[pnl > 0].sum())
    ev_model = float((w * pnl).sum())
    em = spot * atm_iv * math.sqrt(T1)
    return {"debit_mid": round(debit, 3), "debit_fill": round(debit_fill, 3), "max_loss": round(max_loss, 3),
            "max_gain": round(max_gain, 3), "breakevens": [round(b, 2) for b in be], "pop": round(pop, 3),
            "ev_model": round(ev_model, 3), "expected_move": round(em, 2), "first_expiry": e1,
            "curve": [[round(float(x), 2), round(float(y), 3)] for x, y in zip(grid[::20], pnl[::20])]}


def verify(c, g, f, legs, ps, spot, day):
    """Prüfungen von Agent 3. score > 0 = freigegeben, Gründe als Liste."""
    checks, score, fac = [], 0, {}

    def add(key, sym, text, default):
        """Lernbarer Faktor: Gewicht aus chain_lab/research/weights.json (vom Supervisor-Lernmodul kalibriert)."""
        nonlocal score
        w = WEIGHTS.get(key, default)
        checks.append((sym, text))
        score += w
        fac[key] = w
    vol = backtest.STRATS[c["strategy"]]["vol"]
    # Liquidität
    bad = [l for l in legs if l["oi"] < LC["min_open_interest"] or l["rel_spread"] > LC["max_rel_spread"]]
    if bad:
        checks.append(("✗", f"Liquidität schwach: {len(bad)} Leg(s) mit OI < {LC['min_open_interest']} oder Spread > {LC['max_rel_spread']:.0%}"))
        score -= 2
    else:
        add("liq_ok", "✓", f"Liquidität ok (min. OI {min(l['oi'] for l in legs):.0f}, max. Spread {max(l['rel_spread'] for l in legs):.0%})", 1)
    # Belegstufe
    if c["trigger_status"] == "bestätigt":
        add("trig_A", "✓", "Trigger Out-of-Sample bestätigt (Stufe A)", 2)
    else:
        add("trig_B", "~", "Trigger nur Kandidat (Stufe B) – kleinere Größe", 0)
    # Erwartungswert aus Backtest
    if (c.get("oos_mean") or 0) <= 0:
        checks.append(("✗", "Backtest-Erwartungswert ≤ 0"))
        score -= 3
    # GEX
    if g:
        shorts = [l for l in legs if l["qty"] < 0]
        if vol == "short":
            if g["regime"] == "negativ":
                add("gex_neg_sv", "✗", f"Negatives Gamma-Regime (Netto-GEX {g['net_gex']/1e6:,.0f} Mio. $/1 %) – Händler verstärken Bewegungen", -1)
            else:
                add("gex_pos_sv", "✓", "Positives Gamma-Regime – Händler dämpfen Bewegungen", 1)
            for l in shorts:
                if l["cp"] == "P" and g["put_wall"] and l["strike"] > g["put_wall"] * 1.005:
                    add("pwall_above", "~", f"Short-Put {l['strike']:g} liegt über der Put-Wall {g['put_wall']:g}", 0)
                elif l["cp"] == "P" and g["put_wall"]:
                    add("pwall_ok", "✓", f"Short-Put {l['strike']:g} unter/an Put-Wall {g['put_wall']:g} (Unterstützung)", 0.5)
                if l["cp"] == "C" and g["call_wall"] and l["strike"] < g["call_wall"] * 0.995:
                    add("cwall_below", "~", f"Short-Call {l['strike']:g} liegt unter der Call-Wall {g['call_wall']:g}", 0)
                elif l["cp"] == "C" and g["call_wall"]:
                    add("cwall_ok", "✓", f"Short-Call {l['strike']:g} über/an Call-Wall {g['call_wall']:g} (Widerstand)", 0.5)
        else:
            if g["regime"] == "negativ":
                add("gex_neg_lv", "✓", "Negatives Gamma-Regime begünstigt Long-Vola", 1)
            else:
                add("gex_pos_lv", "~", "Positives Gamma-Regime dämpft Bewegungen (gegen Long-Vola)", 0)
        if g.get("flip"):
            checks.append(("i", f"Gamma-Flip bei {g['flip']:.2f} (Kurs {'darüber' if spot > g['flip'] else 'darunter'})"))
    # Agent 9: Makro-Termine und Nachrichten im Zeitfenster
    fe = pd.Timestamp(ps["first_expiry"])
    for s_, t_, w_ in a9_events.check(c["ticker"], day, fe, vol, EV["cal"], EV["news"]):
        if w_ <= -3:                      # feste Sperre von Agent 9 – nicht lernbar
            checks.append((s_, "Ereignis: " + t_))
            score += w_
            c["event_block"] = True
        elif w_ < 0:
            add("event_warn", s_, "Ereignis: " + t_, w_)
        else:
            add("event_in_life" if s_ == "i" else "event_near", s_, "Ereignis: " + t_, 0)
    # Agent 11: SEC-Filings bestätigen/schwächen die Richtung (nur bestätigte Signale haben Gewicht)
    bias_dir = {"bull": 1, "bear": -1}.get(backtest.STRATS[c["strategy"]]["bias"])
    if bias_dir:
        dte = (fe - pd.Timestamp(day)).days
        for s_, t_, w_ in a11_filings.confirm(c["ticker"], bias_dir, dte, EV["a11"]):
            if w_ > 0:
                add("sec_confirm", s_, t_, w_)
            elif w_ < 0:
                add("sec_against", s_, t_, w_)
            else:
                checks.append((s_, t_))
    # Earnings im Zeitfenster
    ed = data.earnings(c["ticker"])
    hit = [e for e in ed if pd.Timestamp(day) <= e <= fe]
    if hit and c["strategy"] not in ("pre_earn", "earn_ifly"):
        add("earn_in", "✗", f"Earnings am {hit[0].date()} innerhalb der Laufzeit", -2)
    # erwartete Bewegung vs. Short-Strikes
    if c["strategy"] in ("pcs", "ccs", "ic"):
        for l in legs:
            if l["qty"] < 0:
                k = abs(l["strike"] - spot) / max(ps["expected_move"], 1e-6)
                checks.append(("i", f"Short-{'Put' if l['cp'] == 'P' else 'Call'} {l['strike']:g}: {k:.2f}× erwartete Bewegung entfernt"))
    if ps["max_loss"] <= 0:
        score -= 5
    # gelernte Gewichte je Trigger und Strategie (Start 0; vom Lernmodul aus der Live-Leistung kalibriert)
    for k in (f"trig:{c['trigger']}", f"strat:{c['strategy']}"):
        w = WEIGHTS.get(k, 0)
        fac[k] = w
        if w:
            checks.append(("✓" if w > 0 else "✗", f"Lernmodul: {k.replace('trig:', 'Trigger ').replace('strat:', 'Strategie ')} live {'besser' if w > 0 else 'schlechter'} als erwartet (Gewicht {w:+.1f})"))
            score += w
    verdict = "freigegeben" if score >= 2 else ("Vorsicht" if score >= 0 else "abgelehnt")
    if c.get("event_block") and verdict == "freigegeben":
        verdict = "Vorsicht"
    c["factors"] = fac
    return verdict, score, [{"s": s, "text": t} for s, t in checks]


EV = {"cal": [], "news": {}, "a11": {}}
WEIGHTS = {}


def run():
    EV["cal"], EV["news"] = a9_events.calendar(), a9_events.news()
    EV["a11"] = jload("a11_filings.json", {})
    WEIGHTS.clear()
    WEIGHTS.update({k: v["w"] for k, v in (yload("weights.yaml", {}) or {}).get("weights", {}).items()})
    a1 = jload("a1_triggers.json")
    a2 = jload("a2_tickers.json", {"triggers": []})
    mkt = features.market()
    day, F, chains = live_features(mkt)
    summ = data.live_summary(day)
    rules = {r["trigger"]: r for r in a2["triggers"]}
    active = [t for t in a1["triggers"] if t.get("status") in ("bestätigt", "Kandidat")]
    gex = {}
    for t, ch in chains.items():
        sp = float(summ.loc[t, "spot"]) if t in summ.index else None
        if sp:
            try:
                gex[t] = gex_profile(ch, sp, day)
            except Exception as e:
                print("gex", t, e)
    cands = []
    for tr in active:
        r2 = rules.get(tr["id"])
        expr = r2["final_expr"] if r2 else tr["when"]
        stats_oos = (r2 or {}).get("final", {}).get("oos") or tr.get("oos", {})
        rank = {x["ticker"]: x for x in (r2 or {}).get("tickers", [])}
        try:
            m = F.eval(expr).fillna(False).astype(bool) if len(F) else pd.Series(dtype=bool)
        except Exception as e:
            print("expr", tr["id"], e)
            continue
        for _, f in F[m.values].iterrows():
            t = f["ticker"]
            if t in rank and rank[t]["score"] < 0 and rank[t]["n"] >= 5:
                continue  # Ticker historisch schlecht für diesen Trigger
            sp = float(summ.loc[t, "spot"]) if t in summ.index else float(f.get("close", np.nan))
            res, err = build_live(t, tr["strategy"], chains[t], day, sp, gex.get(t))
            legs, gnotes = res if res else (None, [])
            c = {"ticker": t, "trigger": tr["id"], "trigger_name": tr["name"], "trigger_status": tr["status"],
                 "strategy": tr["strategy"], "strategy_name": backtest.STRATS[tr["strategy"]]["name"],
                 "bias": backtest.STRATS[tr["strategy"]]["bias"], "vol": backtest.STRATS[tr["strategy"]]["vol"],
                 "expr": expr, "spot": sp, "oos_mean": stats_oos.get("mean"), "oos_win": stats_oos.get("win"),
                 "oos_n": stats_oos.get("n"), "oos_t": stats_oos.get("t"),
                 "ticker_hist": rank.get(t),
                 "signals": {k: (None if pd.isna(f.get(k)) else float(f.get(k))) for k in
                             ("ff", "slope", "iv30", "iv_rank", "ivrv", "skew25", "skew_z", "rv20", "d2e", "dist200", "rsi2")}}
            if legs is None:
                c.update(verdict="abgelehnt", checks=[{"s": "✗", "text": err}], score=-9)
                cands.append(c)
                continue
            ps = payoff_stats(legs, sp, day)
            budget = CFG["account_size"] * CFG["risk"]["options_per_trade"] * (1.0 if c["trigger_status"] == "bestätigt" else 0.5)
            if ps["max_loss"] * 100 > budget and tr["strategy"] in ("pcs", "ccs", "ic", "ifly", "earn_ifly"):
                legs2, note = fit_wings(legs, chains[t], budget, sp, day)
                if legs2:
                    legs, ps = legs2, payoff_stats(legs2, sp, day)
                    gnotes.append(note)
            pick_variant(c, tr, legs, sp, gnotes)
            verdict, score, checks = verify(c, gex.get(t), f, legs, ps, sp, day)
            checks = [{"s": "◆", "text": n} for n in gnotes] + checks
            risk_usd = ps["max_loss"] * 100
            n = int(budget // risk_usd) if risk_usd > 0 else 0
            if n == 0:
                checks.append({"s": "✗", "text": f"Risiko je Kontrakt {risk_usd:,.0f} $ über dem Budget von {budget:,.0f} $ – nicht handelbar"})
                verdict = "abgelehnt"
            greeks = {k: round(sum(l["qty"] * l[k] for l in legs) * 100, 2) for k in ("delta", "gamma", "theta", "vega")}
            c.update(legs=legs, payoff=ps, verdict=verdict, score=score, checks=checks, contracts=n,
                     risk_usd=round(n * risk_usd, 0), greeks_per_contract=greeks,
                     exp_ret_bt=(c["oos_mean"] or 0) * ps["max_loss"] * 100 * n, gex=gex.get(t))
            cands.append(c)
    cands.sort(key=lambda c: (-{"freigegeben": 2, "Vorsicht": 1, "abgelehnt": 0}[c["verdict"]], -c["score"], -(c.get("oos_mean") or 0)))
    # Übersichtstabellen (Volatility / Relative Value / Earnings)
    cols = ["ticker", "close", "iv30", "iv_rank", "iv_pct", "ivrv", "vrp", "rv20", "ff", "ff_s", "slope", "skew25", "skew_z",
            "pskew", "cskew", "d2e", "dist200", "dist50", "ret20", "atr_pct", "spr_atm", "is_etf", "max_dte"]
    table = F[[c for c in cols if c in F]].copy() if len(F) else pd.DataFrame()
    F.to_pickle(os.path.join(CACHE, "live_features.pkl"))
    out = {"day": day, "n_tickers": int(len(F)), "candidates": cands,
           "table": table.replace({np.nan: None}).to_dict("records") if len(table) else [],
           "gex": gex, "summary": summ.reset_index().replace({np.nan: None}).to_dict("records") if len(summ) else []}
    jdump(out, "a3_live.json")
    return out


if __name__ == "__main__":
    o = run()
    print(o["day"], o["n_tickers"], "Ticker,", len(o["candidates"]), "Kandidaten")
    for c in o["candidates"][:20]:
        print(c["ticker"], c["trigger"], c["strategy"], c["verdict"], c.get("score"), c.get("payoff", {}).get("max_loss"))
