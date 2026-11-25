"""Technik-Supervisor der Arena: prüft bei jedem Tageslauf die technische Umsetzung (nicht die Inhalte).

Prüft: Aktualität der Daten (Cboe-Kette, Kurse, Vola-Historie, SEC, Trends), ob die gemeinsamen Signale zum Datentag passen,
Status der Daten-Workflows, Abstürze/Fehler je Netz, ob jedes lebende Netz heute bewertet wurde, Herzschlag des Denk-Laufs.
Ausgabe: arena/out/health.json (Ampel je Prüfung). Der Denk-Lauf (Claude) liest sie zuerst und behebt Fehler sofort.
"""
import datetime as dt
import json
import os

import numpy as np
import pandas as pd
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
A = os.path.join(ROOT, "arena")
D = os.path.join(ROOT, "chain_data")
OUT = os.path.join(ROOT, "chain_lab", "out")


def _j(p, d=None):
    return json.load(open(p)) if os.path.exists(p) else d


def bdays(a, b):
    return int(np.busday_count(a, b))


def run(st=None, today=None):
    today = today or dt.date.today()
    F = []

    def add(level, area, text, fix=""):
        F.append({"level": level, "area": area, "text": text, "fix": fix})

    live = sorted(d for d in os.listdir(os.path.join(D, "live")) if d[:2] == "20") if os.path.isdir(os.path.join(D, "live")) else []
    last = live[-1] if live else None
    if not last:
        add("kritisch", "Daten", "Keine Cboe-Kette vorhanden", "Workflow chain-daily prüfen")
    else:
        lag = bdays(last, str(today))
        if lag > 2:
            add("kritisch", "Daten", f"Cboe-Kette veraltet: {last} ({lag} Handelstage)", "chain-daily starten / Abruf reparieren")
        n = len([f for f in os.listdir(os.path.join(D, "live", last)) if f.endswith(".csv.gz")])
        if n < 120:
            add("warnung", "Daten", f"Nur {n} Ticker in der Kette vom {last}", "Cboe-Rate-Limit prüfen")
    try:
        px = pd.read_csv(os.path.join(D, "prices", "SPY.csv"))
        pl = str(pd.to_datetime(px.iloc[:, 0]).max().date())
        if last and pl < last:
            add("warnung", "Daten", f"Kurse enden {pl}, Kette {last}", "Kursabruf prüfen")
    except Exception as e:
        add("warnung", "Daten", f"Kurse SPY nicht lesbar ({type(e).__name__})")
    try:
        sf = pd.read_csv(os.path.join(D, "surface", "SPY.csv.gz"), usecols=["date"])
        sl = str(sf["date"].max())[:10]
        if bdays(sl, str(today)) > 4:
            add("warnung", "Daten", f"Vola-Historie (DoltHub) endet {sl}", "chain-dolt prüfen (1–2 Tage Verzug sind normal)")
    except Exception:
        add("warnung", "Daten", "Vola-Historie SPY fehlt")
    alt = _j(os.path.join(D, "alt", "_state.json"), {}) or {}
    if not os.path.isdir(os.path.join(D, "alt", "trends")) or len(os.listdir(os.path.join(D, "alt", "trends"))) < 20:
        add("warnung", "Daten", "Google Trends: kaum Daten", "chain-alt (trends) prüfen – Google sperrt zeitweise")
    if len(alt.get("insider_quarters", [])) < 20:
        add("info", "Daten", f"SEC-Historie: {len(alt.get('insider_quarters', []))} Quartale geladen (Ziel ab 2012)")
    # gemeinsame Signale passend zum Datentag?
    for f in ("a3_live.json", "a4_price.json", "a5_macro.json"):
        d = (_j(os.path.join(OUT, f), {}) or {}).get("day")
        if last and d != last:
            add("kritisch", "Signale", f"{f} vom {d}, Daten vom {last}", "Signale neu rechnen (arena-daily)")
    # Workflows (öffentliche API)
    try:
        r = requests.get("https://api.github.com/repos/mf94byzbzj-star/mark-data/actions/runs?per_page=30", timeout=20).json()
        seen = {}
        for w in r.get("workflow_runs", []):
            if w["name"] not in seen and w["status"] == "completed":
                seen[w["name"]] = w
        for name, w in seen.items():
            if w["conclusion"] not in ("success", "cancelled", "skipped"):
                add("warnung", "Workflows", f"{name}: letzter Lauf {w['conclusion']} ({w['created_at'][:16]})", "Log prüfen und reparieren")
    except Exception:
        add("info", "Workflows", "Status der Workflows nicht abrufbar")
    # Netze
    st = st or _j(os.path.join(A, "state.json"), {}) or {}
    for n in (st.get("nets") or {}).values():
        if n.get("status") != "lebt":
            continue
        if n.get("last_error"):
            add("kritisch", "Netz", f"{n['id']}: {n['last_error'][:160]}", "Code des Netzes reparieren (Denk-Lauf)")
        b = _j(os.path.join(A, "nets", n["id"], "book.json"), {}) or {}
        cv = b.get("curve", [])
        if not cv:
            add("warnung", "Netz", f"{n['id']}: noch keine Bewertung")
        elif st.get("last_day") and cv[-1]["day"] != st["last_day"]:
            add("warnung", "Netz", f"{n['id']}: zuletzt bewertet {cv[-1]['day']}, Arena {st['last_day']}")
        if b and cv:   # Margin erlaubt: geprüft wird die Kaufkraft (Brutto-Aktien ≤ 2 × Kontowert) statt einer negativen Kasse
            eqv = cv[-1]["equity"]
            gross = sum(abs(p["shares"] * p.get("mark", p["entry"])) for p in b.get("open", []) if p["kind"] == "equity")
            if eqv <= 0:
                add("kritisch", "Netz", f"{n['id']}: Kontowert ≤ 0")
            elif gross > 2.05 * eqv:
                add("kritisch", "Netz", f"{n['id']}: Hebel {gross / eqv:.2f}× über Reg-T-Grenze 2×")
    hb = (_j(os.path.join(A, "heartbeat.json"), {}) or {}).get("last_think")
    if not hb or (today - dt.date.fromisoformat(hb)).days > 2:
        add("warnung", "Denk-Lauf", f"Letzter Claude-Denk-Lauf: {hb or 'nie'}", "geplante Aufgabe „Arena – Denk-Lauf“ prüfen")
    lv = "kritisch" if any(f["level"] == "kritisch" for f in F) else ("warnung" if any(f["level"] == "warnung" for f in F) else "ok")
    out = {"date": str(today), "data_day": last, "level": lv, "findings": F}
    os.makedirs(os.path.join(A, "out"), exist_ok=True)
    json.dump(out, open(os.path.join(A, "out", "health.json"), "w"), ensure_ascii=False, indent=1)
    return out


if __name__ == "__main__":
    o = run()
    print(o["level"])
    for f in o["findings"]:
        print(f["level"], f["area"], f["text"])
