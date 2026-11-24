# mark-data – Daten-Agent und Evolutions-Arena der Agentenkette

Öffentliches Repo, damit GitHub Actions ohne Minutenlimit laufen.

- `chain_data/` – alle Marktdaten, **einmal** pro Tag geladen und von allen Netzen gemeinsam genutzt (Daten-Agent):
  Cboe-Optionsketten, Kurse, FRED, Earnings, DoltHub-Volaoberflächen, SEC-Form-4, Google Trends.
- `chain_data/requests.yaml` – Einkaufsliste: Datenwünsche der Netze; der Daten-Agent prüft (kostenlos, erlaubt) und lädt jede Quelle einmal.
- `arena/` – Evolutions-Arena: Netze, Depots, Runden, Super-Supervisor.
- `chain/` – Code der Agentenkette (Basis für alle Netze).

Quantitative Analyse, keine Anlageberatung. Alle Trades hier sind simuliert.
