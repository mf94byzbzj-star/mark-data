"""Agent 6 – Dashboard: führt die Ergebnisse aller Agenten zu einer Datei zusammen und baut die Trade-Ideen.

Ausgabe: chain_lab/dashboard/index.html (statisch, aus chain/dashboard/app.html) + data/summary.json
Veröffentlichung als Artifact (Dateien: index.html, data/summary.json), Modus patch.
"""
import datetime as dt
import json
import os
import shutil

from chain.core import data
from chain.stages.common import CFG, LAB, jload

DASH = os.path.join(LAB, "dashboard")
APP = os.path.join(data.ROOT, "chain", "dashboard", "app.html")


def _notes():
    p = os.path.join(LAB, "out", "notes.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _slim(o):
    """Backtest-Ergebnisse ohne Ballast fürs Dashboard."""
    def cut(r):
        return {k: r.get(k) for k in ("id", "name", "dir", "h", "status", "edge", "edge_t", "split", "yearly")} | {
            "n": (r.get("oos") or {}).get("n"), "oos_mean": (r.get("oos") or {}).get("mean"), "oos_win": (r.get("oos") or {}).get("win"),
            "base_mean": (r.get("base_oos") or {}).get("mean"), "is_n": (r.get("is") or {}).get("n")}
    o = json.loads(json.dumps(o, default=str))
    for part in (o, o.get("trends") or {}, o.get("posts") or {}):
        if isinstance(part, dict) and part.get("tests"):
            part["tests"] = [cut(r) for r in part["tests"]]
    return o


def _patterns(show_price):
    a8 = jload("a8_patterns.json", {})
    if not a8:
        return {}
    stats = [{k: v for k, v in x.items() if k not in ("yearly", "is", "base_is", "oos_h1", "oos_h2")} for x in a8.get("patterns", [])]
    return {"split": a8.get("split"), "n_events": a8.get("n_events"), "counts": a8.get("counts"), "stats": stats,
            "now": {t: v for t, v in (a8.get("now") or {}).items() if t in show_price}}


def _proposals():
    import yaml
    p = os.path.join(LAB, "research", "proposals.yaml")
    return (yaml.safe_load(open(p)) or []) if os.path.exists(p) else []


def build_ideas(a3, a4, a5):
    j4 = {(j["ticker"], j["trigger"]): j for j in a4.get("judgements", [])}
    g5 = {(g["ticker"], g["trigger"]): g for g in a5.get("gates", [])}
    ideas = []
    for c in a3.get("candidates", []):
        if not c.get("legs"):
            continue
        k = (c["ticker"], c["trigger"])
        pa, gate = j4.get(k), g5.get(k)
        final = c["verdict"]
        size = 1.0
        if gate and gate["status"] == "blockiert":
            final = "abgelehnt"
        elif gate and gate["status"] == "halbe Größe":
            size = 0.5
        if pa and pa["verdict"] == "passt nicht" and final == "freigegeben":
            final = "Vorsicht"
        n = int(c.get("contracts", 0) * size)
        ideas.append({**{x: c[x] for x in ("ticker", "trigger", "trigger_name", "trigger_status", "strategy", "strategy_name",
                                             "bias", "vol", "spot", "oos_mean", "oos_win", "oos_n", "oos_t", "legs", "payoff",
                                             "checks", "greeks_per_contract", "signals", "ticker_hist", "expr", "score")},
                      "a3_verdict": c["verdict"], "price_action": pa, "macro": gate, "final": final,
                      "contracts": n, "risk_usd": round(n * c["payoff"]["max_loss"] * 100, 0),
                      "gex": {k2: v for k2, v in (c.get("gex") or {}).items() if k2 != "profile"}})
    rank = {"freigegeben": 0, "Vorsicht": 1, "abgelehnt": 2}
    ideas.sort(key=lambda i: (rank[i["final"]], -(i.get("score") or 0), -(i["oos_mean"] or 0)))   # Score enthält gelernte Gewichte
    # je Ticker nur die beste Idee in den Top-Vorschlägen
    seen, top, used = set(), [], 0.0
    cap = CFG["account_size"] * CFG["risk"].get("max_total_option_risk", 0.10)
    for i in ideas:
        if i["final"] == "abgelehnt" or i["ticker"] in seen or len(top) >= CFG["risk"]["max_ideas"]:
            continue
        if i["contracts"] < 1 or used + i["risk_usd"] > cap:
            continue
        top.append(i)
        seen.add(i["ticker"])
        used += i["risk_usd"]
    return top, ideas


def run(publish_dir=None):
    a1 = jload("a1_triggers.json", {})
    a2 = jload("a2_tickers.json", {"triggers": []})
    a3 = jload("a3_live.json", {"candidates": []})
    a4 = jload("a4_price.json", {})
    a5 = jload("a5_macro.json", {})
    top, ideas = build_ideas(a3, a4, a5)
    # schlanke Trigger-Liste (ohne Grid-Details)
    trig = []
    for t in a1.get("triggers", []):
        t2 = {k: v for k, v in t.items() if k not in ("grid_trials",)}
        trig.append(t2)
    show = {i["ticker"] for i in ideas} | set((a4.get("tickers") or {})) | set((a5.get("price_extra") or {}))
    gex = {k: v for k, v in (a3.get("gex") or {}).items() if k in show}
    s = {
        "generated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "live_day": a3.get("day"), "account": CFG["account_size"], "risk": CFG["risk"],
        "research": {"split": a1.get("split"), "n_trades": a1.get("n_trades"), "start": a1.get("data_start"),
                     "end": a1.get("data_end"), "latest_feature_date": a1.get("latest_feature_date")},
        "top": top, "ideas": ideas, "triggers": trig, "strategies": a1.get("strategies", {}),
        "ticker_rules": a2.get("triggers", []),
        "gex": gex,
        "price": {**(a5.get("price_extra") or {}), **(a4.get("tickers") or {})},
        "macro": {k: a5.get(k) for k in ("regime", "market", "market_date", "regime_history", "regime_perf", "series",
                                          "equity_vs_option", "equity_signals", "equity_ideas")},
        "notes": _notes(),
        "audit": jload("a0_audit.json", {}),
        "positions": jload("a7_positions.json", {}),
        "patterns": _patterns(show_price=set((a4.get("tickers") or {})) | set((a5.get("price_extra") or {}))),
        "proposals": _proposals(),
        "events": jload("a9_events.json", {}),
        "learn": {k: v for k, v in jload("a0_learn.json", {}).items()},
        "social": _slim(jload("a10_social.json", {})),
        "filings": _slim(jload("a11_filings.json", {})),
    }
    d = publish_dir or DASH
    os.makedirs(os.path.join(d, "data"), exist_ok=True)
    shutil.copy(APP, os.path.join(d, "index.html"))
    with open(os.path.join(d, "data", "summary.json"), "w") as f:
        json.dump(s, f, ensure_ascii=False, separators=(",", ":"), default=str)
    return s


if __name__ == "__main__":
    s = run()
    print("Top-Ideen:", [(i["ticker"], i["strategy"], i["final"]) for i in s["top"]])
