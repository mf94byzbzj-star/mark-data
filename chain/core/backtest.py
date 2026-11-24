"""Options-Backtester auf der verdichteten Vola-Oberfläche (nur Strategien mit begrenztem Risiko).

Jeder Trade wird mit dem Smile des Einstiegstags bepreist (Strikes über Delta), am Ausstiegstag mit dem Smile
desselben Verfalls neu bewertet, am Verfall mit innerem Wert. Kosten: Anteil des halben Bid-Ask-Spreads je Leg
(aus dem echten ATM-Spread), Stresstest mit dreifachen Kosten. Strikes sind theoretisch (nicht auf das Strike-Raster
gerundet) – die Live-Stufe (Agent 3) baut die Trades mit echten Strikes aus der Kette.

Ergebnis je Trade: ret = P&L / maximales Risiko (Return on Risk), ret_stress mit Stresskosten.
"""
import math
import os

import numpy as np
import pandas as pd

from . import data

R = 0.04
MAX_SPR = 0.25   # ATM-Spread über 25 % = illiquide, kein Einstieg
PTS = [(0.10, "iv_c10"), (0.25, "iv_c25"), (0.50, "iv_atm"), (0.75, "iv_p25"), (0.90, "iv_p10")]
SQ2 = math.sqrt(2.0)


# ------------------------------------------------------------------ schnelle Skalar-BS (math statt scipy)
def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / SQ2))


def bsp(S, K, T, sig, cp):
    if T <= 1e-6:
        return max(cp * (S - K), 0.0)
    sig = max(sig, 1e-3)
    st = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (R + 0.5 * sig * sig) * T) / st
    d2 = d1 - st
    if cp > 0:
        return S * ncdf(d1) - K * math.exp(-R * T) * ncdf(d2)
    return K * math.exp(-R * T) * ncdf(-d2) - S * ncdf(-d1)


def ninv(p):
    # Acklam-Näherung der inversen Normalverteilung
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    p = min(max(p, 1e-6), 1 - 1e-6)
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > 1 - 0.02425:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


class Slice:
    """Smile eines Verfalls an einem Tag."""
    __slots__ = ("S", "T", "dte", "dx", "dy", "kx", "ky", "spr", "atm_mid")

    def __init__(self, row, S=None):
        self.dte = int(row["dte"])
        self.T = max(self.dte, 0.5) / 365.0
        self.S = float(S) if S else float(row["fwd"]) * math.exp(-R * self.T)
        dx, dy = [], []
        for d, c in PTS:
            v = row[c]
            if v == v and 0.01 < v < 4:
                dx.append(d)
                dy.append(float(v))
        self.dx, self.dy = dx, dy
        kx = [math.log(self._k(d, v) / self.S) for d, v in zip(dx, dy)]
        o = np.argsort(kx)
        self.kx = [kx[i] for i in o]
        self.ky = [dy[i] for i in o]
        sp = row["spr_atm"]
        self.spr = float(sp) if sp == sp and sp > 0 else 0.10
        self.atm_mid = bsp(self.S, self.S, self.T, self.iv_d(0.5), 1)

    def _k(self, d, iv):
        return self.S * math.exp(-ninv(d) * iv * math.sqrt(self.T) + (R + 0.5 * iv * iv) * self.T)

    def iv_d(self, d):
        return float(np.interp(d, self.dx, self.dy))

    def strike(self, d):
        """d = Call-Delta-Äquivalent (Put-Delta p -> 1+|p|... hier: Put −0.25 -> 0.75)."""
        return self._k(d, self.iv_d(d))

    def iv(self, K):
        return float(np.interp(math.log(K / self.S), self.kx, self.ky))

    def px(self, K, cp, S=None):
        S = self.S if S is None else S
        return bsp(S, K, self.T, self.iv(K), cp)

    def half_spread(self, mid):
        # absoluter Spread am ATM-Spread verankert: OTM günstiger, tief ITM nicht proportional teurer
        a = max(self.atm_mid, 0.05)
        return max(0.5 * self.spr * a * min(max(mid / a, 0.25), 1.5), 0.005)


