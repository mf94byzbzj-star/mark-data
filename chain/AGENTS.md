# Agentenkette – Handbuch für den täglichen Lauf

Unabhängig vom Options-Quant-Agenten (`AGENT.md`, `oq/`, `lab/`). Hier gilt nur `chain/`, `chain_data/`, `chain_lab/`.
Sprache aller Texte: Deutsch. Quantitative Analyse, keine Anlageberatung.

## Ablauf (jede Stufe = ein Agent)

| Agent | Aufgabe | Code | Pflegt |
|---|---|---|---|
| 0 Supervisor | Überwacht alle Agenten und das Zusammenspiel: automatische Prüfung (`a0_audit.py`), Code-Review, Fehler sofort beheben; Modell-Optimierungen nur als Vorschlag mit Freigabe durch Markus | `chain/stages/a0_audit.py` | `chain_lab/research/proposals.yaml` + Freigabe-Datenbank im Dashboard |
| 1 Trigger | Recherchiert, **wann** ein Optionstrade einen Edge hat; backtestet Trigger | `chain/stages/a1_triggers.py` | `chain_lab/research/triggers.yaml` |
| 2 Ticker | Recherchiert, **auf welchen Tickern** die Trigger funktionieren; leitet Regeln per Backtest ab | `a2_tickers.py` | `chain_lab/research/ticker_rules.yaml` |
| 9 Ereignisse & Nachrichten | **Torwächter vor jedem Trade** (läuft nach 1/2, vor 3). Termin-Kalender (FOMC, CPI, Arbeitsmarkt, Feiertage in `events.yaml` per Recherche gepflegt; OpEx und Mega-Cap-Earnings NVDA/AAPL/MSFT/AMZN/META/GOOGL/AVGO/TSLA berechnet) + tägliche Nachrichtenlage (`news.json`). Regeln: Short-Vola nicht ≤ 2 Handelstage vor Termin „hoch“; Nachrichtenrisiko „hoch“ → keine neuen Short-Vola-Trades; Ticker-Meldung „hoch“ → kein Trade auf dem Ticker. Liefert `check()` an Agent 3 (Freigabe) und Agent 7 (Alarm für offene Positionen) | `a9_events.py` | `chain_lab/research/events.yaml`, `news.json` |
| 10 Posts & Trends | Google Trends: Suchsprung (ASVI ≥ In-Sample-90.-Perzentil) → long 5/20 T bzw. Umkehr short 60 T, Ereignisstudie gegen beliebigen Tag. Posts wichtiger Personen (Trump u. a.) aus `posts.json`: Backtest je Person ab 40 Posts mit Ticker-Bezug; bis dahin nur Hinweis. Nur bestätigte/Kandidat-Signale werden über Agent 5 zu Aktien-Ideen | `a10_social.py`, `chain/core/eventstudy.py` | `chain_lab/research/posts.json` |
| 11 SEC-Filings | Form-4-Insider (EDGAR): Insider-Kauf, Cluster-Kauf, opportunistischer Kauf, CEO/CFO-Kauf, Cluster-Verkauf; Ereignisstudie 21/63 T. Erzeugt keine Trades, sondern **bestätigt (+1) oder schwächt (−1)** Ideen von Agent 3 und 5 – nur mit Status „bestätigt“, Horizont-Abschlag bei kurzen Optionen, überstimmt nie Agent 9/Regime | `a11_filings.py` | – |
| 3 Live | Wendet Regeln 1+2 auf heutige Daten an, baut echte Trades aus der Cboe-Kette. **Strikes gamma-basiert:** Short-Put an/unter Put-Wall, Short-Call an/über Call-Wall (innerhalb ±0,10 Δ um das Ziel-Delta), Mittel-Strike am Gamma-Maximum bei positivem Gamma. Prüft Liquidität, GEX-Regime, Flip, Earnings, Erwartungswert | `a3_live.py` | – |
| 4 Preisaktion | Chartbild je Ticker (Trend, S/R, Squeeze); passt die Struktur? Alternative? | `a4_price.py` | – |
| 5 Makro | Regime (Crash? Korrektur?), Regime-Filter je Strategie, Aktie statt Option?, Aktien-Signale | `a5_macro.py` | `chain_lab/research/equity_triggers.yaml` |
| 8 Chartmuster | Recherchiert Chartmuster/Price-Action-Methoden (Trendlinien, Formationen wie Kopf-Schulter/Dreiecke/Flaggen/Doppeltop, Kerzenmuster, Gaps/Gap-Fill, Liquidity Sweeps, Angebots-/Nachfragezonen, Fair Value Gaps, Ausbrüche …), implementiert Detektoren ohne Lookahead, backtestet jedes Muster seit 2005 auf allen Tickern gegen denselben Einstieg OHNE Muster (Mehrwert-t, BH, 3× Kosten). Bestätigte/Kandidaten fließen in Agent 4 (Struktur-Urteil) und Agent 5 (Aktien-Ideen) ein; alles wird im Kerzenchart eingezeichnet | `chain/core/patterns.py`, `a8_patterns.py` | `chain_lab/research/chart_patterns.yaml` |
| 7 Positionen | Überwacht offene Positionen (von Markus im Dashboard mit „Eingegangen“ übernommen) abends und untertägig; Alarm „Handeln“/„Beobachten“ bei Gewinnziel, Stop, getestetem Short-Strike, nahem Verfall, gebrochener Wall, gedrehtem Gamma-Regime, Regimewechsel, neuen Earnings, erloschenem Signal, Aktien-Stop/Ziel. Führt den Papier-Track-Record aller Vorschläge (angezeigte Top-Ideen getrennt ausgewiesen, Aktien-Ideen mit Stop/Ziel/20 Tage) | `a7_positions.py` | `chain_lab/positions/open.json`, `chain_lab/track/paper.json` |
| 0 Lernmodul | Lernt automatisch aus den geschlossenen Papier-Trades: kalibriert die Gewichte der Bewertungsfaktoren von Agent 3 (inkl. je Trigger/Strategie) und die Größe je Aktien-Signalquelle. Übernahme nur nach bestandener Validierung auf den jüngsten 30 % der Trades; feste Sperren und Edge-Regeln sind nicht lernbar. Lernkurve im Reiter „Optimierungsvorschläge Modell“ | `a0_learn.py` | `chain_lab/research/weights.yaml` |
| 6 Dashboard | Fasst alles zusammen, veröffentlicht das Dashboard (Reiter: Trade-Ideen, Richtung & Aktien mit Kerzencharts + Gamma-Levels, Markt, Strategien & Backtests, Agenten-Log, Optimierungsvorschläge Modell) | `a6_dashboard.py`, `chain/dashboard/app.html` | `chain_lab/out/notes.json` |

