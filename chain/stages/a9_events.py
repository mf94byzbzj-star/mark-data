"""Agent 9 – Ereignisse & Nachrichten: der Torwächter vor jedem Trade.

Auch eine im Backtest gute Strategie kann an einem einzigen Makro-Termin scheitern (FOMC, CPI, Arbeitsmarkt,
Earnings der Index-Schwergewichte wie NVDA). Agent 9 läuft deshalb VOR Agent 3 und liefert:
  - Termin-Kalender der nächsten 45 Tage: Makro (chain_lab/research/events.yaml, von Agent 9 per Recherche gepflegt),
    Options-Verfall (OpEx/Hexensabbat, berechnet), Earnings der Mega-Caps (aus den Earnings-Daten, berechnet)
  - Nachrichtenlage (chain_lab/research/news.json, täglich von Agent 9 per WebSearch geschrieben):
    Gesamtrisiko normal/erhöht/hoch + einzelne Meldungen mit Wirkung auf Markt oder Ticker
  - Prüffunktion check(ticker, start, ende, vol): wird von Agent 3 (neue Trades) und Agent 7 (offene Positionen) genutzt
Regeln (fest):
  - Short-Vola: Termin „hoch“ (FOMC/CPI/NFP, Mega-Cap-Earnings mit Index-Wirkung) in den nächsten 2 Handelstagen → nicht
    einsteigen (erst nach dem Termin; IV fällt danach meist). Nachrichtenrisiko „hoch“ → keine neuen Short-Vola-Trades.
  - Ticker-Nachricht mit Wirkung „hoch“ → kein neuer Trade auf diesem Ticker an diesem Tag.
  - Termine innerhalb der Laufzeit (später als 2 Tage) → Hinweis, kein Ausschluss.
Ausgabe: chain_lab/out/a9_events.json
"""
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from chain.core import data
from chain.stages.common import RES, jdump, jload, yload

MEGA = ["NVDA", "AAPL", "MSFT", "AMZN", "META", "GOOGL", "AVGO", "TSLA"]
# Ticker, auf die Mega-Cap-Earnings und Makro-Termine besonders durchschlagen
INDEX_LIKE = {"SPY", "QQQ", "IWM", "DIA", "MDY", "XLK", "SMH", "XLY", "XLC", "ARKK"}
SEMIS = {"NVDA", "AMD", "AVGO", "MU", "QCOM", "INTC", "TXN", "AMAT", "LRCX", "KLAC", "MRVL", "SMH", "SMCI", "ANET", "DELL"}
HIGH_TYPES = {"FOMC", "CPI", "NFP"}


def _bdays_between(a, b):
    return int(np.busday_count(a, b))


def opex(start, end):
    out = []
    d = dt.date(start.year, start.month, 1)
    while d <= end:
        fr = [d + dt.timedelta(days=k) for k in range(31) if (d + dt.timedelta(days=k)).month == d.month
              and (d + dt.timedelta(days=k)).weekday() == 4]
        third = fr[2]
        if start <= third <= end:
            quad = third.month in (3, 6, 9, 12)
            out.append({"date": third.isoformat(), "time_et": "16:00", "type": "OpEx",
                        "name": "Großer Verfall (Hexensabbat)" if quad else "Monatlicher Options-Verfall",
                        "impact": "mittel", "source": "berechnet (3. Freitag)"})
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def mega_earnings(start, end):
    out = []
    for t in MEGA:
        for e in data.earnings(t):
            if start <= e.date() <= end:
                out.append({"date": e.date().isoformat(), "time_et": "", "type": "Earnings", "name": f"Earnings {t}",
                            "impact": "hoch" if t in ("NVDA", "AAPL", "MSFT") else "mittel", "ticker": t,
                            "source": "Earnings-Daten (yfinance)"})
    return out


def calendar(today=None, days=45):
    today = today or dt.date.today()
    end = today + dt.timedelta(days=days)
    ev = []
    for e in yload("events.yaml", []) or []:
        d = dt.date.fromisoformat(str(e["date"]))
        if today - dt.timedelta(days=3) <= d <= end:
            ev.append({**e, "date": d.isoformat()})
    ev += opex(today, end) + mega_earnings(today, end)
    for e in ev:
        e["bdays"] = _bdays_between(today, dt.date.fromisoformat(e["date"]))
    return sorted(ev, key=lambda e: (e["date"], e.get("time_et") or ""))


