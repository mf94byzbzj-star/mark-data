"""Ereignisstudien für Agent 10/11: Rendite einer Aktie nach einem Ereignis, marktbereinigt (minus SPY), gegen die
Basis „beliebiger Tag desselben Tickers“. Einstieg am ersten Schlusskurs NACH dem Ereignistag (kein Lookahead).
Bewertung mit denselben Regeln wie alle Agenten (stats.walk_forward / verdict, BH über alle Tests)."""
import functools

import numpy as np
import pandas as pd

from chain.core import data, stats

COST = 0.001          # Hin- und Rückweg ~0,1 % (liquide US-Aktien)


@functools.lru_cache(maxsize=None)
def closes(t):
    px = data.prices(t)
    if px is None or px.empty:
        return None
    s = px["Close"].copy()
    s.index = pd.DatetimeIndex(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")]


def fwd_excess(t, event_dates, h, direction=1):
    """Je Ereignis: Einstieg erster Handelstag > Ereignistag, Ausstieg h Handelstage später. Rendite minus SPY."""
    c, m = closes(t), closes("SPY")
    if c is None or m is None:
        return pd.DataFrame()
    idx = c.index
    ev = pd.DatetimeIndex(pd.to_datetime(pd.Series(event_dates), errors="coerce").dropna()).normalize()
    pos = idx.searchsorted(ev, side="right")
    rows = []
    for d, p in zip(ev, pos):
        if p + h >= len(idx):
            continue
        e0, e1 = idx[p], idx[p + h]
        if e0 not in m.index or e1 not in m.index:
            continue
        r = c.iloc[p + h] / c.iloc[p] - 1 - (m.loc[e1] / m.loc[e0] - 1)
        rows.append({"event": d, "entry": e0, "ticker": t, "ret": direction * r})
    df = pd.DataFrame(rows)
    if len(df):
        df["ret_stress"] = df["ret"] - 3 * COST
        df["ret"] = df["ret"] - COST
    return df


def baseline(tickers, h, step=5, since="2012-01-01", direction=1):
    """Basis: jeder 5. Handelstag aller Ticker (gleiche Haltedauer, gleiche Bereinigung)."""
    out = []
    for t in tickers:
        c = closes(t)
        if c is None:
            continue
        d = c.index[(c.index >= since)][::step]
        out.append(fwd_excess(t, d - pd.Timedelta(days=1), h, direction))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def evaluate(tests, cfg):
    """tests: Liste von dicts {id, name, dir, h, trades(DataFrame), base(DataFrame)} → Ergebnisliste mit Status."""
    res = []
    for t in tests:
        tr, base = t["trades"], t["base"]
        if tr is None or len(tr) == 0:
            res.append({**{k: v for k, v in t.items() if k not in ("trades", "base")}, "status": "zu wenig Daten", "oos": {"n": 0}})
            continue
        allx = pd.concat([tr.assign(_sig=True), base.assign(_sig=False)], ignore_index=True)
        split = stats.split_date(allx["entry"], cfg["in_sample_frac"])
        wf = stats.walk_forward(allx, allx["_sig"].values, split, base_mask=~allx["_sig"].values)
        # Mehrwert-t: Signal minus Basis-Mittel (OOS), geclustert nach Monat
        o = tr[pd.DatetimeIndex(tr["entry"]) >= split]
        bo = base[pd.DatetimeIndex(base["entry"]) >= split]
        edge = o["ret"].values - (bo["ret"].mean() if len(bo) else 0)
        et = stats.cluster_t(edge, pd.DatetimeIndex(o["entry"]).to_period("M").astype(str)) if len(o) > 5 else np.nan
        wf["edge"] = float(np.mean(edge)) if len(edge) else None
        wf["edge_t"] = float(et) if et == et else None
        res.append({**{k: v for k, v in t.items() if k not in ("trades", "base")}, **wf, "split": str(split.date()),
                    "yearly": tr.groupby(pd.DatetimeIndex(tr["entry"]).year)["ret"].mean().round(4).to_dict()})
    p = [stats.p_one_sided(r.get("edge_t") or 0) for r in res]
    sig = stats.bh(np.array(p), cfg["fdr_q"]) if res else []
    for r, s in zip(res, sig):
        r["bh"] = bool(s)
        if r.get("status") == "zu wenig Daten":
            continue
        st = stats.verdict(r, cfg)
        # Mehrwert gegenüber der Basis ist Pflicht (wie bei Chartmustern)
        et = r.get("edge_t") or 0
        if st == "bestätigt" and et < cfg["min_oos_t"]:
            st = "Kandidat"
        if st == "Kandidat" and et < cfg.get("cand_t", 1.5):   # P001
            st = "beobachten"
        if st in ("Kandidat", "beobachten") and et < 1.0:
            st = "verworfen"
        r["status"] = st
    return res