# ------------------------------------------------------------------ Strategien
# Leg: (Verfall 'f'/'b', cp +1/-1, Strike-Spez, Menge). Strike-Spez: 'atm', Delta mit Vorzeichen (Put negativ),
#      oder ('same', i) = Strike von Leg i.
STRATS = {
    "cal_atm":   dict(name="Call-Calendar ATM (30/60)", legs=[("f", 1, "atm", -1), ("b", 1, ("same", 0), 1)], exit="front", bias="neutral", vol="long"),
    "cal_put":   dict(name="Put-Calendar ATM (30/60)", legs=[("f", -1, "atm", -1), ("b", -1, ("same", 0), 1)], exit="front", bias="neutral", vol="long"),
    "dcal":      dict(name="Double Calendar 25Δ (30/60)", legs=[("f", -1, -0.25, -1), ("b", -1, ("same", 0), 1), ("f", 1, 0.25, -1), ("b", 1, ("same", 2), 1)], exit="front", bias="neutral", vol="long"),
    "pcs":       dict(name="Put Credit Spread 25/10Δ", legs=[("f", -1, -0.25, -1), ("f", -1, -0.10, 1)], exit="tp50", bias="bull", vol="short"),
    "ccs":       dict(name="Call Credit Spread 25/10Δ", legs=[("f", 1, 0.25, -1), ("f", 1, 0.10, 1)], exit="tp50", bias="bear", vol="short"),
    "ic":        dict(name="Iron Condor 16/8Δ", legs=[("f", -1, -0.16, -1), ("f", -1, -0.08, 1), ("f", 1, 0.16, -1), ("f", 1, 0.08, 1)], exit="tp50", bias="neutral", vol="short"),
    "ifly":      dict(name="Iron Butterfly ATM, Flügel 12Δ", legs=[("f", -1, "atm", -1), ("f", 1, ("same", 0), -1), ("f", -1, -0.12, 1), ("f", 1, 0.12, 1)], exit="tp25", bias="neutral", vol="short"),
    "bcs":       dict(name="Bull Call Debit Spread 50/25Δ", legs=[("f", 1, 0.50, 1), ("f", 1, 0.25, -1)], exit="hold", bias="bull", vol="long"),
    "bps":       dict(name="Bear Put Debit Spread 50/25Δ", legs=[("f", -1, -0.50, 1), ("f", -1, -0.25, -1)], exit="hold", bias="bear", vol="long"),
    "lstrad":    dict(name="Long Straddle ATM (14 T halten)", legs=[("f", 1, "atm", 1), ("f", -1, ("same", 0), 1)], exit="days14", bias="neutral", vol="long"),
    # Earnings (Einstieg über Event-Fenster statt wöchentlich)
    "pre_earn":  dict(name="Long Straddle vor Earnings (Ausstieg vor Termin)", legs=[("f", 1, "atm", 1), ("f", -1, ("same", 0), 1)], exit="pre", bias="neutral", vol="long", event=(5, 14)),
    "earn_ifly": dict(name="Iron Butterfly über Earnings (Ausstieg danach)", legs=[("f", -1, "atm", -1), ("f", 1, ("same", 0), -1), ("f", -1, -0.12, 1), ("f", 1, 0.12, 1)], exit="post", bias="neutral", vol="short", event=(1, 4)),
}

