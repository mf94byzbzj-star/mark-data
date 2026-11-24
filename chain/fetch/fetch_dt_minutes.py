"""Daten-Agent: 1-Minuten-Kerzen inkl. Vor-/Nachbörse (Yahoo, max. 30 Tage zurück) für das Daytrading-Netz.
Ablage: chain_data/minute1/<SYM>.csv.gz (Zeit = New York, ohne Zeitzone). Ergänzt bestehende Dateien (rollierend)."""
import datetime as dt
import os
import sys

import pandas as pd
import yfinance as yf

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "chain_data", "minute1")
os.makedirs(OUT, exist_ok=True)
tick = [t.strip() for t in open(os.path.join(ROOT, "chain_data", "dt_minute_tickers.txt")) if t.strip()]
days = int(sys.argv[1]) if len(sys.argv) > 1 else 29
end = dt.date.today() + dt.timedelta(days=1)
start = end - dt.timedelta(days=days)
chunks = []
a = start
while a < end:
    b = min(a + dt.timedelta(days=7), end)
    chunks.append((a, b))
    a = b
got = {t: [] for t in tick}
for i in range(0, len(tick), 40):
    grp = tick[i:i + 40]
    for a, b in chunks:
        try:
            df = yf.download(grp, start=str(a), end=str(b), interval="1m", prepost=True, group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            print("Fehler", grp[:3], a, e)
            continue
        if df is None or df.empty:
            continue
        for t in grp:
            try:
                x = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            x = x.dropna(subset=["Close"])
            if x.empty:
                continue
            idx = x.index.tz_convert("America/New_York").tz_localize(None) if x.index.tz is not None else x.index
            got[t].append(pd.DataFrame({"open": x["Open"].values, "high": x["High"].values, "low": x["Low"].values,
                                        "close": x["Close"].values, "volume": x["Volume"].values}, index=idx))
n = 0
for t, parts in got.items():
    if not parts:
        continue
    new = pd.concat(parts)
    p = os.path.join(OUT, f"{t}.csv.gz")
    if os.path.exists(p):
        old = pd.read_csv(p, index_col=0, parse_dates=True)
        new = pd.concat([old, new])
    new = new[~new.index.duplicated(keep="last")].sort_index()
    new = new[new.index >= pd.Timestamp(dt.date.today() - dt.timedelta(days=60))]   # rollierend 60 Tage
    new.index.name = "time"
    new.to_csv(p, float_format="%.4f", compression="gzip")
    n += 1
print(f"{n} Ticker gespeichert, Zeitraum {start} – {end}")
