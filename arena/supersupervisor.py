"""Super-Supervisor der Evolutions-Arena (Code-Teil; der Denk-Teil läuft wöchentlich mit Claude, siehe arena/AGENTS.md).

Aufgabe: Survival of the fittest.
  - Jede Woche wird eine Kohorte von 10 Netzen geboren (anfangs 5 Klon-Netze + 5 freie Netze nach Vorbild von Tag 1,
    später die Nachkommen der Gewinner). Dazu je Kohorte 3 Kontrollen: Markus-Regeln (eingefroren), Zufall, SPY.
  - Nach 30 Tagen wird die Kohorte bewertet: Score = Rendite / max(Max-Drawdown, 2 %). Die besten 5 kommen weiter
    (je 2 Nachkommen, ihre Supervisoren verändern sie im Wochenlauf), die schlechtesten 5 werden eliminiert.
  - Jede Auswertung ist eine Runde mit Sicherungspunkt (Genome der Gewinner in arena/checkpoints/round_N).
  - Verschlechtert sich der Score über mehrere Runden signifikant gegenüber der besten Runde: Warnung → Analyse und
    Anweisungen (Wochenlauf); bleibt es schlechter: Rücksprung zur besten Runde, alle späteren Netze werden eliminiert.
"""
import datetime as dt
import json
import math
import os
import shutil

import numpy as np
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
A = os.path.join(ROOT, "arena")
NETS, SEEDS, CK = os.path.join(A, "nets"), os.path.join(A, "seeds"), os.path.join(A, "checkpoints")
CFG = yaml.safe_load(open(os.path.join(A, "config.yaml")))
SP = os.path.join(A, "state.json")


def load_state():
    if os.path.exists(SP):
        return json.load(open(SP))
    return {"nets": {}, "cohorts": [], "rounds": [], "pending": [], "seed_source": "vorlage", "alert": {"level": "ok", "bad": 0},
            "rollbacks": [], "best_round": None, "counter": 0}


def save_state(st):
    json.dump(st, open(SP, "w"), ensure_ascii=False, indent=1, default=str)


def _new_id(st, kind):
    st["counter"] += 1
    return f"{ {'klon': 'K', 'frei': 'F', 'markus': 'M', 'zufall': 'Z', 'spy': 'S'}[kind]}{st['counter']:04d}"


def _copy_dir(src, dst, files=("genome.yaml", "strategy.py", "notes.md")):
    os.makedirs(dst, exist_ok=True)
    for f in files:
        if os.path.exists(os.path.join(src, f)):
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))


def _make(st, kind, src_dir, parent=None, gen=0, pending=False, lineage=None):
    nid = _new_id(st, kind)
    _copy_dir(src_dir, os.path.join(NETS, nid))
    note = os.path.join(NETS, nid, "notes.md")
    with open(note, "a") as f:
        f.write(f"\n## {nid} – entstanden {dt.date.today()} aus {parent or 'Vorlage ' + os.path.basename(src_dir)}\n")
    st["nets"][nid] = {"id": nid, "kind": kind, "parent": parent, "gen": gen, "status": "wartet" if pending else "neu",
                       "mutation_pending": kind in ("klon", "frei") and (pending or True), "lineage": lineage or nid,
                       "seed": os.path.basename(src_dir)}
    return nid