# P002 (freigegeben 23.11.2026): Varianten mit Flügelbreite nach Risikobudget wie im Live-Trade (Agent 3 verengt die
# Flügel, damit das Risiko je Kontrakt ins 2-%-Budget passt). Strike-Spez ("wing", i, pct) = Strike von Leg i
# minus (Put) bzw. plus (Call) pct × Kurs. Nur Kennzahlen für Live; Status/Trigger-Bewertung bleibt auf der Basisstruktur.
for _w, _tag in ((0.015, "w15"), (0.03, "w30")):
    _pct = f"{_w*100:.1f}".replace(".", ",")
    STRATS[f"pcs_{_tag}"] = dict(name=f"Put Credit Spread 25Δ, Flügel {_pct} %", legs=[("f", -1, -0.25, -1), ("f", -1, ("wing", 0, _w), 1)], exit="tp50", bias="bull", vol="short", variant_of="pcs", wing=_w)
    STRATS[f"ccs_{_tag}"] = dict(name=f"Call Credit Spread 25Δ, Flügel {_pct} %", legs=[("f", 1, 0.25, -1), ("f", 1, ("wing", 0, _w), 1)], exit="tp50", bias="bear", vol="short", variant_of="ccs", wing=_w)
    STRATS[f"ic_{_tag}"] = dict(name=f"Iron Condor 16Δ, Flügel {_pct} %", legs=[("f", -1, -0.16, -1), ("f", -1, ("wing", 0, _w), 1), ("f", 1, 0.16, -1), ("f", 1, ("wing", 2, _w), 1)], exit="tp50", bias="neutral", vol="short", variant_of="ic", wing=_w)
    STRATS[f"ifly_{_tag}"] = dict(name=f"Iron Butterfly ATM, Flügel {_pct} %", legs=[("f", -1, "atm", -1), ("f", 1, ("same", 0), -1), ("f", -1, ("wing", 0, _w), 1), ("f", 1, ("wing", 0, _w), 1)], exit="tp25", bias="neutral", vol="short", variant_of="ifly", wing=_w)
VARIANTS = {k: v["variant_of"] for k, v in STRATS.items() if v.get("variant_of")}
BASE_STRATS = [k for k in STRATS if k not in VARIANTS]


def _dcall(spec):
    """Delta-Spez -> Call-Delta-Äquivalent für den Smile."""
    if spec == "atm":
        return 0.5
    return spec if spec > 0 else 1.0 + spec


