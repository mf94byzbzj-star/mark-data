"""Agent 10 – Posts & Google Trends.

Teil A – Google Trends (chain_data/alt/trends/<T>.csv, wöchentlicher Suchindex „<TICKER> stock“, 5 Jahre):
  ASVI = log(SVI) − log(Median der 8 Vorwochen)                                        (Da/Engelberg/Gao 2011)
  G1 Aufmerksamkeits-Sprung → long, 5 und 20 Handelstage   (Kurzfrist-Druck durch Privatanleger)
  G2 Aufmerksamkeits-Sprung → short, 60 Handelstage        (spätere Umkehr laut Da/Engelberg/Gao)
  Schwelle = 90.-Perzentil der ASVI im In-Sample-Zeitraum (keine Zukunftsdaten). Ereignistag = Ende der Woche.
Teil B – Posts wichtiger Personen (chain_lab/research/posts.json, vom Abendlauf per Recherche gepflegt):
  Liste {date, person, text, tickers, sector, url}. Backtest je Person: Rendite der genannten Ticker nach dem Post
  (1 und 5 Tage, marktbereinigt) gegen Basis. Erst ab 40 Posts mit Ticker-Bezug bewertbar → bis dahin nur Hinweis.
  (X/Twitter-API ist kostenpflichtig; Historie: Trump-Archive, siehe AGENTS.md)
Trades: nur Signale mit Status bestätigt/Kandidat erzeugen Aktien-Ideen (über Agent 5, mit Stop/Ziel); alles andere = Info.
Ausgabe: chain_lab/out/a10_social.json
"""
import json
import os

import numpy as np
import pandas as pd

from chain.core import data, eventstudy, stats
from chain.stages.common import CFG, RES, jdump

TR = os.path.join(data.D, "alt", "trends")


def load_trends():
    out = {}
    if not os.path.isdir(TR):
        return out
    for f in os.listdir(TR):
        if f.endswith(".csv"):
            df = pd.read_csv(os.path.join(TR, f), parse_dates=["week"])
            if len(df) > 20:
                s = df.set_index("week")["svi"].astype(float)
                out[f[:-4]] = s
    return out


def asvi(s):
    med = s.shift(1).rolling(8, min_periods=6).median()
    return np.log(s.clip(lower=1)) - np.log(med.clip(lower=1))


def trends_part():
    T = load_trends()
    if not T:
        return {"status": "keine Daten", "tests": [], "now": []}
    A = {t: asvi(s) for t, s in T.items()}
    allv = pd.concat([a.dropna().rename(t) for t, a in A.items()], axis=1)
    weeks = allv.index
    split = stats.split_date(weeks, CFG["research"]["in_sample_frac"])
    thr = float(np.nanpercentile(allv[allv.index < split].values, 90))
    ev = {t: list(a[a >= thr].index + pd.Timedelta(days=6)) for t, a in A.items()}   # Ende der Woche (Samstag)
    tests = []
    for sid, d, h, name in (("G1", 1, 5, "Suchsprung → long 1 Woche"), ("G1", 1, 20, "Suchsprung → long 1 Monat"),
                            ("G2", -1, 60, "Suchsprung → Umkehr short 3 Monate")):
        base = eventstudy.baseline(list(T), h, since=str(weeks.min().date()), direction=d)
        tr = pd.concat([eventstudy.fwd_excess(t, e, h, d) for t, e in ev.items() if e] or [pd.DataFrame()], ignore_index=True)
        tests.append({"id": f"{sid}_h{h}", "sig": sid, "name": name, "dir": d, "h": h, "trades": tr, "base": base})
    res = eventstudy.evaluate(tests, CFG["research"])
    last_week = max(s.index.max() for s in T.values())
    now = []
    for t, a in A.items():
        if a.index.max() == last_week and a.iloc[-1] >= thr:
            now.append({"ticker": t, "asvi": round(float(a.iloc[-1]), 2), "svi": float(T[t].iloc[-1]), "week": str(last_week.date())})
    now.sort(key=lambda x: -x["asvi"])
    top = sorted(({"ticker": t, "asvi": round(float(a.iloc[-1]), 2)} for t, a in A.items() if a.index.max() == last_week and a.iloc[-1] == a.iloc[-1]),
                 key=lambda x: -x["asvi"])[:10]
    return {"status": "ok", "coverage": len(T), "threshold": round(thr, 3), "last_week": str(last_week.date()),
            "tests": res, "now": now, "top": top}


def posts_part():
    p = os.path.join(RES, "posts.json")
    posts = json.load(open(p)) if os.path.exists(p) else []
    by = {}
    for x in posts:
        if x.get("tickers"):
            by.setdefault(x["person"], []).append(x)
    tests, info = [], []
    for person, lst in by.items():
        n = len(lst)
        if n < 40:
            info.append({"person": person, "n": n, "status": "zu wenig Historie (< 40 Posts mit Ticker)"})
            continue
        for h in (1, 5):
            tr = []
            for x in lst:
                d = x.get("dir", 1)
                for t in x["tickers"]:
                    tr.append(eventstudy.fwd_excess(t, [pd.Timestamp(x["date"]) - pd.Timedelta(days=1)], h, d))
            tr = pd.concat(tr, ignore_index=True) if tr else pd.DataFrame()
            tk = sorted({t for x in lst for t in x["tickers"]})
            tests.append({"id": f"P_{person}_h{h}", "sig": "P", "name": f"Posts {person} (Richtung laut Post)", "dir": 1, "h": h,
                          "trades": tr, "base": eventstudy.baseline(tk, h)})
    res = eventstudy.evaluate(tests, CFG["research"]) if tests else []
    recent = sorted(posts, key=lambda x: x.get("date", ""), reverse=True)[:12]
    return {"tests": res, "people": info, "recent": recent}


def run():
    out = {"trends": trends_part(), "posts": posts_part()}
    jdump(out, "a10_social.json")
    return out


def active_signals(a10):
    """Aktien-Signale für Agent 5: nur bestätigte/Kandidat-Tests mit heutigem Auslöser."""
    tr = a10.get("trends", {})
    good = [r for r in tr.get("tests", []) if r.get("status") in ("bestätigt", "Kandidat")]
    out = []
    for r in sorted(good, key=lambda r: (r["status"] != "bestätigt", -(r.get("edge_t") or 0)))[:1]:
        for n in tr.get("now", [])[:5]:
            out.append({"ticker": n["ticker"], "direction": "long" if r["dir"] > 0 else "short", "status": r["status"],
                        "reason": f"Google-Trends-Suchsprung (ASVI {n['asvi']:+.2f}) – {r['name']}",
                        "evidence": f"OOS Mehrwert {r['edge']:+.2%} je Trade gegenüber beliebigem Tag (t={r['edge_t']:.1f}, {r['status']})",
                        "source": r["id"]})
    return out


if __name__ == "__main__":
    o = run()
    t = o["trends"]
    print("Trends:", t.get("status"), t.get("coverage"), "Schwelle", t.get("threshold"))
    for r in t.get("tests", []):
        print(r["id"], r["status"], r.get("oos", {}).get("n"), r.get("edge"), r.get("edge_t"))
    print("jetzt:", t.get("now", [])[:5])
