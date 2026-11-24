"""Agent 11 – SEC-Filings: bestätigt oder schwächt die Richtung der anderen Agenten. Erzeugt KEINE eigenen Trades.

Daten: chain_data/alt/insider/<T>.csv.gz (Form 4, SEC EDGAR, kostenlos). Ereignistag = Meldedatum (öffentlich bekannt),
Einstieg erster Schlusskurs danach.
Signale (Literatur):
  I1 Insider-Kauf          Kauf am offenen Markt (Code P) durch Direktor/Vorstand, ≥ 10.000 $        (Lakonishok/Lee 2001)
  I2 Cluster-Kauf          ≥ 2 verschiedene Insider kaufen innerhalb von 30 Tagen                    (Alldredge/Blank 2019)
  I3 Opportunistischer Kauf Käufer ohne Routine-Muster (nicht jedes Jahr im selben Monat)            (Cohen/Malloy/Pomorski 2012)
  I4 CEO/CFO-Kauf          Kauf durch CEO oder CFO                                                    (Wang/Shin/Francis 2012)
  I5 Cluster-Verkauf       ≥ 3 Insider verkaufen außerhalb von 10b5-1-Plänen innerhalb von 30 Tagen  (bärisch, schwach belegt)
Je Signal Haltedauer 21 und 63 Handelstage, marktbereinigt, gegen Basis „beliebiger Tag“.
Gewichtung für die Kette (fest):
  - nur Signale mit Status „bestätigt“ verändern Bewertungen (+1 gleichgerichtet, −1 gegenläufig); „Kandidat“ = nur Hinweis
  - Zeithorizont: Signal gilt 30 (h21) bzw. 90 (h63) Kalendertage; bei Optionen mit < 30 Tagen Laufzeit und h63 halbes Gewicht
  - überstimmt nie Agent 9 oder den Regime-Filter von Agent 5
Ausgabe: chain_lab/out/a11_filings.json
"""
import os

import pandas as pd

from chain.core import data, eventstudy
from chain.stages.common import CFG, jdump, jload

A = os.path.join(data.D, "alt", "insider")
H = (21, 63)
VALID_DAYS = {21: 30, 63: 90}
NAMES = {"I1": "Insider-Kauf", "I2": "Cluster-Kauf (≥ 2 Insider)", "I3": "Opportunistischer Insider-Kauf",
         "I4": "CEO/CFO-Kauf", "I5": "Cluster-Verkauf (≥ 3 Insider, ohne 10b5-1)"}
DIRS = {"I1": 1, "I2": 1, "I3": 1, "I4": 1, "I5": -1}


def load(t):
    p = os.path.join(A, f"{t}.csv.gz")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, dtype={"acc": str, "owner_cik": str, "title": str, "code": str})
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["filed"])
    df["title"] = df["title"].fillna("")
    return df


def _cluster(ev, k, days=30):
    """Ereignisse, an denen innerhalb der letzten `days` Tage ≥ k verschiedene Insider gehandelt haben (Abstand ≥ days)."""
    ev = ev.sort_values("filed")
    out, last = [], None
    for i, r in ev.iterrows():
        win = ev[(ev["filed"] > r["filed"] - pd.Timedelta(days=days)) & (ev["filed"] <= r["filed"])]
        if win["owner_cik"].nunique() >= k and (last is None or r["filed"] - last >= pd.Timedelta(days=days)):
            out.append(r["filed"])
            last = r["filed"]
    return out


def signals(df):
    """Ereignistage je Signal (Meldedatum)."""
    ins = df[(df["director"] == 1) | (df["officer"] == 1)]
    buys = ins[(ins["code"] == "P") & (ins["value"].fillna(0) >= 10_000)]
    sells = ins[(ins["code"] == "S") & (ins["plan"].fillna(0) == 0)]
    out = {"I1": sorted(set(buys["filed"])), "I2": _cluster(buys, 2), "I5": _cluster(sells, 3)}
    # Routine: Insider hat in jedem der 3 Vorjahre im selben Kalendermonat gehandelt (nur Vergangenheit)
    allt = df[df["code"].isin(["P", "S"])].assign(y=lambda x: x["trade_date"].dt.year, m=lambda x: x["trade_date"].dt.month)
    seen = set(zip(allt["owner_cik"], allt["y"], allt["m"]))
    opp = [r["filed"] for _, r in buys.iterrows() if pd.notna(r["trade_date"]) and not all(
        (r["owner_cik"], r["trade_date"].year - k, r["trade_date"].month) in seen for k in (1, 2, 3))]
    out["I3"] = sorted(set(opp))
    ceo = buys[buys["title"].str.contains(r"CEO|Chief Exec|CFO|Chief Fin|President", case=False, regex=True)]
    out["I4"] = sorted(set(ceo["filed"]))
    return out