class Book:
    """Alle Slices eines Tickers: (Datum, Verfall) -> Slice; plus Kursreihe."""

    def __init__(self, sym):
        self.sym = sym
        s = data.surface(sym)
        self.ok = s is not None and len(s) > 0
        if not self.ok:
            return
        self.dates = pd.DatetimeIndex(sorted(s["date"].unique()))
        self.by, self.spot = {}, {}
        for d, g in s.groupby("date"):
            self.by[d] = {e: r for e, r in zip(g["expiry"], g.to_dict("records"))}
            # ein Kurs je Tag für alle Verfälle (sonst erzeugen Forward-Fehler Schein-Gewinne bei Calendars)
            self.spot[d] = float(np.median(g["fwd"] * np.exp(-R * g["dte"].clip(lower=1) / 365)))
        self._cache = {}
        px = data.prices(sym)
        self.close = px["Close"] if px is not None else None
        self.earn = data.earnings(sym)

    def sl(self, d, e):
        k = (d, e)
        if k not in self._cache:
            row = self.by.get(d, {}).get(e)
            self._cache[k] = Slice(row, self.spot.get(d)) if row is not None else None
        return self._cache[k]

    def slice_fallback(self, d, e):
        """Verfall fehlt an diesem Tag -> Smile des nächstgelegenen Verfalls, Laufzeit angepasst."""
        s = self.sl(d, e)
        if s is not None:
            return s
        rows = self.by.get(d)
        if not rows:
            return None
        e2 = min(rows, key=lambda x: abs((x - e).days))
        # DoltHub führt Verfälle erst ab ~11 Tagen Restlaufzeit: kurz vor Verfall den kürzesten Verfall als IV-Näherung
        if (e - d).days <= 10:
            e2 = min(rows)
        elif abs((e2 - e).days) > 10:
            return None
        s2 = self.sl(d, e2)
        if s2 is None:
            return None
        import copy
        s3 = copy.copy(s2)
        s3.dte = max((e - d).days, 0)
        s3.T = max(s3.dte, 0.5) / 365
        return s3

    def spot_close(self, d):
        """Schlusskurs am Tag d in derselben (unbereinigten) Einheit wie die Strikes.
        Fehlerbehebung 24.11.2026: prices/ sind dividenden-/split-bereinigt (SPY 2019 ~10 % unter dem echten Kurs) –
        ohne Rückrechnung wurden Puts am Verfall systematisch zu tief im Geld abgerechnet (bps/pcs/ic verzerrt).
        Faktor = Oberflächen-Spot / bereinigter Schluss am letzten Oberflächen-Tag ≤ d (max. 10 Tage zurück)."""
        if self.close is None:
            return None
        i = self.close.index.searchsorted(d, side="right") - 1
        if i < 0:
            return None
        c = float(self.close.iloc[i])
        j = self.dates.searchsorted(d, side="right") - 1
        if j < 0 or (d - self.dates[j]).days > 10:
            return None
        fs = []   # Median über die letzten bis zu 10 Oberflächen-Tage (≤ 30 Tage) glättet Forward-Rauschen
        for sd in self.dates[max(0, j - 9):j + 1]:
            if (d - sd).days > 30:
                continue
            k = self.close.index.searchsorted(sd, side="right") - 1
            if k >= 0 and float(self.close.iloc[k]) > 0:
                fs.append(self.spot[sd] / float(self.close.iloc[k]))
        f = float(np.median(fs)) if fs else np.nan
        if not (0.5 < f < 2.0):
            return None
        return c * f

    def pick_exp(self, d, target, lo, hi):
        best = None
        for e, r in self.by[d].items():
            dte = r["dte"]
            if lo <= dte <= hi and (best is None or abs(dte - target) < abs(best[1] - target)):
                best = (e, dte)
        return best[0] if best else None


def _build(book, d, spec):
    """Trade am Tag d aufbauen. Rückgabe: legs [(exp, cp, K, qty, mid, hs)] oder None."""
    ef = book.pick_exp(d, 30, 20, 42)
    if ef is None:
        return None
    eb = None
    if any(l[0] == "b" for l in spec["legs"]):
        eb = book.pick_exp(d, 60, 45, 80)
        if eb is None or eb <= ef:
            return None
    legs = []
    for exp_k, cp, ks, qty in spec["legs"]:
        e = ef if exp_k == "f" else eb
        s = book.sl(d, e)
        if s is None or len(s.dx) < 2 or s.spr > MAX_SPR:
            return None
        if isinstance(ks, tuple) and ks[0] == "wing":
            K = legs[ks[1]][2] + cp * ks[2] * s.S
            if K <= 0:
                return None
        elif isinstance(ks, tuple):
            K = legs[ks[1]][2]
        elif ks == "atm":
            K = s.S
        else:
            if abs(ks) < 0.2 and min(s.dx) > 0.12 and _dcall(ks) < 0.5:
                pass  # leichte Extrapolation (flach) zulässig
            K = s.strike(_dcall(ks))
        mid = s.px(K, cp)
        legs.append((e, cp, K, qty, mid, s.half_spread(mid)))
    return legs


def _value(book, d, legs, at_expiry_S=None):
    """Wert der Position am Tag d (Mid) + halbe Spreads für einen Ausstieg."""
    v, hs = 0.0, 0.0
    for e, cp, K, qty, _, _ in legs:
        if d >= e:
            S = at_expiry_S if at_expiry_S is not None else book.spot_close(e)
            if S is None:
                return None, None
            v += qty * max(cp * (S - K), 0.0)
            continue
        s = book.slice_fallback(d, e)
        if s is None:
            return None, None
        m = s.px(K, cp)
        v += qty * m
        hs += s.half_spread(m)
    return v, hs