## Täglicher Lauf (geplante Aufgabe)

1. Setup: `cd /home/claude/mark && git pull -q && pip install --break-system-packages -q -r chain/requirements.txt`
2. **Agent 1 – Forschung (max. ~15 min):** Mit WebSearch/WebFetch nach Einstiegskriterien für Optionsstrategien suchen
   (oquants.com, Volatility Vibes, ORATS-Blog, SSRN/JFE/JFQA-Papers, tastytrade-Studien, Moontower, Euan Sinclair …).
   - Pro Tag höchstens **2 neue Trigger** in `triggers.yaml` (mit `source`, `mechanism`, `added`), nur über vorhandene
     Merkmale (`chain/core/features.py`) – oder ein neues Merkmal/eine neue Strategie im Code ergänzen (mit Quelle).
   - Schwellen nur aus der Quelle oder als `grid` (max. 4 Werte). Nie `fut_*`-Spalten verwenden (Zukunft).
   - Abgelehnte Trigger (Status „verworfen“ über ≥ 20 Läufe) nicht löschen, sondern mit `status_note` markieren.
   - Dann `python -m chain.run a1`.
3. **Agent 2 – Forschung (max. ~10 min):** Literatur zur Ticker-Auswahl (Liquidität, IV-Niveau, Sektor, Earnings, Index
   vs. Einzelwert …). Höchstens 2 neue Regeln in `ticker_rules.yaml`. Dann `python -m chain.run a2`.
3b. **Agent 8 – Forschung (max. ~10 min):** Recherche zu Price-Action-/Chartmuster-Methoden (Bulkowski, Lo/Mamaysky/Wang,
   Osler, Caginalp, Marshall/Young/Rose, ICT/Smart-Money-Konzepte, Volume-Profile, Wyckoff …). Höchstens 1 neues Muster pro Tag:
   Detektor in `chain/core/patterns.py` (Signal erst am Abschlusstag, Stop = Invalidierung, Ziel = Kursziel) + Eintrag in
   `chart_patterns.yaml` mit Quelle. Ein Muster ohne Mehrwert über ≥ 20 Läufe mit `enabled: false` stilllegen ist eine
   Modelländerung → als Vorschlag. Dann `python -m chain.run a8`.
