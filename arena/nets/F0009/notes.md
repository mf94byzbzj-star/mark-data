
## F0009 – entstanden 2026-11-24 aus Vorlage frei_4

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Insider – frische Käufe“
Kein Elternteil, noch kein Trade im Depot → Verbesserung aus Logik-Prüfung der Startidee und aktueller Lage (Korrektur, VIX 26,6, Kurve invertiert). strategy.py überarbeitet.
- Nur Insider-Signale der letzten 14 Tage – Meldungen vom 04.11. (INTC) sind schon eingepreist; Insider-Effekt wirkt v. a. in den ersten Wochen
- Kein Kauf, wenn Kurs > 8 % unter SMA50 – Nicht ins fallende Messer greifen – Korrektur-Regime
- max_open 6 – Positionslimit fehlte (Risiko 4–6 % je Trade)