def _max_risk(legs, debit):
    """Maximaler Verlust je Aktie. Gleicher Verfall: Auszahlung über ein Kursraster; sonst gezahlte Prämie."""
    exps = {l[0] for l in legs}
    if len(exps) > 1:
        return max(debit, 1e-6)
    ks = [l[2] for l in legs]
    grid = np.concatenate([np.linspace(min(ks) * 0.5, max(ks) * 1.5, 400), ks])
    pay = np.zeros_like(grid)
    for _, cp, K, qty, _, _ in legs:
        pay += qty * np.maximum(cp * (grid - K), 0.0)
    worst = (pay - debit).min()
    return max(-worst, 1e-6)


def simulate(book, d, key, slip=0.5, stress=3.0, last_date=None):
    spec = STRATS[key]
    legs = _build(book, d, spec)
    key0, key = key, spec.get("variant_of", key)   # Strike-Prüfungen wie bei der Basisstruktur
    if legs is None:
        return None
    if not all(np.isfinite(l[2]) and np.isfinite(l[4]) for l in legs):
        return None
    # Strikes müssen zur Struktur passen (z. B. Short-Put über Long-Put), sonst ist der Smile fehlerhaft
    ks = [l[2] for l in legs]
    if spec.get("vol") == "short" or key in ("bcs", "bps"):
        same_exp = len({l[0] for l in legs}) == 1
        if same_exp and len(set(np.round(ks, 6))) < len(ks) - (1 if key in ("ifly", "earn_ifly") else 0):
            return None
    if key == "pcs" and not ks[0] > ks[1]:
        return None
    if key == "ccs" and not ks[0] < ks[1]:
        return None
    if key == "ic" and not (ks[1] < ks[0] < ks[2] < ks[3]):
        return None
    if key in ("ifly", "earn_ifly") and not (ks[2] < ks[0] < ks[3]):
        return None
    if key == "bcs" and not ks[0] < ks[1]:
        return None
    if key == "bps" and not ks[0] > ks[1]:
        return None
    debit = sum(l[3] * l[4] for l in legs)              # >0 bezahlt, <0 eingenommen
    if spec["exit"] == "front" and debit <= 0:          # Calendar muss etwas kosten, sonst Datenfehler
        return None
    cost_in = sum(l[5] for l in legs)
    risk = _max_risk(legs, debit)
    S_ref = book.sl(d, legs[0][0]).S
    if risk <= max(0.002 * S_ref, 0.02) or not np.isfinite(risk):
        return None
    if len({l[0] for l in legs}) == 1 and len(set(ks)) > 1:
        width = max(ks) - min(ks)
        if key0 in VARIANTS and key in ("ic", "ifly"):   # schmale Flügel: Breite je Seite, nicht Gesamtspanne
            width = max(ks[0] - ks[1], ks[3] - ks[2]) if key == "ic" else max(ks[0] - ks[2], ks[3] - ks[0])
        if risk < 0.1 * width or risk > 0.95 * width + max(debit, 0):   # unplausible Prämie
            return None
    ef = min(l[0] for l in legs)
    later = book.dates[(book.dates > d)]
    exit_d, reason, val, hs_out = None, None, None, 0.0
    mode = spec["exit"]
    if mode in ("tp50", "tp25", "hold"):
        tp = {"tp50": 0.5, "tp25": 0.25, "hold": None}[mode]
        for x in later[later < ef]:
            v, h = _value(book, x, legs)
            if v is None:
                continue
            if tp is not None and debit < 0 and (-v) <= (1 - tp) * (-debit):  # Gewinnmitnahme bei Credit-Trades
                exit_d, reason, val, hs_out = x, f"tp{int(tp*100)}", v, h
                break
        if exit_d is None:
            if last_date is not None and ef > last_date:
                return {"open": True}
            S = book.spot_close(ef)
            if S is None:
                return None
            exit_d, reason = ef, "verfall"
            val, hs_out = _value(book, ef, legs, at_expiry_S=S)
    elif mode == "front":
        cand = later[(later <= ef - pd.Timedelta(days=1)) & (later >= ef - pd.Timedelta(days=6))]
        if len(cand) == 0:
            if last_date is not None and ef > last_date:
                return {"open": True}
            return None
        exit_d, reason = cand[-1], "front-verfall"
        val, hs_out = _value(book, exit_d, legs)
    elif mode.startswith("days"):
        n = int(mode[4:])
        cand = later[(later <= d + pd.Timedelta(days=n)) & (later < ef)]
        if len(cand) == 0 or (cand[-1] - d) < pd.Timedelta(days=max(n - 6, 3)):
            if last_date is not None and d + pd.Timedelta(days=n) > last_date:
                return {"open": True}
            return None
        exit_d, reason = cand[-1], f"{n}T"
        val, hs_out = _value(book, exit_d, legs)
    elif mode in ("pre", "post"):
        nxt = book.earn[book.earn >= d]
        if len(nxt) == 0:
            return None
        E = nxt[0]
        if mode == "pre":
            cand = later[(later < E) & (later >= E - pd.Timedelta(days=3))]
        else:
            cand = later[(later >= E) & (later <= E + pd.Timedelta(days=5)) & (later < ef)]
        if len(cand) == 0:
            if last_date is not None and E + pd.Timedelta(days=5) > last_date:
                return {"open": True}
            return None
        exit_d = cand[-1] if mode == "pre" else cand[0]
        reason = "vor Earnings" if mode == "pre" else "nach Earnings"
        val, hs_out = _value(book, exit_d, legs)
    if val is None or not np.isfinite(val):
        return None
    gross = val - debit
    cost = slip * (cost_in + hs_out)
    pnl = gross - cost
    pnl_s = gross - stress * cost
    S0 = book.sl(d, legs[0][0]).S
    return {"ticker": book.sym, "strat": key0, "entry": d, "exit": exit_d, "reason": reason,
            "dte": int((ef - d).days), "spot": round(S0, 3),
            "strikes": "/".join(f"{l[2]:.2f}" for l in legs), "debit": round(debit, 4), "risk": round(risk, 4),
            "pnl": round(pnl, 4), "ret": round(pnl / risk, 4), "ret_stress": round(pnl_s / risk, 4),
            "cost_ror": round(cost / risk, 4)}


