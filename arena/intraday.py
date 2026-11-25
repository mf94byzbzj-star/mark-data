"""Intraday-Fenster der Arena (ca. 15:30 New York): lädt EINMAL 5-Minuten-Kurse für alle Netze (Daten-Agent-Prinzip) und
lässt Netze mit einer Funktion decide_intraday(ctx) handeln. Ausführung zum letzten 5-Minuten-Kurs (+ halbe Spanne).
Positionen mit exit="close" schließt der Tageslauf zum Schlusskurs desselben Tages, exit="open" zur nächsten Eröffnung.
Gleiche Spielregeln wie im Tageslauf (10 % Risiko je Trade, Pause ohne Claude-Denk-Lauf)."""
import datetime as dt
import importlib.util
import json
import os
import sys

import pandas as pd
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from arena import engine as E  # noqa: E402
from arena import supersupervisor as ss  # noqa: E402

TICK = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "SMH", "TLT", "GLD"]
D5 = os.path.join(ROOT, "chain_data", "intraday5m")


def fetch():
    import yfinance as yf
    os.makedirs(D5, exist_ok=True)
    first = not os.listdir(D5)
    df = yf.download(TICK, period="60d" if first else "1d", interval="5m", group_by="ticker", auto_adjust=False,
                     progress=False, threads=True, prepost=False)
    out = {}
    for t in TICK:
        try:
            x = df[t].dropna(how="all")
        except Exception:
            continue
        x.index = x.index.tz_convert("America/New_York")
        out[t] = x
        for day, g in x.groupby(x.index.date):
            p = os.path.join(D5, str(day))
            os.makedirs(p, exist_ok=True)
            g.to_csv(os.path.join(p, f"{t}.csv.gz"), compression="gzip")
    return out


def run():
    now = pd.Timestamp.now(tz="America/New_York")
    today = now.date()
    if not (now.hour == 15 and 10 <= now.minute <= 58) or now.weekday() >= 5:
        print(f"außerhalb des Intraday-Fensters ({now:%H:%M} New York) – kein Handel (GitHub-Zeitplan verspätet?)")
        return
    bars = fetch()
    if not bars or "SPY" not in bars or bars["SPY"].index[-1].date() != today:
        print("keine heutigen 5-Minuten-Kurse (Feiertag?)")
        return
    st = ss.load_state()
    hb = (E.jl(os.path.join(E.A, "heartbeat.json"), {}) or {}).get("last_think")
    if not hb or (today - dt.date.fromisoformat(hb)).days > E.CFG.get("think_max_age_days", 9):
        print("Arena pausiert (kein Claude-Denk-Lauf)")
        return
    tb = {t: x[x.index.date == today] for t, x in bars.items()}
    ctx = {"day": str(today), "time": str(now.time())[:5], "bars": tb, "closes": None}
    decs = sorted(f for f in os.listdir(os.path.join(E.A, "decisions"))) if os.path.isdir(os.path.join(E.A, "decisions")) else []
    lastdec = E.jl(os.path.join(E.A, "decisions", decs[-1]), {}) if decs else {}
    for net in [n for n in st["nets"].values() if n["status"] == "lebt" and n["kind"] == "frei"]:
        c = next((c for c in st["cohorts"] if c["id"] == net.get("cohort")), {})
        if c.get("paused") or net.get("mutation_pending"):
            continue
        if not (lastdec.get(net["id"]) or {}).get("allow_intraday"):
            continue     # Intraday nur, wenn der Supervisor (Claude) es im letzten Entscheidungslauf freigegeben hat
        p = os.path.join(E.NETS, net["id"], "strategy.py")
        spec = importlib.util.spec_from_file_location(f"s_{net['id']}", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "decide_intraday"):
            continue
        bp = os.path.join(E.NETS, net["id"], "book.json")
        book = E.jl(bp) or E.new_book()
        view = dict(ctx, book=json.loads(json.dumps({k: book[k] for k in ("cash", "open")}, default=str)),
                    genome=yaml.safe_load(open(os.path.join(E.NETS, net["id"], "genome.yaml"))) or {})
        try:
            orders = [o for o in (mod.decide_intraday(view) or []) if o.get("type") == "equity" and o.get("ticker") in tb]
            for o in orders:
                o["fill_px"] = float(tb[o["ticker"]]["Close"].iloc[-1])
                o.setdefault("exit", "close")
            lg = E.execute(book, orders, {"candidates": []}, today, net["id"])
            net["last_intraday"] = {"time": ctx["time"], "orders": [x for x in lg if x.get("ok")]}
        except Exception as e:
            net["last_error"] = f"intraday {type(e).__name__}: {e}"[:300]
        E.jd(book, bp)
    ss.save_state(st)


if __name__ == "__main__":
    run()
