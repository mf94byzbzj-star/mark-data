"""Merkmale je Ticker und Tag – die Bausteine, aus denen Agent 1 und 2 ihre Trigger und Regeln formulieren.

Options-Merkmale (aus der Vola-Oberfläche):
  iv_f, dte_f       ATM-IV und Laufzeit des Front-Verfalls (Ziel 30 T, Fenster 20–40)
  iv_b, dte_b       ATM-IV und Laufzeit des Back-Verfalls (Ziel 60 T, Fenster 45–75)
  iv30              ATM-IV, auf 30 Tage interpoliert (Gesamtvarianz)
  ff                Forward Factor = IV_front / Forward-Vol(front→back) − 1   (Volatility Vibes / oquants)
  ff_s              Forward Factor kurz (Ziel 14 T vs. 30 T)
  slope             Term-Structure: IV_back / IV_front − 1  (>0 Contango, <0 Backwardation)
  skew25            (IV Put25 − IV Call25) / IV ATM  (30 T)
  pskew, cskew      IV Put25 / ATM − 1 ; IV Call25 / ATM − 1
  wing10            IV Put10 / ATM − 1
  spr_atm           relativer Bid-Ask-Spread ATM (Liquidität)
  iv_rank, iv_pct   IV30 gegenüber den letzten 365 Tagen
  iv_chg5           Veränderung IV30 über ~5 Handelstage
  skew_z            z-Wert skew25 über 365 Tage
Kurs-/Volamerkmale:
  rv10, rv20, rv60  realisierte Vola (Close-to-Close, annualisiert)
  ivrv, vrp         IV30/RV20 ; IV30 − RV20
  ret5, ret20, ret60, dist50, dist200, rsi2, rsi14, atr_pct, adx14, bbw, bbw_rank, dd52, mom12_1
Event:
  d2e               Kalendertage bis zu den nächsten Earnings (NaN = unbekannt / ETF)
  e_in_f            Earnings vor dem Front-Verfall (0/1)
Markt (für alle Ticker gleich):
  vix, vix_rank, vix_ts (VIX/VIX3M), spy_dd52, spy_dist200, breadth200 (wird in market() berechnet)
"""
import numpy as np
import pandas as pd

from . import data

R = 0.04


def _pick(g, target, lo, hi):
    c = g[(g.dte >= lo) & (g.dte <= hi)]
    if c.empty:
        return None
    return c.iloc[(c.dte - target).abs().argsort().iloc[0]]


def _fwd_factor(iv1, t1, iv2, t2):
    if not all(np.isfinite([iv1, iv2])) or t2 <= t1:
        return np.nan
    fv = (iv2 ** 2 * t2 - iv1 ** 2 * t1) / (t2 - t1)
    if fv <= 1e-6:
        return 3.0  # extreme Inversion -> gekappt
    return min(iv1 / np.sqrt(fv) - 1.0, 3.0)


def _iv_at(g, days):
    """ATM-IV auf 'days' interpoliert in Gesamtvarianz; flach außerhalb."""
    g = g.dropna(subset=["iv_atm"])
    if g.empty:
        return np.nan
    t = g.dte.values / 365.0
    w = g.iv_atm.values ** 2 * t
    tt = days / 365.0
    if tt <= t[0]:
        return float(g.iv_atm.values[0])
    if tt >= t[-1]:
        return float(g.iv_atm.values[-1])
    return float(np.sqrt(np.interp(tt, t, w) / tt))