4. **Positionen synchronisieren:** `db_query` Collection `positions` (Artifact-Slug), alle Dokumente als JSON-Liste
   (Feld `id` = Dokument-ID, plus alle Felder) nach `chain_lab/positions/open.json` schreiben.
   Dann `python -m chain.run l0 a10 a11 a9 a3 a8 a4 a5 a7` – Live-Trades, Preisaktion, Makro, Positionen + Papier-Track-Record.
3a. **Agent 10 – Posts (max. ~10 min):** WebSearch nach marktbewegenden Posts wichtiger Personen der letzten 24 Std.
   (Trump auf Truth Social/X, Musk, Fed-Vorsitz, Finanzminister, CEOs der Mega-Caps). Jeden Post mit Bezug zu Aktien/Branchen/Zöllen
   an `chain_lab/research/posts.json` anhängen: `{"date", "person", "text" (sinngemäß, max. 200 Zeichen), "tickers": [...],
   "dir": 1|-1 (Richtung laut Post), "url"}`. Nur mit Quelle, nichts erfinden. Historie für Backtests: einmalig ältere Trump-Posts
   mit klarem Ticker-/Branchenbezug aus öffentlichen Archiven ergänzen (je Lauf max. 50).
3b2. **Fragen von Markus:** `db_query` Collection `questions`, Status „offen“: jede Frage mit den Daten der Kette beantworten, bei Bedarf
   eigenen kleinen Backtest rechnen (Python, Daten aus `chain_data/`), Antwort per `db_write` (update) in `answer_deep`, Status „beantwortet“.
3c. **Agent 9 – Termine & Nachrichten (max. ~10 min, VOR Schritt 4):**
   - `events.yaml`: künftige FOMC-, CPI-, Arbeitsmarkt-Termine und Börsenfeiertage mind. 60 Tage im Voraus führen (nur von
     federalreserve.gov, bls.gov, nyse.com; Feld `source`). Fehlt etwas → per WebSearch/WebFetch ergänzen.
   - WebSearch nach heutigen marktbewegenden Nachrichten (Fed/Notenbanker, Inflation, Geopolitik, Zölle, große Earnings,
     Ticker des Universums mit Kurssprung/Übernahme/Gewinnwarnung). `chain_lab/research/news.json` schreiben:
     `{"date": "<heute>", "risk": "normal|erhöht|hoch", "summary": "…", "items": [{"headline", "impact": "hoch|mittel|niedrig",
     "scope": "markt|ticker", "tickers": [...], "url"}]}`. „hoch“ nur bei klarer Marktgefahr (z. B. Notenbank-Überraschung,
     Kriegsereignis, Crash-Tag). Nichts erfinden, jede Meldung mit Quelle.
   - Dann `python -m chain.run a9`.
6. **Notizen schreiben** – `chain_lab/out/notes.json`:
   ```json
   {"headline": "Befund des Tages in einem Satz (wird Dashboard-Titel)",
    "a1": "Was wurde recherchiert/neu getestet, was ist bestätigt/verworfen und warum",
    "a2": "…", "a3": "Welche Trades und warum (konkret: Ticker, Struktur, Strikes)", "a4": "…",
    "a5": "Regime, Aktie-statt-Option-Befunde", "a9": "Termine/Nachrichten und was sie blockiert haben", "a6": "Kurzfazit für Markus",
    "log": "fortlaufendes Forschungsprotokoll (letzte ~10 Einträge, neueste oben)"}
   ```
   Jede Aussage muss aus den JSON-Ergebnissen in `chain_lab/out/` stammen – nichts erfinden.
6b. **Supervisor** (siehe unten): Freigaben umsetzen, prüfen, Fehler beheben, Vorschläge anlegen.
7. `python -m chain.run a0 a6` und Dashboard veröffentlichen: Artifact-Slug steht in `chain_lab/dashboard/ARTIFACT`.
   `publish_files` mit `slug`, `base_version` (vorher `get_artifact`), `mode: "patch"`, Dateien:
   `index.html` ← `chain_lab/dashboard/index.html`, `data/summary.json` ← `chain_lab/dashboard/data/summary.json`.
8. Commit + Push: `git add chain chain_lab && git commit -m "Kette: <Datum> <Befund>" && git push`
   (`chain_data/` nie selbst ändern – das macht GitHub Actions).

