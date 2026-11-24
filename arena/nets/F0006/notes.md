
## F0006 – entstanden 2026-11-24 aus Vorlage frei_1

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Trendfolger mit Marktfilter“
Kein Elternteil, noch kein Trade im Depot → Verbesserung aus Logik-Prüfung der Startidee und aktueller Lage (Korrektur, VIX 26,6, Kurve invertiert). strategy.py überarbeitet.
- Marktfilter: keine Neukäufe, wenn SPY unter SMA200 (min_spy_dist200 0) – Trendfolge hat im Korrektur-Regime (SPY −1,1 % unter SMA200, Breite 43 %) schlechte Trefferquote; Fehlausbrüche vermeiden
- Relative Stärke zu SPY verlangt (min_rel_mom 0) – Nur echte Leader kaufen, nicht Werte, die bloß mit dem Markt steigen
- Trendbruch-Ausstieg: Close-Order bei Schluss < SMA50 – Bisher nur fester Stop/Ziel/Zeit – Trendfolger sollen Verlierer früh schneiden und Gewinner laufen lassen
- risk 0,05→0,04, Ziel 2R→3R, max_days 30→40 – Kleineres Einzelrisiko bei 5 Positionen (max 20 % Risiko); asymmetrische Auszahlung passt zur Trendlogik
