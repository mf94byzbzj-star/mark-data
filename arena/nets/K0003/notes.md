
## K0003 – entstanden 2026-11-24 aus Vorlage klon

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Prämie nur bei Rückenwind“
Kein Elternteil (Kohorte 1, alle Klone identisch gestartet) → bewusst in verschiedene Richtungen gelenkt. Lage: Regime Korrektur, VIX 26,6, VIX-Kurve invertiert (1,04), SPY −7 % vom Hoch; nur 1 Optionskandidat (DAL lstrad, abgelehnt).
- Nur Short-Vola (Put-Credit-Spreads T05/T12), Long-Vola aus – Gegenhypothese zu K0004: Prämie verdienen, aber nur in ruhigen/Bullen-Regimen
- regime_block Korrektur/Bär/Crash – Bullische Short-Vola im Korrektur-Regime laut a5 nur halbe Größe; hier ganz gesperrt – wartet auf Regimewechsel
- Final-Gates aus, min_score 0 – Sonst praktisch kein Kandidat; Filter über Trigger-Status und Regime
- risk 0,04, exit tp50 erzwungen – Credit Spreads: kleine Gewinne, seltene große Verluste – Risiko je Trade begrenzen, Gewinn früh sichern
- trigger_status + „beobachten“ – T05/T12 stehen auf „beobachten“, sonst nie ein Trade