def births(st, today):
    """Einmal pro Kalenderwoche eine neue Kohorte."""
    wk = today.isocalendar()[:2]
    if any(tuple(c["week"]) == tuple(wk) for c in st["cohorts"]):
        return
    alive = [c for c in st["cohorts"] if c["status"] == "läuft"]
    members = []
    if st["pending"]:
        members = st["pending"][: CFG["cohort_size"]]
        st["pending"] = st["pending"][CFG["cohort_size"]:]
    elif len(alive) < max(1, CFG["cohort_days"] // 7):
        src = st["seed_source"]
        if src == "vorlage":
            for k in range(5):
                members.append(_make(st, "klon", os.path.join(SEEDS, "klon")))
            fs = sorted((d for d in os.listdir(SEEDS) if d.startswith("frei_")), key=lambda x: int(x.split("_")[1]))
            k0 = (len(st["cohorts"]) * 5) % len(fs)               # Vorlagen rotieren, damit alle Startideen zum Zug kommen
            for s in (fs[k0:] + fs[:k0])[:5]:
                members.append(_make(st, "frei", os.path.join(SEEDS, s)))
        else:   # nach Rücksprung: Klone der Gewinner der besten Runde
            r = src["round"]
            for d in sorted(os.listdir(os.path.join(CK, f"round_{r}"))):
                info = json.load(open(os.path.join(CK, f"round_{r}", d, "info.json")))
                for _ in range(2):
                    members.append(_make(st, info["kind"], os.path.join(CK, f"round_{r}", d), parent=d, gen=info["gen"] + 1,
                                         lineage=info.get("lineage")))
    if not members:
        return
    controls = [_make(st, k, os.path.join(SEEDS, k)) for k in CFG["controls"]]
    cid = len(st["cohorts"]) + 1
    for nid in members + controls:
        n = st["nets"][nid]
        n.update(status="lebt", cohort=cid, born=str(today))
        if n["kind"] in ("markus", "zufall", "spy"):
            n["mutation_pending"] = False
    st["cohorts"].append({"id": cid, "week": list(wk), "born": str(today), "members": members, "controls": controls, "status": "läuft"})


def _welch(a, b):
    a, b = np.array(a, float), np.array(b, float)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    v = a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)
    return float((a.mean() - b.mean()) / math.sqrt(v)) if v > 0 else 0.0


def selection(st, today):
    for c in st["cohorts"]:
        if c["status"] != "läuft" or c.get("active_days", 0) < CFG["cohort_trading_days"]:
            continue
        comp = [st["nets"][i] for i in c["members"]]
        elig = sorted([n for n in comp if (n.get("m") or {}).get("n_trades", 0) >= CFG["min_trades_to_win"]],
                      key=lambda n: -(n.get("m") or {}).get("score", -9))
        rest = [n for n in comp if n not in elig]
        ranked = elig + sorted(rest, key=lambda n: -(n.get("m") or {}).get("score", -9))
        win = ranked[: CFG["survivors"]]
        rnd = len(st["rounds"]) + 1
        ckd = os.path.join(CK, f"round_{rnd}")
        for n in ranked:
            n["status"] = "weiter" if n in win else "eliminiert"
            n["ended"] = str(today)
        for n in win:
            _copy_dir(os.path.join(NETS, n["id"]), os.path.join(ckd, n["id"]))
            json.dump({k: n.get(k) for k in ("kind", "gen", "lineage", "m")}, open(os.path.join(ckd, n["id"], "info.json"), "w"), default=str)
            for _ in range(2):
                st["pending"].append(_make(st, n["kind"], os.path.join(NETS, n["id"]), parent=n["id"], gen=n["gen"] + 1,
                                           pending=True, lineage=n.get("lineage")))
        for i in c["controls"]:
            st["nets"][i]["status"] = "beendet"
            st["nets"][i]["ended"] = str(today)
        c["status"] = "bewertet"
        c["ranking"] = [n["id"] for n in ranked]
        sc = [(n.get("m") or {}).get("score", 0) for n in comp]
        ctrl = {st["nets"][i]["kind"]: (st["nets"][i].get("m") or {}).get("score") for i in c["controls"]}
        st["rounds"].append({"round": rnd, "date": str(today), "cohort": c["id"], "scores": sc,
                             "mean": round(float(np.mean(sc)), 4), "best": round(float(max(sc)), 4),
                             "mean_ret": round(float(np.mean([(n.get("m") or {}).get("ret", 0) for n in comp])), 5),
                             "mean_dd": round(float(np.mean([(n.get("m") or {}).get("maxdd", 0) for n in comp])), 5),
                             "winners": [n["id"] for n in win], "controls": ctrl,
                             "klon_mean": _mean([n for n in comp if n["kind"] == "klon"]),
                             "frei_mean": _mean([n for n in comp if n["kind"] == "frei"])})
        _degradation(st, today)