## Supervisor (Agent 0) – jeder Lauf
1. **Freigaben abarbeiten:** `db_query` (Artifact-Slug aus `chain_lab/dashboard/ARTIFACT`, Collection `proposals`).
   - Für jeden Vorschlag mit `status == "freigegeben"` (Kommentar von Markus beachten): umsetzen, testen, Kette neu rechnen.
   - Danach `db_write` (update, mit `ifVersion`): `status: "umgesetzt"`, `result` (was geändert, Vorher/Nachher-Kennzahlen, Commit).
   - Gleichen Stand in `chain_lab/research/proposals.yaml` spiegeln. **Nie** etwas mit Status offen/abgelehnt/zurückgestellt umsetzen.
2. **Prüfen:** `python -m chain.run a0` und Befunde in `chain_lab/out/a0_audit.json` lesen; dazu kurz die Ergebnisse aller Agenten auf
   Plausibilität prüfen (Widersprüche, Ausreißer, Daten­lücken, Risiko-Grenzen, Logik zwischen den Stufen).
3. **Fehler sofort beheben** (ohne Freigabe): Abstürze, falsche Rechnungen, Datenfehler, Lookahead, verletzte Risiko-Grenzen,
   Widersprüche zwischen Stufen, Darstellungsfehler. Im Agenten-Log (`notes.json → log`) dokumentieren.
4. **Optimierungen nur vorschlagen** (keine eigenmächtige Änderung): alles, was das Modell verändert – Schwellen, Regeln,
   Gewichtungen, neue/entfernte Strategien, Kosten- oder Risikomodell, Universum, Statuslogik.
   - Erst wenn die Historie vollständig ist (`clone_complete` in `chain_data/surface/_state.json`), außer bei offensichtlichen Mängeln.
   - Max. 3 neue Vorschläge pro Tag, nur mit Beleg aus den Ergebnisdateien. Felder: id (P###), created, agent, title, problem,
     change, expected, evidence, risk, status "offen".
   - Anlegen per `db_write` (set) unter `proposals/<id>` **und** in `proposals.yaml`. Keine Duplikate offener Vorschläge.

## Regeln für Edge (fest, nicht aufweichen)
- Walk-forward: Schwellen nur In-Sample (erste 60 % der Zeit), Bewertung Out-of-Sample.
- Bestätigt = OOS Ø > 0 und ≥ Strategie ohne Trigger, geclusterter t ≥ 2, beide OOS-Hälften positiv, positiv mit 3× Kosten, BH-signifikant (10 %).
- Kandidat = OOS Ø > 0, t ≥ 1,5 und Vorsprung gegenüber der Strategie ohne Trigger > 0 (halbe Positionsgröße; P001, umgesetzt 24.11.2026). OOS Ø > 0 und t ≥ 1 sonst „beobachten“ (Anzeige, kein Trade). Alles andere wird nicht gehandelt.
- Nur Strategien mit begrenztem Risiko. Optionen: 2 % Kontorisiko je Trade, Summe aller Vorschläge max. 10 %. Aktien: 3 % Risiko bis Stop, max. 20 % Position, Summe max. 100 % (ungehebelt), keine widersprüchlichen Richtungen, Long im Crash/Bärenmarkt und Short im ruhigen Bullenmarkt gesperrt.

## Daten (`chain_data/`, von GitHub Actions)
- `surface/<T>.csv.gz` – Vola-Oberfläche je Tag/Verfall (DoltHub ab 2019, Verfälle bis ~60 T, anfangs wöchentlich)
- `live/<Tag>/<T>.csv.gz` – komplette Cboe-Kette mit OI/Greeks (täglich nach US-Schluss), `_summary.csv`
- `prices/`, `earnings/`, `macro/fred.csv`, `volhist/` (IV/HV von ~1.500 Symbolen)
- `intraday/latest/` – untertägiger Ketten-Schnappschuss (~1,5 Std. vor US-Schluss) für den Positionswächter
- `alt/insider/<T>.csv.gz` (SEC Form 4 ab 2012), `alt/trends/<T>.csv` (Google Trends, 5 J. wöchentlich) – Workflow `chain-alt.yml` (22:50 UTC)
- Workflows: `.github/workflows/chain-daily.yml` (21:40 UTC), `chain-dolt.yml` (02:15 UTC), `chain-intraday.yml` (19:25 UTC)

## Push-Meldungen
- Abendlauf: SendUserMessage NUR, wenn mindestens ein Top-Trade „freigegeben“ ist (handelbar, ≥ 1 Kontrakt) ODER eine offene
  Position den Alarm „Handeln“ hat. Sonst keine Nachricht.
- Untertägiger Wächter: SendUserMessage NUR bei Positionen mit „Handeln“, die beim letzten Lauf noch nicht „Handeln“ waren
  (keine Wiederholungen).