def option_features(sym, extra=None, since=None):
    s = data.surface(sym)
    if s is not None and since is not None:
        s = s[s["date"] >= since]
    if extra is not None and len(extra):
        from .surface import clean
        extra = clean(extra)
        s = extra if s is None else pd.concat([s[s["date"] < extra["date"].min()], extra])
    if s is None or s.empty:
        return None
    rows = []
    for d, g in s.groupby("date", sort=True):
        g = g.sort_values("dte")
        f = _pick(g, 30, 20, 42)
        b = _pick(g, 60, 45, 80)
        fs = _pick(g, 14, 7, 21)
        r = {"date": d, "n_exp": len(g), "max_dte": int(g.dte.max())}
        r["iv30"] = _iv_at(g, 30)
        if f is not None:
            r.update(iv_f=f.iv_atm, dte_f=f.dte, exp_f=f.expiry, spr_atm=f.spr_atm,
                     skew25=(f.iv_p25 - f.iv_c25) / f.iv_atm, pskew=f.iv_p25 / f.iv_atm - 1,
                     cskew=f.iv_c25 / f.iv_atm - 1, wing10=f.iv_p10 / f.iv_atm - 1,
                     spot_imp=f.fwd * np.exp(-R * f.dte / 365))
        if b is not None:
            r.update(iv_b=b.iv_atm, dte_b=b.dte, exp_b=b.expiry)
        if f is not None and b is not None:
            r["ff"] = _fwd_factor(f.iv_atm, f.dte / 365, b.iv_atm, b.dte / 365)
            r["slope"] = b.iv_atm / f.iv_atm - 1
        if fs is not None and f is not None and fs.expiry != f.expiry:
            r["ff_s"] = _fwd_factor(fs.iv_atm, fs.dte / 365, f.iv_atm, f.dte / 365)
        rows.append(r)
    df = pd.DataFrame(rows).set_index("date")
    for c in ("iv_f", "dte_f", "exp_f", "spr_atm", "skew25", "pskew", "cskew", "wing10", "spot_imp", "iv_b", "dte_b", "exp_b", "ff", "slope", "ff_s"):
        if c not in df:
            df[c] = np.nan
    # Ränge über 365 Kalendertage (Datenabstände anfangs wöchentlich -> zeitbasiertes Fenster)
    iv = df["iv30"]
    roll = iv.rolling("365D", min_periods=20)
    lo, hi = roll.min(), roll.max()
    df["iv_rank"] = (iv - lo) / (hi - lo).replace(0, np.nan)
    df["iv_pct"] = iv.rolling("365D", min_periods=20).apply(lambda x: (x[:-1] < x[-1]).mean() if len(x) > 1 else np.nan, raw=True)
    prev = iv.dropna()
    df["iv_chg5"] = iv.values - prev.reindex(prev.index.union(df.index - pd.Timedelta("7D"))).ffill().reindex(df.index - pd.Timedelta("7D")).values
    sk = df["skew25"]
    df["skew_z"] = (sk - sk.rolling("365D", min_periods=20).mean()) / sk.rolling("365D", min_periods=20).std()
    return df


def _rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=c.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    ndi = 100 * pd.Series(ndm, index=c.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), atr


def price_features(sym):
    px = data.prices(sym)
    if px is None or len(px) < 60:
        return None
    c, h, l = px["Close"], px["High"], px["Low"]
    lr = np.log(c).diff()
    f = pd.DataFrame(index=px.index)
    f["close"] = c
    for n in (10, 20, 60):
        f[f"rv{n}"] = lr.rolling(n).std() * np.sqrt(252)
    for n in (5, 20, 60):
        f[f"ret{n}"] = c / c.shift(n) - 1
    f["mom12_1"] = c.shift(21) / c.shift(252) - 1
    f["dist50"] = c / c.rolling(50).mean() - 1
    f["dist200"] = c / c.rolling(200).mean() - 1
    f["rsi2"] = _rsi(c, 2)
    f["rsi14"] = _rsi(c, 14)
    adx, atr = _adx(h, l, c)
    f["adx14"] = adx
    f["atr_pct"] = atr / c
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    f["bbw"] = 4 * sd / ma
    f["bbw_rank"] = f["bbw"].rolling(252, min_periods=60).rank(pct=True)
    f["dd52"] = c / c.rolling(252, min_periods=20).max() - 1
    f["fut_ret5"] = c.shift(-5) / c - 1     # ZUKUNFT – nur Zielgröße für Equity-Backtests, nie als Trigger
    f["fut_ret10"] = c.shift(-10) / c - 1
    f["fut_ret20"] = c.shift(-20) / c - 1
    return f


