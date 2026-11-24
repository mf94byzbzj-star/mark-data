"""Supervisor (Agent 0) – automatische Prüfung der gesamten Kette.

Prüft nach jedem Lauf: Datenaktualität und -abdeckung, Datenqualität, Backtest-Plausibilität, Lookahead,
Konsistenz Backtest ↔ Live, Trade-Strukturen, Positionsgrößen, Dashboard-Datei, Laufzeitfehler.
Schweregrade: kritisch (Ergebnis unbrauchbar → sofort beheben), warnung (prüfen), info.
Ausgabe: chain_lab/out/a0_audit.json (Grundlage für Fehlerbehebung und Optimierungsvorschläge).
"""
import datetime as dt
import json
import os
import re

import numpy as np
import pandas as pd

from chain.core import backtest, data
from chain.stages.common import CFG, OUT, all_trades, jdump, jload, yload

F = []


def add(sev, area, text, fix=None):
    F.append({"sev": sev, "area": area, "text": text, "fix": fix})


def us_trading_days_between(a, b):
    return int(np.busday_count(a, b))


def check_data():
    today = dt.date.today()
    days = data.live_days()
    if not days:
        add("kritisch", "Daten", "Keine Cboe-Live-Kette vorhanden", "Workflow chain-daily prüfen")
    else:
        lag = us_trading_days_between(dt.date.fromisoformat(days[-1]), today)
        if lag > 2:
            add("kritisch", "Daten", f"Live-Kette veraltet: {days[-1]} ({lag} Handelstage alt)", "Workflow chain-daily / Cboe-Abruf prüfen")
        summ = data.live_summary()
        miss = [t for t in data.UNIV if t not in summ.index]
        if len(miss) > 5:
            add("warnung", "Daten", f"{len(miss)} Ticker ohne Live-Kette: {', '.join(miss[:12])}", "Cboe-Rate-Limit/Symbole prüfen")
    sd = os.path.join(data.D, "surface")
    st = json.load(open(os.path.join(sd, "_state.json"))) if os.path.exists(os.path.join(sd, "_state.json")) else {}
    spy = data.surface("SPY")
    if spy is None:
        add("kritisch", "Daten", "Keine DoltHub-Historie für SPY")
    else:
        first, last = spy["date"].min().date(), spy["date"].max().date()
        if not st.get("clone_complete"):
            add("info", "Daten", f"Historie wird noch geladen: SPY ab {first} (Ziel 2019)")
        lag = us_trading_days_between(last, today)
        if lag > 4:
            add("warnung", "Daten", f"DoltHub-Historie endet am {last} ({lag} Handelstage)", "Workflow chain-dolt prüfen")
        # Qualität: Anteil Ausreißer, die die Bereinigung entfernt hat
        raw = pd.read_csv(os.path.join(sd, "SPY.csv.gz"))
        drop = 1 - len(spy) / max(len(raw), 1)
        if drop > 0.05:
            add("warnung", "Datenqualität", f"SPY: {drop:.0%} der Oberflächen-Zeilen als fehlerhaft verworfen")
    for k in ("IDX_VIX", "SPY"):
        px = data.prices(k.replace("IDX_", "^") if k.startswith("IDX_") else k)
        if px is None:
            add("kritisch", "Daten", f"Kurse fehlen: {k}")
        elif us_trading_days_between(px.index[-1].date(), today) > 3:
            add("warnung", "Daten", f"Kurse {k} enden am {px.index[-1].date()}")
    fr = data.fred()
    if fr.empty:
        add("warnung", "Daten", "FRED-Makrodaten fehlen")
    logs = os.path.join(data.D, "logs")
    if os.path.isdir(logs):
        for f in os.listdir(logs):
            txt = open(os.path.join(logs, f)).read()
            n = len(re.findall(r"FEHLER|Traceback|fehler ", txt))
            if n:
                add("warnung", "Daten-Pipeline", f"{f}: {n} Fehlermeldung(en) im letzten Lauf")