def entry_dates(book, key):
    """Wöchentliche Einstiege (erster Datentag je Kalenderwoche); Earnings-Strategien je Event einmal."""
    spec = STRATS[key]
    ds = pd.DatetimeIndex(book.dates)
    if "event" not in spec:
        wk = ds.to_period("W")
        return list(pd.Series(ds, index=ds).groupby(wk).first().values)
    lo, hi = spec["event"]
    out = []
    for E in book.earn:
        cand = ds[(ds >= E - pd.Timedelta(days=hi)) & (ds <= E - pd.Timedelta(days=lo))]
        if len(cand):
            out.append(cand[0] if key == "pre_earn" else cand[-1])
    return out


def run_ticker(sym, keys=None, since=None):
    book = Book(sym)
    if not book.ok:
        return pd.DataFrame(), pd.DataFrame()
    last = book.dates[-1]
    rows, open_rows = [], []
    for k in keys or STRATS:
        for d in entry_dates(book, k):
            if since is not None and d < since:
                continue
            try:
                r = simulate(book, pd.Timestamp(d), k, last_date=last)
            except Exception:
                r = None
            if r is None:
                continue
            if r.get("open"):
                open_rows.append({"ticker": sym, "strat": k, "entry": d})
            else:
                rows.append(r)
    return pd.DataFrame(rows), pd.DataFrame(open_rows)
