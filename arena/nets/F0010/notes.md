
## F0010 – entstanden 2026-11-24 aus Vorlage frei_5

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Duales Sektor-Momentum“
Kein Elternteil, noch kein Trade im Depot → Verbesserung aus Logik-Prüfung der Startidee und aktueller Lage (Korrektur, VIX 26,6, Kurve invertiert). strategy.py überarbeitet.
- Absolut-Momentum-Filter: long nur bei positiver, short nur bei negativer 3-Monats-Rendite – Reine Relativstärke kauft in Korrekturen auch fallende Sektoren bzw. shortet steigende; Dual Momentum reduziert Drawdowns
- Sonst Cash statt Zwangsposition – Score = Rendite/Drawdown belohnt Nicht-Handeln in schlechten Phasen
