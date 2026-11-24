# Anweisungen an alle Supervisoren

## 2026-11-24 – von Markus
1. **Haltedauer immer klar festlegen:** Jede Order bekommt eine bewusst gewählte Haltedauer (`max_days`, bei Optionen die
   Ausstiegsregel). Kurz-Swing (1–5 Tage), Overnight (`exit: "open"`) und Intraday-Fenster (`decide_intraday`, `exit: "close"`)
   sind jetzt möglich – nutzt sie, wo eure Logik es begründet.
2. **Gewinner laufen lassen:** Profitable Trades dürfen über die geplante Haltedauer hinaus laufen, auch länger als 30 Tage
   (`let_run: true`). Dann wird der Stop konsequent nachgezogen (`trail` oder `modify` mit höherem Stop).
3. **Stop nachziehen:** Stops werden nur in Gewinnrichtung nachgezogen, nie zurückgesetzt. Ordnet das aktiv an.
4. **Pyramiding und DCA sind erlaubt:** Nachkaufen in Gewinner (`add`) und Verbilligen von Verlustpositionen, deren Stop noch
   nicht erreicht ist (`add`). Grenze: Verlust der Gesamtposition am Stop höchstens 10 % des Kontos.
5. Klon-Netze steuern das über ihr Genom (`equity_trail`, `equity_let_run`, `equity_days`) und einmalige Orders in `orders.json`.

## 2026-11-24 (2) – von Markus: Margin
6. **Positionen dürfen größer als das Konto sein (Margin):** bis 2 × Kontowert über Nacht, 4 × intraday. Zinsen (Fed Funds + 1,5 %)
   und Leihgebühren werden realistisch abgezogen – Hebel lohnt nur, wenn der erwartete Ertrag die Kosten klar übersteigt.
   Die 10-%-Stop-Regel je Trade und der Max-Drawdown-Stopp (−30 %) gelten unverändert; ein Margin Call schließt Positionen
   zwangsweise. Hebel bewusst und begründet einsetzen (in notes.md festhalten).
