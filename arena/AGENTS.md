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
Orders:
- `{"type":"option","cid":…, "risk":0.05}` – Haltedauer = Ausstiegsregel der Struktur (tp50/tp25/daysN/front/pre/post/Verfall)
- `{"type":"equity","ticker":…, "direction":"long|short", "stop":…, "target":…|null, "risk":0.03, "max_days":20,
   "trail":{"atr":3}|{"pct":0.08}|null, "let_run":true|false, "exit":null|"open"|"close"}`
  – `max_days` = geplante Haltedauer in Handelstagen (Pflicht, immer bewusst setzen); `exit:"open"` = über Nacht bis zur
  Eröffnung, `exit:"close"` = intraday bis Schluss; `let_run` = Gewinner dürfen über max_days hinaus laufen (auch > 30 Tage),
  dann trägt der nachgezogene Stop; `trail` = Stop täglich nachziehen (nur in Gewinnrichtung).
- `{"type":"modify","id":…, "stop":…, "target":…, "max_days":…, "trail":…, "let_run":…, "exit":"tp50|hold|…"(nur Optionen)}`
  – Stop darf nur nachgezogen werden, nie Risiko erhöhen.
- `{"type":"add","id":…, "risk":0.03}` – Pyramiding (im Gewinn) oder DCA (im Verlust, solange der Stop nicht erreicht ist).
  Grenze: Verlust der GESAMTEN Position am Stop ≤ 10 % des Kontos.
- `{"type":"close","id":…}`. `risk` = Anteil des Kontos bis Stop/Max-Verlust (≤ 0,10).
- Optional `decide_intraday(ctx)` (ca. 15:30 New York): `ctx` = day, time, bars (5-Minuten-Kerzen des Tages für SPY, QQQ, IWM,
  DIA, Sektor-ETFs, TLT, GLD), book, genome; nur Aktien/ETF-Orders, Ausführung zum letzten 5-Minuten-Kurs.
Einmalige Orders des Supervisors: `arena/nets/<ID>/orders.json` (gleiche Form) – werden am nächsten Handelstag ausgeführt.

## Haltedauer und Positionsführung (Markus, 24.11.2026)
- Jede Position hat eine klar festgelegte Haltedauer (max_days bzw. Ausstiegsregel); sie steht im Dashboard bei jeder Position.
- Erlaubt und erwünscht: Gewinner länger laufen lassen (auch über 30 Tage) mit nachgezogenem Stop; Pyramiding in Gewinnern;
  DCA in Verlustpositionen, solange der Stop nicht erreicht ist – immer innerhalb der 10-%-Grenze für die Gesamtposition.
- Kurz-Swing (1–5 Tage), Overnight (exit "open") und Intraday-Fenster (decide_intraday, exit "close") sind erlaubt.
- Nach der Auswahl laufen offene Trades der Gewinner-Netze nach ihren Regeln aus (Nachlauf, ohne neue Trades).

## Ohne Claude kein Handel (Markus, 24.11.2026)
- Jeder Denk-Lauf schreibt am Ende `arena/heartbeat.json` = `{"last_think": "<YYYY-MM-DD>"}`.
- Ist der letzte Denk-Lauf älter als 9 Tage (z. B. Claude-Kontingent aufgebraucht), pausiert die Arena: kein neuer Handel, keine
  Auswahl, die 21-Handelstage-Uhr der Kohorten steht. Bestehende Stops/Ausstiege laufen weiter (Schutz). Sobald wieder gedacht
  wird, geht es weiter.
- Eine Kohorte pausiert, solange eines ihrer Netze `mutation_pending` hat (neue Netze handeln erst nach ihrem Supervisor).
- Nach dem Denk-Lauf: `python -m arena.engine <letzter Handelstag>` ausführen, damit die freigegebenen Netze noch am selben Tag
  handeln (der Code lässt einen Nachlauf nur zu, wenn an dem Tag pausiert war).

## Zwei Supervisor-Rollen
- **Technik-Supervisor** (Code `arena/health.py` bei jedem Tageslauf + Schritt 0 jedes Denk-Laufs): Daten aktuell? Signale zum
  Datentag passend? Workflows grün? Netze ohne Absturz, heute bewertet, Kasse nicht negativ? Herzschlag? Fehler SOFORT beheben
  (Abrufcode, Engine, Netz-Code), dokumentieren in `arena/tech_log.md`. Keine inhaltlichen Änderungen an Strategien.
- **Super-Supervisor + Netz-Supervisoren** (inhaltlich): Auswahl, Mutation, Analyse, Anweisungen – siehe unten.

## Denk-Lauf (täglich kurz, sonntags ausführlich – Reihenfolge)
0. **Technik-Supervisor:** `python -m arena.health` und `arena/out/health.json` lesen; jeden Befund „kritisch“/„warnung“ prüfen und
   beheben (z. B. Workflow neu starten per Marker `chain_data/.run_daily`/`.run_alt`/`.run_backfill`, Code reparieren, Signale
   mit `python -m chain.run a1 a2 a10 a11 a9 a3 a8 a4 a5` neu rechnen). Kurz in `arena/tech_log.md` festhalten.
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
6. `arena/heartbeat.json` schreiben, Nachlauf `python -m arena.engine <letzter Handelstag>`, dann Commit „Arena: Denk-Lauf <Datum>“ und push. Nie Kontrollen, config.yaml oder fremde Depots (book.json) verändern.