def check_backtests():
    try:
        t = all_trades()
    except Exception as e:
        add("kritisch", "Backtest", f"Backtests nicht berechenbar: {e}")
        return
    if t.empty:
        add("kritisch", "Backtest", "Keine Trades simuliert")
        return
    for k, g in t.groupby("strat"):
        r = g["ret"]
        ext = (r.abs() > 5).mean()
        if ext > 0.01:
            add("warnung", "Backtest", f"{k}: {ext:.1%} der Trades mit |Rendite| > 500 % des Risikos (Datenfehler?)",
                "Ausreißer prüfen, ggf. Plausibilitätsfilter verschärfen")
        if r.min() < -1.6 and backtest.STRATS[k]["exit"] != "front":
            # Payoff-Fehler nur, wenn schon der Brutto-Verlust (vor Kosten) das definierte Risiko übersteigt
            gross = (r + g["cost_ror"]).min() if "cost_ror" in g else r.min()
            if gross < -1.05:
                add("warnung", "Backtest", f"{k}: Verlust {gross:.2f}× Risiko vor Kosten – mehr als das definierte Risiko")
            else:
                add("info", "Kosten", f"{k}: schlechtester Trade {r.min():.2f}× Risiko, allein durch Kosten (brutto ≥ {gross:.2f}×)")
        cr = g["cost_ror"].median()
        if cr > 0.35:
            add("info", "Kosten", f"{k}: Kosten im Median {cr:.0%} des Risikos – Edge muss sehr groß sein")
        if r.isna().any():
            add("warnung", "Backtest", f"{k}: {int(r.isna().sum())} Trades ohne Ergebnis")
    n_by = t.groupby("strat").size()
    low = n_by[n_by < 100]
    if len(low):
        add("info", "Backtest", "Wenig Trades: " + ", ".join(f"{k} ({v})" for k, v in low.items()))


def check_research():
    for tr in yload("triggers.yaml", []) or []:
        if "fut_" in str(tr.get("when", "")):
            add("kritisch", "Lookahead", f"Trigger {tr['id']} nutzt Zukunftsdaten", "Trigger korrigieren")
        if tr.get("strategy") not in backtest.STRATS:
            add("kritisch", "Forschung", f"Trigger {tr['id']}: unbekannte Strategie {tr.get('strategy')}")
        if not tr.get("source"):
            add("warnung", "Forschung", f"Trigger {tr['id']} ohne Quelle")
        g = tr.get("grid") or {}
        if sum(len(v) for v in g.values()) > 4 * max(len(g), 1):
            add("warnung", "Mehrfachtests", f"Trigger {tr['id']}: Grid größer als erlaubt")
    a1 = jload("a1_triggers.json", {})
    for tr in a1.get("triggers", []):
        if tr.get("status") == "Fehler":
            add("kritisch", "Forschung", f"Trigger {tr['id']}: {tr.get('error')}")
        o, b = tr.get("oos", {}), tr.get("base_oos", {})
        if tr.get("status") == "bestätigt" and o.get("mean", 0) > 0.5:
            add("warnung", "Plausibilität", f"{tr['id']} bestätigt mit Ø {o['mean']:+.0%} je Trade – ungewöhnlich hoch, Daten prüfen")
    for r in yload("ticker_rules.yaml", []) or []:
        if "fut_" in str(r.get("rule", "")):
            add("kritisch", "Lookahead", f"Ticker-Regel {r['id']} nutzt Zukunftsdaten")


def check_patterns():
    a8 = jload("a8_patterns.json", {})
    if not a8:
        add("warnung", "Chartmuster", "Agent 8 hat keine Ergebnisse geliefert")
        return
    if a8.get("n_events", 0) < 10000:
        add("warnung", "Chartmuster", f"Nur {a8.get('n_events')} Muster-Ereignisse – Kursdaten unvollständig?")
    for x in a8.get("patterns", []):
        o = x.get("oos", {})
        if o.get("n", 0) and abs(o.get("mean") or 0) > 1.5:
            add("warnung", "Chartmuster", f"{x['id']}: Ø {o['mean']:+.2f} R – unplausibel hoch, Detektor prüfen")
        if x["status"] in ("bestätigt", "Kandidat") and (x.get("edge_vs_base") or 0) <= 0:
            add("kritisch", "Chartmuster", f"{x['id']}: Status {x['status']} ohne Mehrwert gegenüber Einstieg ohne Muster")
    lib = {e["id"] for e in (yload("chart_patterns.yaml", []) or [])}
    found = {x["id"] for x in a8.get("patterns", [])}
    miss = found - lib
    if miss:
        add("warnung", "Chartmuster", "Muster ohne Bibliothekseintrag (Quelle fehlt): " + ", ".join(sorted(miss)))


