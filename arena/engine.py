"""Evolutions-Arena – täglicher Lauf als reiner Code (läuft in GitHub Actions, ohne Claude).

Ablauf je Handelstag (nach dem Daten-Agenten):
  1. Gemeinsame Signale einmal rechnen (Kettencode, `python -m chain.run a1 a2 a10 a11 a9 a3 a8 a4 a5`) → chain_lab/out
  2. Für jedes lebende Netz: Depot bewerten, Ausstiegsregeln anwenden, Policy fragen (Klon-Netz: Genom; freies Netz:
     eigene strategy.py; Kontrollen: Zufall/SPY/Markus-Regeln), Orders ausführen (Fills aus der echten Cboe-Kette)
  3. Super-Supervisor (arena/supersupervisor.py): Kohorten nach 30 Tagen bewerten, klonen, eliminieren, Rücksprung
  4. arena/out/summary.json für das Dashboard
Feste Spielregeln für ALLE Netze (nicht änderbar durch Supervisoren): nur begrenztes Risiko, max. 10 % Kontorisiko je
Trade, Kaufkraft darf nicht negativ werden (ungehebelt), gleiche Fills/Kosten, keine Daten aus der Zukunft.
"""
import datetime as dt
import importlib.util
import json
import math
import os
import random
import sys

import numpy as np
import pandas as pd
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from chain.core import backtest, data  # noqa: E402
from chain.stages.a7_positions import chain_of, mark_option  # noqa: E402

A = os.path.join(ROOT, "arena")
NETS = os.path.join(A, "nets")
OUT = os.path.join(ROOT, "chain_lab", "out")
CFG = yaml.safe_load(open(os.path.join(A, "config.yaml")))
START_CASH = CFG["account"]
MAX_RISK = CFG["max_risk_per_trade"]


def jl(p, d=None):
    return json.load(open(p)) if os.path.exists(p) else d


