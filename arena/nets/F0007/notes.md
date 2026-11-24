
## F0007 – entstanden 2026-11-24 aus Vorlage frei_2

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Prämienverkäufer, regimebewusst“
Kein Elternteil, noch kein Trade im Depot → Verbesserung aus Logik-Prüfung der Startidee und aktueller Lage (Korrektur, VIX 26,6, Kurve invertiert). strategy.py überarbeitet.
- Pause auch im Bärenmarkt; Korrektur: halbe Größe, keine bullischen Strukturen – oos_stress aller Short-Vola-Trigger negativ; a5 rät in Korrektur zu halber Größe bei bullischer Short-Vola
- Mindest-t 1,0 und Trigger-Status nicht „verworfen“ – Bisher reichte oos_mean > 0 – T04/T08/T15 haben t < 0,7 und sind verworfen (Rauschen)
- Rangfolge zusätzlich × POP – Credit-Strukturen mit höherer Gewinnwahrscheinlichkeit bevorzugen
- risk 0,05→0,04 – Seltene große Verluste der Short-Vola begrenzen
