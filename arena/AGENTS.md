# Evolutions-Arena – Handbuch für den wöchentlichen Denk-Lauf (Claude)

Ziel: Aus Survival of the fittest entsteht ohne Zutun von Markus ein immer besseres Agentennetz.
Alles hier ist simuliert (Papierdepots je 100.000 $). Quantitative Analyse, keine Anlageberatung.

## Aufbau
- **Daten-Agent** (`chain_data/`, Workflows `chain-*.yml`): lädt jede Quelle einmal pro Tag für alle Netze.
  Netze rufen NIE selbst Daten ab. Datenwünsche → `chain_data/requests.yaml` (der Daten-Agent prüft: kostenlos, erlaubt,
  zuverlässig; nimmt an oder lehnt mit Begründung ab und baut den Abruf in `chain/fetch/`).
- **Gemeinsame Signale** (täglich, `arena-daily.yml`): Kettencode rechnet einmal Kandidaten, Preisaktion, Makro, Muster,
  Termine, SEC, Trends → `chain_lab/out/`. Daraus bedient sich jedes Netz.
- **Netze** (`arena/nets/<ID>/`): `genome.yaml` (Regeln), `strategy.py` (nur freie Netze), `book.json` (Depot), `notes.md`
  (Tagebuch des Supervisors). Typen: `klon` (Regeln der Original-Kette, Genom frei änderbar), `frei` (eigene Logik),
  Kontrollen `markus` (eingefroren), `zufall`, `spy` – Kontrollen nie verändern.
- **Super-Supervisor** (`arena/supersupervisor.py` + dieser Lauf): Kohorten, Auswahl, Klonen, Rücksprung.
- Tageslauf (`arena/engine.py`) ist reiner Code; dieser Wochenlauf ist das „Denken“.

## Feste Spielregeln (nur Markus ändert sie, `arena/config.yaml`)
- Nur Positionen mit begrenztem Risiko (Optionen nur aus der Kandidatenliste mit echten Kettenpreisen; Aktien mit Stop).
- Max. 10 % Kontorisiko je Trade; Kaufkraft darf nicht negativ werden; Aktien ungehebelt.
- Gleiche Fills und Kosten für alle; kein Blick in die Zukunft (nur Daten bis heute); keine Netzwerkzugriffe in strategy.py.
- Score = Rendite / max(Max-Drawdown, 2 %) nach 30 Tagen; mind. 2 Trades, sonst kein Sieger.

## Schnittstelle freier Netze: `decide(ctx) -> list[order]`
`ctx`: `day`, `regime`, `market` (vix, vix_ts, spy_dd52 …), `candidates` (Optionsstrukturen: cid, ticker, strategy, vol, bias,
payoff{debit_fill,max_loss,max_gain,pop}, legs, oos_mean/oos_win/oos_t, trigger_status, score, factors, final, gex),
`equity_ideas`, `equity_signals`, `price` (Preisaktion der Kandidaten), `patterns`, `events`, `filings` (SEC je Ticker),
`trends`, `closes` (DataFrame Schlusskurse ~300 Tage, alle Ticker), `book` (cash, open), `genome`.
Orders: `{"type":"option","cid":…, "risk":0.05}` · `{"type":"equity","ticker":…, "direction":"long|short", "stop":…,
"target":…|null, "risk":0.03, "max_days":20}` · `{"type":"close","id":…}`. `risk` = Anteil des Kontos bis Stop/Max-Verlust (≤ 0,10).
Einmalige Orders des Supervisors: `arena/nets/<ID>/orders.json` (gleiche Form) – werden am nächsten Handelstag ausgeführt.

## Wöchentlicher Denk-Lauf (Reihenfolge)
1. `git pull`; `arena/state.json`, `arena/out/summary.json` lesen.
2. **Neue Netze mit `mutation_pending: true`** (Status „wartet“ oder frisch „lebt“): je Netz als dessen Supervisor denken:
   - Eltern-Netz auswerten (book.json der Eltern, notes.md: was lief gut/schlecht, welche Trades, welches Regime).
   - Gezielte, begründete Änderungen (keine Zufallsänderungen): Klon-Netz → Genom (Gewichte, Schwellen, Strategien/Trigger
     an/aus, Risiko je Trade ≤ 0,10, Ausstieg, Regime-Sperren, Aktienteil). Freies Netz → `strategy.py`/Genom umbauen oder
     neue Logik erfinden (nur aus `ctx`). Geschwister (gleicher Elternteil) bewusst in VERSCHIEDENE Richtungen entwickeln.
   - Änderung in `genome.yaml → changes` (Datum, was, warum) und `notes.md` festhalten; `mutation_pending` auf false.
   - Code testen: `python -c "from arena.engine import *; …"` oder Tageslauf trocken (nicht committen, wenn er abstürzt).
3. **Lebende Netze** kurz prüfen (max. 2 Min. je Netz): grobe Fehler (Absturz `last_error`, keine Trades über 10 Tage, Regelbruch)
   beheben; optional einmalige Orders/Schließungen über `orders.json` mit Begründung in notes.md.
4. **Super-Supervisor:** Rundenverlauf prüfen (`rounds`, `alert`). Bei `alert.level == "warnung"`: Ursachen analysieren
   (Regime? zu viel Risiko? zu ähnliche Netze? Überanpassung?) und Anweisungen in `arena/instructions.md` schreiben, die alle
   Supervisoren im nächsten Lauf befolgen. Rücksprung führt der Code automatisch aus; danach Neustart kommentieren.
5. Datenwünsche der Netze (`chain_data/requests.yaml`) als Daten-Agent bearbeiten.
6. Commit „Arena: Wochenlauf <Datum>“ und push. Nie Kontrollen, config.yaml oder fremde Depots (book.json) verändern.