def _mean(ns):
    v = [(n.get("m") or {}).get("score", 0) for n in ns]
    return round(float(np.mean(v)), 4) if v else None


def _degradation(st, today):
    R = st["rounds"]
    best = max(R, key=lambda r: r["mean"])
    st["best_round"] = best["round"]
    cur = R[-1]
    t = _welch(best["scores"], cur["scores"])
    al = st["alert"]
    if cur["round"] != best["round"] and t >= 2:
        al["bad"] = al.get("bad", 0) + 1
    else:
        al["bad"] = 0
        al["level"] = "ok"
    if al["bad"] >= CFG["degrade_rounds"]:
        al["level"] = "warnung"
        al["since"] = al.get("since") or str(today)
        al["text"] = (f"Score seit {al['bad']} Runden signifikant unter Runde {best['round']} "
                      f"(Ø {cur['mean']:+.2f} vs. {best['mean']:+.2f}, t={t:.1f}) – Analyse und Anweisungen im Wochenlauf")
    if al["bad"] >= CFG["degrade_rounds"] + CFG["rollback_after"]:
        killed = []
        for n in st["nets"].values():
            if n["kind"] in ("klon", "frei") and n["status"] in ("lebt", "wartet"):
                n["status"], n["ended"] = "eliminiert (Rücksprung)", str(today)
                killed.append(n["id"])
        for c in st["cohorts"]:
            if c["status"] == "läuft":
                c["status"] = "abgebrochen (Rücksprung)"
                for i in c["controls"]:
                    st["nets"][i]["status"] = "beendet"
        st["pending"] = []
        st["seed_source"] = {"round": best["round"]}
        st["rollbacks"].append({"date": str(today), "to_round": best["round"], "killed": len(killed)})
        al.update(level="rücksprung", bad=0, text=f"Rücksprung zu Runde {best['round']}: {len(killed)} Netze eliminiert, Neustart mit deren Gewinnern")


