
## F0008 – entstanden 2026-11-24 aus Vorlage frei_3

### 2026-11-24 – Supervisor-Denk-Lauf 1: „Angst-Käufer mit Kapitulations-Filter“
Kein Elternteil, noch kein Trade im Depot → Verbesserung aus Logik-Prüfung der Startidee und aktueller Lage (Korrektur, VIX 26,6, Kurve invertiert). strategy.py überarbeitet.
- Kapitulations-Filter: 5-Tage-Rendite ≤ −5 % – Aktuell aktiv (VIX 26,6, Kurve 1,04) – RSI<30 allein kauft auch langsame Abwärtstrends; Panik-Erholung braucht einen echten Ausverkauf
- max_open 6 Aktien, max_open_opt 3 – Vorher kein Positionslimit: bei 2 Käufen/Tag hätte das Netz in einer Stressphase das Konto voll ins Risiko gelegt
- Long-Vola nur bei OOS-t ≥ 1,5 – Nur belegte Long-Vola-Trigger (T03, T07) statt jeder Long-Vola-Struktur