def news():
    p = os.path.join(RES, "news.json")
    return json.load(open(p)) if os.path.exists(p) else {"date": None, "risk": "unbekannt", "items": []}


def affects(e, ticker):
    """Wirkt ein Termin auf diesen Ticker? Makro-Termine auf alle, Mega-Cap-Earnings auf den Titel selbst,
    auf Index-ETFs und (bei NVDA/AVGO) auf Halbleiter."""
    if e["type"] in HIGH_TYPES or e["type"] == "OpEx":
        return True
    if e["type"] == "Earnings":
        t = e.get("ticker")
        return ticker == t or ticker in INDEX_LIKE or (t in ("NVDA", "AVGO") and ticker in SEMIS)
    return bool(e.get("tickers") and ticker in e["tickers"]) or e.get("scope") == "markt"


def check(ticker, start, end, vol, cal=None, nw=None):
    """Ergebnis: Liste von (Zeichen, Text, Gewicht). Gewicht < 0 senkt die Bewertung von Agent 3."""
    cal = cal if cal is not None else calendar()
    nw = nw if nw is not None else news()
    out = []
    s, e_ = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    for ev in cal:
        d = dt.date.fromisoformat(ev["date"])
        if not (s <= d <= e_) or not affects(ev, ticker):
            continue
        if ev["type"] == "OpEx" or (ev["type"] == "Earnings" and ev.get("ticker") == ticker):
            continue  # OpEx ist Struktur, eigene Earnings prüft Agent 3 separat
        near = _bdays_between(s, d) <= 2
        hi = ev.get("impact") == "hoch"
        if near and hi and vol == "short":
            out.append(("✗", f"{ev['name']} am {d.strftime('%d.%m.')} ({ev.get('time_et') or 'ganztägig'} ET) – Short-Vola erst nach dem Termin", -3))
        elif near and hi:
            out.append(("~", f"{ev['name']} am {d.strftime('%d.%m.')} – erhöhte Bewegung erwartet", 0))
        elif hi:
            out.append(("i", f"In der Laufzeit: {ev['name']} am {d.strftime('%d.%m.')}", 0))
    if nw.get("risk") == "hoch" and vol == "short":
        out.append(("✗", f"Nachrichtenlage „hoch“ ({nw.get('summary', '')[:120]}) – keine neuen Short-Vola-Trades", -3))
    elif nw.get("risk") == "erhöht" and vol == "short":
        out.append(("~", f"Nachrichtenlage erhöht: {nw.get('summary', '')[:120]}", -1))
    for it in nw.get("items", []):
        if it.get("impact") == "hoch" and ticker in (it.get("tickers") or []):
            out.append(("✗", f"Nachricht: {it.get('headline', '')[:140]}", -3))
        elif it.get("impact") == "mittel" and ticker in (it.get("tickers") or []):
            out.append(("~", f"Nachricht: {it.get('headline', '')[:140]}", -1))
    return out


def run():
    today = dt.date.today()
    cal = calendar(today)
    nw = news()
    stale = nw.get("date") != today.isoformat()
    nxt = [e for e in cal if e["bdays"] >= 0 and e.get("impact") == "hoch"][:6]
    out = {"date": today.isoformat(), "calendar": cal, "news": nw, "news_stale": stale,
           "next_high": nxt, "risk": nw.get("risk", "unbekannt") if not stale else "unbekannt",
           "blackout_short_vol": [e for e in cal if 0 <= e["bdays"] <= 2 and e.get("impact") == "hoch" and e["type"] in HIGH_TYPES]}
    jdump(out, "a9_events.json")
    return out


if __name__ == "__main__":
    o = run()
    print("Nachrichtenrisiko:", o["risk"], "(veraltet)" if o["news_stale"] else "")
    for e in o["calendar"][:15]:
        print(e["date"], e["type"], e["name"], e["impact"], f"in {e['bdays']} HT")