def jd(o, p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(o, open(p, "w"), ensure_ascii=False, indent=1, default=str)


# ------------------------------------------------------------------ gemeinsamer Kontext (einmal pro Tag)
def context(today):
    a3 = jl(os.path.join(OUT, "a3_live.json"), {"candidates": []})
    a4 = jl(os.path.join(OUT, "a4_price.json"), {})
    a5 = jl(os.path.join(OUT, "a5_macro.json"), {})
    from chain.stages.a6_dashboard import build_ideas
    try:
        top, allideas = build_ideas(a3, a4, a5)
    except Exception:
        top, allideas = [], []
    final = {(i["ticker"], i["trigger"]): i.get("final") for i in allideas}
    cands = []
    for k, c in enumerate(a3.get("candidates", [])):
        if not c.get("legs"):
            continue
        cands.append({**{x: c.get(x) for x in ("ticker", "trigger", "trigger_name", "trigger_status", "strategy", "strategy_name",
                                              "bias", "vol", "spot", "oos_mean", "oos_win", "oos_n", "oos_t", "verdict", "score",
                                              "factors", "payoff", "legs", "gex", "signals")},
                      "final": final.get((c["ticker"], c["trigger"])), "cid": f"{c['ticker']}|{c['trigger']}|{c['strategy']}"})
    uni = sorted(set(data.CFG["universe"]["etfs"] + data.CFG["universe"]["stocks"]) if hasattr(data, "CFG") else [])
    if not uni:
        cfg = yaml.safe_load(open(os.path.join(ROOT, "chain", "config.yaml")))
        uni = cfg["universe"]["etfs"] + cfg["universe"]["stocks"]
    closes = {}
    for t in uni:
        p = data.prices(t)
        if p is not None and len(p):
            s = p["Close"].copy()
            s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
            closes[t] = s[~s.index.duplicated(keep="last")].iloc[-300:]
    C = pd.DataFrame(closes).sort_index()
    C = C[C.index <= pd.Timestamp(today)]
    return {"day": str(today), "live_day": a3.get("day"), "candidates": cands, "equity_ideas": a5.get("equity_ideas", []),
            "equity_signals": [{k: v for k, v in s.items() if k in ("id", "name", "direction", "hold", "status", "firing_now")}
                               for s in a5.get("equity_signals", [])],
            "regime": a5.get("regime"), "market": a5.get("market", {}), "price": a4.get("tickers", {}),
            "patterns": (jl(os.path.join(OUT, "a8_patterns.json"), {}) or {}).get("now", {}),
            "events": (jl(os.path.join(OUT, "a9_events.json"), {}) or {}),
            "filings": (jl(os.path.join(OUT, "a11_filings.json"), {}) or {}).get("now", {}),
            "trends": ((jl(os.path.join(OUT, "a10_social.json"), {}) or {}).get("trends") or {}).get("now", []),
            "closes": C}


# ------------------------------------------------------------------ Margin
MG = CFG.get("margin", {})


def margin_rate(today):
    """Sollzins p. a.: Fed Funds (FRED DFF, letzter Wert bis heute) + Aufschlag."""
    try:
        f = pd.read_csv(os.path.join(data.D, "macro", "fred.csv"), usecols=["date", "DFF"]).dropna()
        f = f[f["date"] <= str(today)]
        base = float(f["DFF"].iloc[-1]) / 100
    except Exception:
        base = 0.04
    return base + MG.get("rate_spread", 0.015)


def gross_equity(book):
    return sum(abs(p["shares"] * p.get("mark", p["entry"])) for p in book["open"] if p["kind"] == "equity")


def finance(book, today):
    """Taggenaue Zinsen auf negative Kasse und Leihgebühren für Leerverkäufe (Kalendertage /360)."""
    last = book.get("last_fin")
    book["last_fin"] = str(today)
    if not last:
        return
    days = (pd.Timestamp(today) - pd.Timestamp(last)).days
    if days <= 0:
        return
    cost = 0.0
    if book["cash"] < 0:
        cost += -book["cash"] * margin_rate(today) * days / 360
    short = sum(p["shares"] * p.get("mark", p["entry"]) for p in book["open"] if p["kind"] == "equity" and p["dir"] == "short")
    cost += short * MG.get("short_borrow", 0.003) * days / 360
    book["cash"] -= cost
    book["fees"] = round(book.get("fees", 0) + cost, 2)


def risk_controls(book, today, base, genome):
    """Margin Call und Max-Drawdown-Stopp – schließt Positionen erzwungen zum Schlusskurs."""
    eq, _ = mark_book(book, today, base)
    book["peak"] = max(book.get("peak", START_CASH), eq)
    reason = None
    if eq <= book["peak"] * (1 - CFG.get("max_drawdown_stop", 0.30)):
        reason = "Max-Drawdown-Stopp"
        book["halted"] = True
        for p in book["open"]:
            p["close_req"] = True
    else:
        gross = gross_equity(book)
        if gross > 0 and eq < MG.get("maintenance", 0.25) * gross:
            reason = "Margin Call"
            for p in sorted([p for p in book["open"] if p["kind"] == "equity"], key=lambda p: -abs(p["shares"] * p.get("mark", p["entry"]))):
                p["close_req"] = True
                gross -= abs(p["shares"] * p.get("mark", p["entry"]))
                if eq >= MG.get("maintenance", 0.25) * gross * 1.2:
                    break
    if reason:
        n0 = len(book["closed"])
        exits(book, today, base, genome)
        for c in book["closed"][n0:]:
            c["reason"] = reason
        book.setdefault("events", []).append({"day": str(today), "what": reason})


# ------------------------------------------------------------------ Depot
def new_book():
    return {"cash": START_CASH, "open": [], "closed": [], "curve": [], "n_orders": 0}


def mark_book(book, today, base):
    """Wert aller Positionen; gibt (equity, risk_open) zurück und aktualisiert Marken."""
    val, risk = book["cash"], 0.0
    for p in book["open"]:
        if p["kind"] == "option":
            spot = None
            px = data.prices(p["ticker"])
            if px is not None and len(px):
                spot = float(px["Close"].iloc[-1])
            m = mark_option(p, chain_of(base, p["ticker"]), spot, today) if (base and spot) else None
            if m is not None:
                p["mark"] = m["value"]
            val += p.get("mark", p["entry"]) * 100 * p["n"]
            risk += p["max_loss"] * 100 * p["n"]
        else:
            last = last_close(p["ticker"], today)
            if last:
                p["mark"] = last
            px = p.get("mark", p["entry"])
            val += p["shares"] * px * (1 if p["dir"] == "long" else -1)
            risk += abs(p["entry"] - p["stop"]) * p["shares"]
    return val, risk


def last_close(t, today):
    p = data.prices(t)
    if p is None or not len(p):
        return None
    s = p["Close"]
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    s = s[s.index <= pd.Timestamp(today)]
    return float(s.iloc[-1]) if len(s) else None


def bar(t, today):
    p = data.prices(t)
    if p is None or not len(p):
        return None
    p = p.copy()
    p.index = pd.DatetimeIndex(p.index).tz_localize(None).normalize()
    p = p[p.index <= pd.Timestamp(today)]
    return p.iloc[-1] if len(p) else None


def exits(book, today, base, genome):
    still = []
    for p in book["open"]:
        reason, px_exit = None, None
        if p["kind"] == "option":
            mode = p.get("exit_mode") or genome.get("exit_override") or backtest.STRATS.get(p["strategy"], {}).get("exit", "hold")
            front = min(pd.Timestamp(l["expiry"]) for l in p["legs"])
            days = (front - pd.Timestamp(today)).days
            m = p.get("mark", p["entry"])
            if mode in ("tp50", "tp25") and p["entry"] < 0 and (m - p["entry"]) >= (0.5 if mode == "tp50" else 0.25) * (-p["entry"]):
                reason = mode
            elif mode == "front" and days <= 1:
                reason = "Front-Verfall"
            elif mode.startswith("days") and (pd.Timestamp(today) - pd.Timestamp(p["opened"])).days >= int(mode[4:]):
                reason = mode
            if days < 0 or (reason is None and pd.Timestamp(today) >= front):
                reason = reason or "Verfall"
            if p.get("close_req"):
                reason = "Supervisor"
            if reason:
                hs = 0.0
                ch = chain_of(base, p["ticker"]) if base else None
                if ch is not None and reason != "Verfall":
                    for l in p["legs"]:
                        r = ch[(ch.expiry == l["expiry"]) & (ch.cp == l["cp"]) & ((ch.strike - l["strike"]).abs() < 1e-6)]
                        if len(r):
                            hs += 0.5 * (r.iloc[0].ask - r.iloc[0].bid) * 0.5
                proceeds = (m - hs) * 100 * p["n"]
                book["cash"] += proceeds
                pnl = proceeds - p["entry"] * 100 * p["n"]
                book["closed"].append({**{k: p[k] for k in ("id", "kind", "ticker", "strategy", "opened", "n", "entry")},
                                       "closed": str(today), "reason": reason, "pnl": round(pnl, 2),
                                       "risk": round(p["max_loss"] * 100 * p["n"], 2), "r": round(pnl / (p["max_loss"] * 100 * p["n"]), 3)})
                continue
        else:
            b = bar(p["ticker"], today)
            d = 1 if p["dir"] == "long" else -1
            if b is not None and str(b.name.date()) >= p["opened"]:
                same_day = str(b.name.date()) == p["opened"]
                op, lo, hi, cl = float(b["Open"]), float(b["Low"]), float(b["High"]), float(b["Close"])
                if same_day:
                    if p.get("exit") == "close":                     # Intraday-Einstieg, Ausstieg zum Schluss desselben Tages
                        reason, px_exit = "Schluss", cl
                elif p.get("exit") == "open":                        # Overnight: Ausstieg zur Eröffnung des Folgetags
                    reason, px_exit = "Eröffnung", op
                elif (d > 0 and lo <= p["stop"]) or (d < 0 and hi >= p["stop"]):
                    gap = (d > 0 and op <= p["stop"]) or (d < 0 and op >= p["stop"])
                    reason, px_exit = ("Stop (Gap)", op) if gap else ("Stop" if not p.get("trailed") else "Nachgezogener Stop", p["stop"])
                elif p.get("target") and ((d > 0 and hi >= p["target"]) or (d < 0 and lo <= p["target"])):
                    reason, px_exit = "Ziel", p["target"]
                elif p.get("close_req"):
                    reason, px_exit = "Supervisor", cl
                elif np.busday_count(p["opened"], str(today)) >= p.get("max_days", 20):
                    in_profit = (cl - p["entry"]) * d > 0
                    if p.get("let_run") and in_profit:
                        p["extended"] = True                         # Gewinner laufen lassen – Stop wird nachgezogen
                    else:
                        reason, px_exit = "Zeit", cl
                if not reason and p.get("trail"):                    # Stop nachziehen (nur in Gewinnrichtung)
                    tr = p["trail"]
                    if "atr" in tr:
                        h = data.prices(p["ticker"])
                        h = h[pd.DatetimeIndex(h.index).tz_localize(None).normalize() <= pd.Timestamp(today)].iloc[-15:]
                        atr = float((h["High"] - h["Low"]).mean())
                        cand = cl - d * tr["atr"] * atr
                    else:
                        cand = cl * (1 - d * tr.get("pct", 0.1))
                    if (d > 0 and cand > p["stop"]) or (d < 0 and cand < p["stop"]):
                        p["stop"], p["trailed"] = round(cand, 2), True
            if reason:
                px_exit *= 1 - 0.0005 * (1 if p["dir"] == "long" else -1)   # halbe Spanne/Slippage
                if p["dir"] == "long":
                    book["cash"] += p["shares"] * px_exit
                else:
                    book["cash"] -= p["shares"] * px_exit
                pnl = (px_exit - p["entry"]) * p["shares"] * (1 if p["dir"] == "long" else -1)
                rk = p.get("risk0") or abs(p["entry"] - p["stop"]) * p["shares"]
                book["closed"].append({**{k: p[k] for k in ("id", "kind", "ticker", "dir", "opened", "shares", "entry")},
                                       "held_days": int(np.busday_count(p["opened"], str(today))), "adds": p.get("adds", 0),
                                       "strategy": "aktie", "closed": str(today), "reason": reason, "pnl": round(pnl, 2),
                                       "risk": round(rk, 2), "r": round(pnl / rk, 3) if rk else None})
                continue
        still.append(p)
    book["open"] = still


def execute(book, orders, ctx, today, net_id):
    """Führt Orders aus. Prüft feste Spielregeln. Gibt Liste ausgeführter/abgelehnter Orders zurück."""
    log = []
    cmap = {c["cid"]: c for c in ctx["candidates"]}
    for o in orders or []:
        try:
            equity, risk_open = mark_book(book, today, None)
            lev = MG.get("intraday_leverage", 4.0) if o.get("exit") == "close" else MG.get("overnight_leverage", 2.0)
            buying_power = equity * lev - risk_open - gross_equity(book)   # Margin: Kaufkraft = Hebel × Kontowert
            rf = min(float(o.get("risk", MAX_RISK)), MAX_RISK)
            if o["type"] == "close":
                for p in book["open"]:
                    if p["id"] == o["id"]:
                        p["close_req"] = True
                log.append({"o": o, "ok": True})
                continue
            if o["type"] == "modify":                     # Stop nachziehen, Ziel/Haltedauer/Trailing ändern, Gewinner laufen lassen
                p = next((x for x in book["open"] if x["id"] == o["id"]), None)
                if not p:
                    log.append({"o": o, "ok": False, "why": "Position unbekannt"})
                    continue
                if p["kind"] == "equity" and "stop" in o:
                    d = 1 if p["dir"] == "long" else -1
                    if (o["stop"] - p["stop"]) * d < 0:
                        log.append({"o": o, "ok": False, "why": "Stop darf nur nachgezogen werden (nie Risiko erhöhen)"})
                        continue
                    p["stop"], p["trailed"] = float(o["stop"]), True
                for k in ("target", "max_days", "trail", "let_run"):
                    if k in o:
                        p[k] = o[k]
                if "exit" in o and p["kind"] == "option":
                    p["exit_mode"] = o["exit"]
                log.append({"o": o, "ok": True})
                continue
            if o["type"] == "add":                        # Pyramiding (im Gewinn) oder DCA (im Verlust, Stop nicht erreicht)
                p = next((x for x in book["open"] if x["id"] == o["id"] and x["kind"] == "equity"), None)
                px = last_close(p["ticker"], today) if p else None
                if not p or not px:
                    log.append({"o": o, "ok": False, "why": "Aktienposition unbekannt"})
                    continue
                d = 1 if p["dir"] == "long" else -1
                if (px - p["stop"]) * d <= 0:
                    log.append({"o": o, "ok": False, "why": "Stop bereits erreicht"})
                    continue
                want = int(min(rf * equity / abs(px - p["stop"]), max(buying_power, 0) / px))
                # Gesamtverlust der Position am Stop darf 10 % des Kontos nicht übersteigen
                while want > 0:
                    tot = p["shares"] + want
                    avg = (p["entry"] * p["shares"] + px * want) / tot
                    if (avg - p["stop"]) * d * tot <= MAX_RISK * equity:
                        break
                    want = int(want * 0.8)
                if want < 1:
                    log.append({"o": o, "ok": False, "why": "Risikolimit der Gesamtposition (10 %) erreicht"})
                    continue
                fill = px * (1 + 0.0005 * d)
                book["cash"] += -want * fill if d > 0 else want * fill
                p["entry"] = (p["entry"] * p["shares"] + fill * want) / (p["shares"] + want)
                p["shares"] += want
                p["risk0"] = (p.get("risk0") or 0) + abs(px - p["stop"]) * want
                p["adds"] = p.get("adds", 0) + 1
                log.append({"o": o, "ok": True, "shares": want})
                continue
            if o["type"] == "option":
                c = cmap.get(o["cid"])
                if not c:
                    log.append({"o": o, "ok": False, "why": "unbekannter Kandidat"})
                    continue
                ml = float(c["payoff"]["max_loss"]) * 100
                if not ml > 0 or not math.isfinite(ml):
                    log.append({"o": o, "ok": False, "why": "Risiko unbegrenzt/unklar"})
                    continue
                n = int(min(rf * equity, buying_power) // ml)
                if n < 1:
                    log.append({"o": o, "ok": False, "why": "zu wenig Kaufkraft/Risikobudget"})
                    continue
                entry = float(c["payoff"]["debit_fill"])
                book["cash"] -= entry * 100 * n
                book["open"].append({"id": f"{net_id}-{today}-{c['cid']}", "kind": "option", "ticker": c["ticker"],
                                     "strategy": c["strategy"], "trigger": c["trigger"], "opened": str(today), "n": n,
                                     "entry": entry, "max_loss": ml / 100, "legs": [{k: l[k] for k in ("expiry", "cp", "strike", "qty")} for l in c["legs"]]})
                log.append({"o": o, "ok": True, "n": n})
            elif o["type"] == "equity":
                t, d = o["ticker"], o["direction"]
                px = float(o["fill_px"]) if o.get("fill_px") else last_close(t, today)
                stop = float(o["stop"])
                if not px or (d == "long" and stop >= px) or (d == "short" and stop <= px):
                    log.append({"o": o, "ok": False, "why": "Stop ungültig"})
                    continue
                rps = abs(px - stop)
                sh = int(max(buying_power, 0) / px) if o.get("full") else int(min(rf * equity / rps, max(buying_power, 0) / px, max(CFG["max_position_frac"], lev if o.get("exit") == "close" else 0) * equity / px))
                if sh < 1:
                    log.append({"o": o, "ok": False, "why": "zu wenig Kaufkraft"})
                    continue
                entry = float(o["fill_px"]) if o.get("fill_px") else px * (1 + 0.0005 * (1 if d == "long" else -1))
                book["cash"] += -sh * entry if d == "long" else sh * entry
                book["open"].append({"id": f"{net_id}-{today}-{t}-{d}", "kind": "equity", "ticker": t, "dir": d, "opened": str(today),
                                     "shares": sh, "entry": entry, "stop": stop, "target": o.get("target"),
                                     "max_days": int(o.get("max_days", 20)), "trail": o.get("trail"), "let_run": bool(o.get("let_run")),
                                     "exit": o.get("exit"), "risk0": round(abs(entry - stop) * sh, 2)})
                log.append({"o": o, "ok": True, "shares": sh})
        except Exception as e:
            log.append({"o": o, "ok": False, "why": f"Fehler: {type(e).__name__}"})
    book["n_orders"] += sum(1 for x in log if x.get("ok") and x["o"]["type"] != "close")
    return log


# ------------------------------------------------------------------ Policies
def policy_chain(g, ctx, book):
    """Klon-Netz: Regeln der Original-Kette, gesteuert über das Genom (vom Supervisor änderbar)."""
    held = {p.get("ticker") for p in book["open"]}
    orders = []
    if g.get("regime_block") and ctx.get("regime") in g["regime_block"]:
        return []
    W = g.get("weights", {})
    pick = []
    for c in ctx["candidates"]:
        if c["ticker"] in held or c["strategy"] in g.get("strategies_off", []) or c["trigger"] in g.get("triggers_off", []):
            continue
        if c.get("trigger_status") not in g.get("trigger_status", ["bestätigt", "Kandidat"]):
            continue
        if g.get("use_final_gates", True) and c.get("final") not in g.get("final_ok", ["freigegeben"]):
            continue
        s = (c.get("score") or 0) + sum(W.get(k, w) - w for k, w in (c.get("factors") or {}).items())
        if s >= g.get("min_score", 2):
            pick.append((s, c))
    pick.sort(key=lambda x: -x[0])
    open_opt = sum(1 for p in book["open"] if p["kind"] == "option")
    for s, c in pick[: max(0, min(g.get("max_new_per_day", 3), g.get("max_open_options", 8) - open_opt))]:
        orders.append({"type": "option", "cid": c["cid"], "risk": g.get("risk_per_trade", 0.10)})
    if g.get("trade_equity", True):
        open_eq = sum(1 for p in book["open"] if p["kind"] == "equity")
        for e in ctx["equity_ideas"][: max(0, g.get("max_open_equity", 5) - open_eq)]:
            if e["ticker"] not in held:
                orders.append({"type": "equity", "ticker": e["ticker"], "direction": e["direction"], "stop": e["stop"],
                               "target": e.get("target"), "risk": g.get("equity_risk", 0.03), "max_days": g.get("equity_days", 20),
                               "trail": g.get("equity_trail"), "let_run": g.get("equity_let_run", False)})
    return orders


def policy_random(ctx, book, today, net_id):
    rnd = random.Random(f"{net_id}-{today}")
    held = {p.get("ticker") for p in book["open"]}
    c = [x for x in ctx["candidates"] if x["ticker"] not in held]
    if not c or len([p for p in book["open"] if p["kind"] == "option"]) >= 8:
        return []
    return [{"type": "option", "cid": rnd.choice(c)["cid"], "risk": MAX_RISK}]


def policy_spy(ctx, book):
    if book["open"] or book["closed"]:
        return []
    px = ctx["closes"]["SPY"].iloc[-1] if "SPY" in ctx["closes"] else None
    if not px:
        return []
    return [{"type": "equity", "ticker": "SPY", "direction": "long", "stop": px * 0.01, "target": None, "full": True, "max_days": 10000}]


def policy_free(net, ctx, book):
    p = os.path.join(NETS, net["id"], "strategy.py")
    if not os.path.exists(p):
        return []
    spec = importlib.util.spec_from_file_location(f"strat_{net['id']}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    view = {k: v for k, v in ctx.items()}
    view["book"] = json.loads(json.dumps({k: book[k] for k in ("cash", "open")}, default=str))
    view["genome"] = yaml.safe_load(open(os.path.join(NETS, net["id"], "genome.yaml"))) or {}
    return mod.decide(view) or []


# ------------------------------------------------------------------ Kennzahlen
def metrics(book):
    cur = pd.Series([x["equity"] for x in book["curve"]], dtype=float)
    if not len(cur):
        return {"equity": START_CASH, "ret": 0.0, "maxdd": 0.0, "score": 0.0, "n_trades": 0, "win": None, "risk_used": 0.0}
    ret = cur.iloc[-1] / START_CASH - 1
    dd = float((cur / cur.cummax() - 1).min())
    closed = book["closed"]
    win = np.mean([c["pnl"] > 0 for c in closed]) if closed else None
    ru = float(np.mean([x.get("risk", 0) / x["equity"] for x in book["curve"] if x["equity"] > 0])) if book["curve"] else 0
    r_tail = sorted([c.get("r") or 0 for c in closed])[: max(1, len(closed) // 20)] if closed else [0]
    score = ret / max(abs(dd), CFG["min_dd_for_score"])
    lev_now = gross_equity(book) / cur.iloc[-1] if cur.iloc[-1] > 0 else 0
    return {"lev": round(float(lev_now), 2), "fees": round(book.get("fees", 0), 2), "halted": bool(book.get("halted")),
            "equity": round(float(cur.iloc[-1]), 2), "ret": round(float(ret), 5), "maxdd": round(dd, 5), "score": round(float(score), 4),
            "n_trades": book["n_orders"], "n_closed": len(closed), "win": None if win is None else round(float(win), 3),
            "risk_used": round(ru, 4), "worst_r": round(float(np.mean(r_tail)), 3), "n_open": len(book["open"])}


# ------------------------------------------------------------------ Tageslauf
def run(today=None):
    from arena import supersupervisor as ss
    st = ss.load_state()
    days = data.live_days()
    # Handelstag = letzter US-Handelstag mit Cboe-Kette (nicht das UTC-Datum – GitHub-Zeitpläne laufen oft Stunden zu spät)
    today = today or (dt.date.fromisoformat(days[-1]) if days else dt.date.today())
    base = os.path.join(data.D, "live", days[-1]) if days else None
    if st.get("last_day") == str(today) and st.get("traded_day") == str(today):
        print("heute schon gelaufen")
        return st   # Nachlauf am selben Tag nur, wenn vorher wegen Pause nicht gehandelt wurde (z. B. nach dem Denk-Lauf)
    fresh = bool(days) and days[-1] == str(today)   # nur an Tagen mit frischer Cboe-Kette handeln (kein Handel auf alten Kursen)
    if fresh:
        ss.births(st, today)                  # neue Kohorte (wöchentlich) vor dem Handel
    # Ohne Claude kein Handel: Arena pausiert, wenn der letzte Denk-Lauf zu lange her ist; eine Kohorte pausiert,
    # solange ein Netz noch auf seinen Supervisor wartet (mutation_pending). Bestehende Stops/Ausstiege laufen weiter.
    hb = jl(os.path.join(A, "heartbeat.json"), {}) or {}
    last = hb.get("last_think")
    gpause = (not last) or (today - dt.date.fromisoformat(last)).days > CFG.get("think_max_age_days", 9)
    st["pause"] = {"global": gpause, "last_think": last,
                   "reason": None if not gpause else ("noch kein Claude-Denk-Lauf" if not last else f"letzter Claude-Denk-Lauf {last} – wartet auf Claude-Kontingent")}
    cpause = {}
    for c in st["cohorts"]:
        if c["status"] == "läuft":
            waiting = [i for i in c["members"] if st["nets"][i].get("mutation_pending")]
            cpause[c["id"]] = gpause or bool(waiting)
            c["paused"] = cpause[c["id"]]
            c["waiting"] = waiting
            if fresh and not cpause[c["id"]] and c.get("last_active") != str(today):
                c["active_days"] = c.get("active_days", 0) + 1
                c["last_active"] = str(today)
    ctx = context(today)
    for net in [n for n in st["nets"].values() if n["status"] in ("lebt", "weiter")]:
        retired = net["status"] == "weiter"   # Gewinner nach der Auswahl: offene Trades laufen nach ihren Regeln aus, keine neuen
        bp = os.path.join(NETS, net["id"], "book.json")
        book = jl(bp) or new_book()
        g = yaml.safe_load(open(os.path.join(NETS, net["id"], "genome.yaml"))) or {}
        finance(book, today)
        mark_book(book, today, base)
        exits(book, today, base, g)
        risk_controls(book, today, base, g)
        try:
            if net["kind"] == "klon" or net["kind"] == "markus":
                orders = policy_chain(g, ctx, book)
            elif net["kind"] == "frei":
                orders = policy_free(net, ctx, book)
            elif net["kind"] == "zufall":
                orders = policy_random(ctx, book, today, net["id"])
            elif net["kind"] == "spy":
                orders = policy_spy(ctx, book)
            else:
                orders = []
            orders = [{k: v for k, v in o.items() if k != "fill_px"} for o in (orders or [])]   # Fill-Preise setzt nur der Code
            if retired or book.get("halted"):
                orders = [o for o in orders if o.get("type") in ("modify", "close")]
            if not fresh or (cpause.get(net.get("cohort"), True) and not retired):
                orders = []
            manual = os.path.join(NETS, net["id"], "orders.json")   # Orders des Supervisors aus dem Wochenlauf
            if fresh and not cpause.get(net.get("cohort"), True) and os.path.exists(manual):
                orders = (json.load(open(manual)) or []) + orders
                os.remove(manual)
            lg = execute(book, orders, ctx, today, net["id"])
            net["last_error"] = None
        except Exception as e:
            lg, net["last_error"] = [], f"{type(e).__name__}: {e}"[:300]
        eq, rk = mark_book(book, today, base)
        if book["curve"] and book["curve"][-1]["day"] == str(today):
            book["curve"].pop()
        book["curve"].append({"day": str(today), "equity": round(eq, 2), "risk": round(rk, 2)})
        jd(book, bp)
        net["m"] = metrics(book)
        net["last_orders"] = [x for x in lg if x.get("ok")][:10]
    if fresh and any(not v for v in cpause.values()):
        st["traded_day"] = str(today)
    ss.selection(st, today)                   # Kohorten nach 21 aktiven Handelstagen: klonen/eliminieren, Rücksprung prüfen
    st["last_day"] = str(today)
    ss.save_state(st)
    try:
        from arena import health
        health.run(st, today)                 # Technik-Supervisor
    except Exception as e:
        print("health", e)
    ss.write_summary(st)
    return st


if __name__ == "__main__":
    run(dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None)