def check_live():
    a3 = jload("a3_live.json", {})
    acct = CFG["account_size"]
    for c in a3.get("candidates", []):
        legs, p = c.get("legs"), c.get("payoff")
        if not legs:
            continue
        tag = f"{c['ticker']} {c['strategy']}"
        if p["max_loss"] <= 0:
            add("kritisch", "Live-Trade", f"{tag}: max. Verlust ≤ 0 (Struktur fehlerhaft)")
        if c.get("risk_usd", 0) > acct * CFG["risk"]["options_per_trade"] * 1.01:
            add("kritisch", "Risiko", f"{tag}: Risiko {c['risk_usd']:.0f} $ über dem Limit")
        if c["verdict"] == "freigegeben" and c.get("contracts", 0) == 0:
            add("warnung", "Risiko", f"{tag}: freigegeben, aber 0 Kontrakte (Risiko je Kontrakt > Budget)")
        if any(l["rel_spread"] > 0.5 for l in legs) and c["verdict"] != "abgelehnt":
            add("warnung", "Liquidität", f"{tag}: Leg mit Spread > 50 % nicht abgelehnt")
        exps = {l["expiry"] for l in legs}
        if c["strategy"].startswith("cal") or c["strategy"] == "dcal":
            if len(exps) != 2:
                add("kritisch", "Live-Trade", f"{tag}: Calendar ohne zwei Verfälle")
        d = [l["dte"] for l in legs]
        if min(d) < 14:
            add("warnung", "Live-Trade", f"{tag}: Verfall in {min(d)} Tagen – kürzer als im Backtest")


def check_dashboard():
    p = os.path.join(data.LAB, "dashboard", "data", "summary.json")
    if not os.path.exists(p):
        add("warnung", "Dashboard", "summary.json fehlt")
        return
    sz = os.path.getsize(p)
    if sz > 3e6:
        add("warnung", "Dashboard", f"summary.json {sz/1e6:.1f} MB – Ladezeit")
    try:
        s = json.load(open(p))
        for k in ("top", "ideas", "triggers", "macro", "price"):
            if k not in s:
                add("kritisch", "Dashboard", f"Feld {k} fehlt in summary.json")
    except Exception as e:
        add("kritisch", "Dashboard", f"summary.json ungültig: {e}")


def check_alt():
    a11 = jload("a11_filings.json", {})
    if a11.get("status") != "ok":
        add("info", "SEC-Filings", "Noch keine Insider-Daten (Workflow chain-alt lädt sie)")
    elif a11.get("last_filing") and (pd.Timestamp.today() - pd.Timestamp(a11["last_filing"])).days > 10:
        add("warnung", "SEC-Filings", f"Letzte Insider-Meldung {a11['last_filing']} – Abruf prüfen", "Workflow chain-alt / data.sec.gov")
    tr = (jload("a10_social.json", {}).get("trends") or {})
    if tr.get("status") != "ok":
        add("info", "Google Trends", "Noch keine Trends-Daten (Workflow chain-alt lädt sie; Google sperrt zeitweise)")
    elif tr.get("coverage", 0) < 50:
        add("warnung", "Google Trends", f"Nur {tr['coverage']} Aktien mit Trends-Daten", "Abruf läuft schrittweise weiter")
    ln = jload("a0_learn.json", {})
    if not ln:
        add("warnung", "Lernmodul", "Lernmodul lief nicht")


def check_events():
    ev = jload("a9_events.json", {})
    if not ev:
        add("warnung", "Ereignisse", "Agent 9 hat keinen Kalender geschrieben")
        return
    if ev.get("news_stale"):
        add("warnung", "Ereignisse", "Nachrichtenlage nicht von heute – Short-Vola-Freigaben ohne News-Prüfung", "news.json im Abendlauf schreiben")
    fut = [e for e in yload("events.yaml", []) or [] if str(e["date"]) >= ev["date"] and e.get("type") == "FOMC"]
    if len(fut) < 2:
        add("warnung", "Ereignisse", "Weniger als 2 künftige FOMC-Termine im Kalender", "events.yaml per Recherche ergänzen")
    cpi = [e for e in yload("events.yaml", []) or [] if str(e["date"]) >= ev["date"] and e.get("type") == "CPI"]
    if len(cpi) < 2:
        add("warnung", "Ereignisse", "Weniger als 2 künftige CPI-Termine im Kalender", "events.yaml per Recherche ergänzen")


def run(run_log=None):
    F.clear()
    for fn in (check_data, check_backtests, check_research, check_patterns, check_live, check_events, check_alt, check_dashboard):
        try:
            fn()
        except Exception as e:
            add("kritisch", "Supervisor", f"Prüfung {fn.__name__} abgebrochen: {e}")
    if run_log:
        for line in run_log:
            if "FEHLER" in line:
                add("kritisch", "Lauf", line[:300])
    order = {"kritisch": 0, "warnung": 1, "info": 2}
    F.sort(key=lambda x: order[x["sev"]])
    out = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "counts": {k: sum(1 for f in F if f["sev"] == k) for k in order}, "findings": F}
    jdump(out, "a0_audit.json")
    return out


if __name__ == "__main__":
    o = run()
    print(o["counts"])
    for f in o["findings"]:
        print(f"[{f['sev']}] {f['area']}: {f['text']}")