def run():
    stocks = CFG["universe"]["stocks"]
    ev = {}
    for t in stocks:
        df = load(t)
        if df is not None and len(df):
            ev[t] = signals(df)
    if not ev:
        out = {"status": "keine Daten", "tests": [], "now": {}, "coverage": 0}
        jdump(out, "a11_filings.json")
        return out
    tests = []
    for h in H:
        base = {d: eventstudy.baseline(list(ev), h, direction=d) for d in (1, -1)}
        for sid in NAMES:
            d = DIRS[sid]
            tr = pd.concat([eventstudy.fwd_excess(t, e[sid], h, d) for t, e in ev.items() if e.get(sid)] or [pd.DataFrame()], ignore_index=True)
            tests.append({"id": f"{sid}_h{h}", "sig": sid, "name": NAMES[sid], "dir": d, "h": h, "trades": tr, "base": base[d]})
    res = eventstudy.evaluate(tests, CFG["research"])
    best = {}
    for r in res:
        if r["status"] in ("bestätigt", "Kandidat"):
            cur = best.get(r["sig"])
            if not cur or (r["status"] == "bestätigt", r.get("edge_t") or 0) > (cur["status"] == "bestätigt", cur.get("edge_t") or 0):
                best[r["sig"]] = r
    today = pd.Timestamp.today().normalize()
    now = {}
    for t, e in ev.items():
        act = []
        for sid, dates in e.items():
            if not dates:
                continue
            last = max(dates)
            r = best.get(sid)
            h = r["h"] if r else 21
            if today - last <= pd.Timedelta(days=VALID_DAYS[h]):
                act.append({"sig": sid, "name": NAMES[sid], "dir": DIRS[sid], "filed": str(last.date()), "h": h,
                            "status": r["status"] if r else "verworfen",
                            "edge": r.get("edge") if r else None, "edge_t": r.get("edge_t") if r else None})
        if act:
            now[t] = act
    last_filed = max((max(d) for e in ev.values() for d in e.values() if d), default=None)
    out = {"status": "ok", "coverage": len(ev), "last_filing": str(last_filed.date()) if last_filed is not None else None,
           "tests": res, "now": now}
    jdump(out, "a11_filings.json")
    return out


def confirm(ticker, direction, dte=None, a11=None):
    """Urteil für eine Idee: Liste (Zeichen, Text, Gewicht). direction: +1 bullisch, −1 bärisch."""
    a11 = a11 if a11 is not None else jload("a11_filings.json", {})
    out = []
    for s in (a11.get("now") or {}).get(ticker, []):
        same = s["dir"] == direction
        txt = f"SEC: {s['name']} (gemeldet {s['filed']}, Horizont {s['h']} HT)"
        if s["status"] == "bestätigt":
            w = 1.0 if same else -1.0
            if dte is not None and dte < 30 and s["h"] == 63:
                w *= 0.5
            out.append(("✓" if same else "✗", txt + (" – bestätigt die Richtung" if same else " – spricht gegen die Richtung"), w))
        elif s["status"] == "Kandidat":
            out.append(("i", txt + " – Signal nur Kandidat, kein Gewicht", 0))
    return out


if __name__ == "__main__":
    o = run()
    print(o.get("status"), o.get("coverage"), "Ticker, letzte Meldung", o.get("last_filing"))
    for r in o.get("tests", []):
        print(r["id"], r["status"], r.get("oos", {}).get("n"), r.get("edge"), r.get("edge_t"))