def write_summary(st):
    from arena.engine import jl
    nets = []
    curves = {}
    for n in st["nets"].values():
        if n["status"] in ("wartet",):
            continue
        b = jl(os.path.join(NETS, n["id"], "book.json"), {}) or {}
        cv = b.get("curve", [])
        if n["status"] == "lebt" or (n.get("ended") and n["ended"] >= str(dt.date.today() - dt.timedelta(days=45))):
            curves[n["id"]] = [[x["day"], x["equity"]] for x in cv]
        g = {}
        try:
            g = yaml.safe_load(open(os.path.join(NETS, n["id"], "genome.yaml"))) or {}
        except Exception:
            pass
        trades = None
        if n["status"] == "lebt" or n["id"] in curves:
            EXT = {"tp50": "bis 50 % des max. Gewinns, spätestens Verfall", "tp25": "bis 25 % des max. Gewinns, spätestens Verfall",
                   "hold": "bis Verfall", "front": "bis kurz vor Verfall des vorderen Monats", "pre": "bis vor die Earnings",
                   "post": "bis nach den Earnings"}

            def plan(p):
                if p["kind"] == "option":
                    from chain.core import backtest
                    m = p.get("exit_mode") or backtest.STRATS.get(p.get("strategy"), {}).get("exit", "hold")
                    exp = min(l["expiry"] for l in p["legs"])
                    return (f"{m[4:]} Tage" if m.startswith("days") else EXT.get(m, m)) + f" (Verfall {exp})"
                if p.get("exit") == "open":
                    return "über Nacht – Ausstieg zur Eröffnung"
                if p.get("exit") == "close":
                    return "intraday – Ausstieg zum Schluss"
                if p.get("max_days", 20) >= 1000:
                    return "dauerhaft (kaufen und halten)"
                t = f"{p.get('max_days', 20)} Handelstage"
                if p.get("let_run"):
                    t += ", Gewinner dürfen länger laufen"
                if p.get("trail"):
                    tr = p["trail"]
                    t += f", Stop wird nachgezogen ({tr['atr']}× ATR)" if "atr" in tr else f", Stop wird nachgezogen ({tr.get('pct', 0) * 100:.0f} %)"
                return t + (" – verlängert" if p.get("extended") else "")

            def pos(p):
                d = {"ticker": p["ticker"], "kind": p["kind"], "opened": p["opened"], "entry": round(p["entry"], 2),
                     "mark": round(p.get("mark", p["entry"]), 2), "plan": plan(p), "id": p["id"],
                     "held": int(np.busday_count(p["opened"], str(dt.date.today()))), "adds": p.get("adds", 0), "trailed": p.get("trailed", False)}
                if p["kind"] == "option":
                    d.update(what=p.get("strategy"), n=p["n"], risk=round(p["max_loss"] * 100 * p["n"], 0),
                             legs=" / ".join(f"{'+' if l['qty'] > 0 else '−'}{abs(l['qty'])} {l['cp']}{l['strike']:g} {l['expiry'][5:]}" for l in p["legs"]),
                             pnl=round((p.get("mark", p["entry"]) - p["entry"]) * 100 * p["n"], 0))
                else:
                    sg = 1 if p["dir"] == "long" else -1
                    d.update(what=p["dir"], n=p["shares"], stop=p["stop"], target=p.get("target"),
                             risk=round(abs(p["entry"] - p["stop"]) * p["shares"], 0),
                             pnl=round((p.get("mark", p["entry"]) - p["entry"]) * p["shares"] * sg, 0))
                return d
            trades = {"open": [pos(p) for p in b.get("open", [])],
                      "closed": [{k: c.get(k) for k in ("ticker", "kind", "strategy", "dir", "opened", "closed", "reason", "pnl", "r", "held_days", "adds")}
                                 for c in b.get("closed", [])[-12:]][::-1],
                      "cash": round(b.get("cash", 0), 0)}
        nets.append({**{k: n.get(k) for k in ("id", "kind", "parent", "gen", "status", "cohort", "born", "ended", "lineage", "last_error")},
                     "trades": trades,
                     "m": n.get("m"), "idea": g.get("idee") or g.get("name"), "changes": g.get("changes", [])[-3:]})
    alive = [n for n in nets if n["status"] == "lebt"]
    comp = [n for n in alive if n["kind"] in ("klon", "frei")]
    hp = os.path.join(A, "out", "health.json")
    out = {"updated": str(dt.date.today()), "round": len(st["rounds"]), "pause": st.get("pause"),
           "health": json.load(open(hp)) if os.path.exists(hp) else None, "cohorts": st["cohorts"][-8:], "rounds": st["rounds"][-30:],
           "alert": st["alert"], "rollbacks": st["rollbacks"], "best_round": st["best_round"], "pending": len(st["pending"]),
           "alive": len(comp), "controls_alive": len(alive) - len(comp), "nets": nets, "curves": curves, "rules": CFG,
           "pop": {"mean_ret": round(float(np.mean([(n["m"] or {}).get("ret", 0) for n in comp])), 5) if comp else None,
                   "mean_dd": round(float(np.mean([(n["m"] or {}).get("maxdd", 0) for n in comp])), 5) if comp else None,
                   "mean_score": round(float(np.mean([(n["m"] or {}).get("score", 0) for n in comp])), 4) if comp else None}}
    os.makedirs(os.path.join(A, "out"), exist_ok=True)
    json.dump(out, open(os.path.join(A, "out", "summary.json"), "w"), ensure_ascii=False, default=str)