def market():
    """Marktweite Merkmale je Tag."""
    vix = data.prices("^VIX")
    vix3m = data.prices("^VIX3M")
    spy = data.prices("SPY")
    m = pd.DataFrame(index=spy.index if spy is not None else pd.DatetimeIndex([]))
    if spy is not None:
        c = spy["Close"]
        m["spy_dd52"] = c / c.rolling(252, min_periods=20).max() - 1
        m["spy_dist200"] = c / c.rolling(200).mean() - 1
        m["spy_ret20"] = c / c.shift(20) - 1
    if vix is not None:
        m["vix"] = vix["Close"].reindex(m.index).ffill()
        v = m["vix"]
        m["vix_rank"] = (v - v.rolling(252).min()) / (v.rolling(252).max() - v.rolling(252).min())
    if vix3m is not None and vix is not None:
        m["vix_ts"] = m["vix"] / vix3m["Close"].reindex(m.index).ffill()
    # Marktbreite: Anteil Universum über SMA200
    above, n = None, None
    for t in data.UNIV:
        px = data.prices(t)
        if px is None or len(px) < 220:
            continue
        a = (px["Close"] > px["Close"].rolling(200).mean()).astype(float).reindex(m.index)
        ok = a.notna().astype(float)
        above = a.fillna(0) if above is None else above + a.fillna(0)
        n = ok if n is None else n + ok
    if above is not None:
        m["breadth200"] = above / n.replace(0, np.nan)
    fr = data.fred()
    if not fr.empty:
        fr = fr.reindex(m.index.union(fr.index)).ffill().reindex(m.index)
        if "BAMLH0A0HYM2" in fr:
            m["hy_spread"] = fr["BAMLH0A0HYM2"]
            m["hy_chg20"] = fr["BAMLH0A0HYM2"] - fr["BAMLH0A0HYM2"].shift(20)
        if "T10Y2Y" in fr:
            m["curve_10y2y"] = fr["T10Y2Y"]
        if "NFCI" in fr:
            m["nfci"] = fr["NFCI"]
    return m


def earn_features(sym, dates):
    ed = data.earnings(sym)
    out = pd.DataFrame(index=dates)
    if len(ed) == 0:
        out["d2e"] = np.nan
        out["dse"] = np.nan
        return out
    arr = ed.values
    idx = np.searchsorted(arr, dates.values, side="left")
    nxt = np.where(idx < len(arr), arr[np.minimum(idx, len(arr) - 1)], np.datetime64("NaT"))
    prv = np.where(idx > 0, arr[np.maximum(idx - 1, 0)], np.datetime64("NaT"))
    out["d2e"] = (pd.to_datetime(nxt) - dates).days
    out["dse"] = (dates - pd.to_datetime(prv)).days
    # Earnings-Historie von yfinance reicht ~10 Jahre; davor unbekannt
    out.loc[dates < arr[0] - np.timedelta64(120, "D"), ["d2e", "dse"]] = np.nan
    return out


def ticker_features(sym, mkt=None, extra=None, since=None):
    """Alle Merkmale je Ticker auf den Tagen der Vola-Oberfläche (Einstiegstage der Backtests).
    extra: zusätzliche Oberflächen-Zeilen (z. B. heutige Cboe-Kette) für die Live-Auswertung."""
    of = option_features(sym, extra, since)
    pf = price_features(sym)
    if of is None or pf is None:
        return None
    pf2 = pf.reindex(pf.index.union(of.index)).ffill().reindex(of.index)
    f = of.join(pf2)
    f["ivrv"] = f["iv30"] / f["rv20"]
    f["vrp"] = f["iv30"] - f["rv20"]
    e = earn_features(sym, f.index)
    f = f.join(e)
    if "exp_f" in f:
        f["e_in_f"] = ((f["d2e"] >= 0) & (f["d2e"] <= f["dte_f"])).astype(float).where(f["d2e"].notna())
    if mkt is not None:
        m2 = mkt.reindex(mkt.index.union(f.index)).ffill().reindex(f.index)
        f = f.join(m2)
    f["ticker"] = sym
    f["is_etf"] = float(sym in data.ETFS)
    return f
