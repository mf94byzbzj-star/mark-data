"""Agent 8 – Chartmuster-Erkennung auf Tageskerzen (ohne Lookahead).

Jeder Detektor liefert Ereignisse am Tag, an dem das Muster ABGESCHLOSSEN und damit handelbar ist (Schlusskurs).
Pivots (Swing-Hochs/-Tiefs) gelten erst k Kerzen nach dem Extrem als bestätigt.

Ereignis: dict(i=Index der Signalkerze, dir=+1/−1, stop=Invalidierungs-Kurs, target=Kursziel oder None,
               label=Kurztext, draw=Zeichenobjekte für den Chart)
Zeichenobjekte: {"type": "line", "p": [[i1, y1], [i2, y2]]} · {"type": "zone", "i": i0, "lo": a, "hi": b}
                {"type": "mark", "i": i, "y": y, "text": "H&S"}
"""
import numpy as np
import pandas as pd


# ------------------------------------------------------------------ Hilfen
def atr(h, l, c, n=14):
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    tr = np.concatenate([[h[0] - l[0]], tr])
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def pivots(h, l, k=5):
    """Swing-Hochs/-Tiefs. Rückgabe: Listen (i_extrem, i_bestätigt, preis)."""
    n = len(h)
    ph, pl = [], []
    for i in range(k, n - k):
        if h[i] == h[i - k:i + k + 1].max() and (h[i] > h[i - k:i]).all():
            ph.append((i, i + k, h[i]))
        if l[i] == l[i - k:i + k + 1].min() and (l[i] < l[i - k:i]).all():
            pl.append((i, i + k, l[i]))
    return ph, pl


def _known(piv, t):
    """Pivots, die am Tag t bereits bestätigt sind."""
    return [p for p in piv if p[1] <= t]


# ------------------------------------------------------------------ Kerzenmuster
def candles(o, h, l, c, a):
    ev = []
    body = abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    up_sh = h - np.maximum(o, c)
    lo_sh = np.minimum(o, c) - l
    for i in range(6, len(c)):
        down5 = c[i - 1] < c[i - 6]
        up5 = c[i - 1] > c[i - 6]
        # Engulfing
        if down5 and c[i] > o[i] and c[i - 1] < o[i - 1] and c[i] >= o[i - 1] and o[i] <= c[i - 1] and body[i] > body[i - 1]:
            ev.append(dict(p="bull_engulfing", i=i, dir=1, stop=min(l[i], l[i - 1]), label="Bullish Engulfing"))
        if up5 and c[i] < o[i] and c[i - 1] > o[i - 1] and c[i] <= o[i - 1] and o[i] >= c[i - 1] and body[i] > body[i - 1]:
            ev.append(dict(p="bear_engulfing", i=i, dir=-1, stop=max(h[i], h[i - 1]), label="Bearish Engulfing"))
        # Hammer / Shooting Star (Pin Bar)
        if down5 and lo_sh[i] >= 2 * body[i] and up_sh[i] <= 0.3 * max(body[i], 0.1 * rng[i]) and rng[i] >= 0.8 * a[i]:
            ev.append(dict(p="hammer", i=i, dir=1, stop=l[i], label="Hammer"))
        if up5 and up_sh[i] >= 2 * body[i] and lo_sh[i] <= 0.3 * max(body[i], 0.1 * rng[i]) and rng[i] >= 0.8 * a[i]:
            ev.append(dict(p="shooting_star", i=i, dir=-1, stop=h[i], label="Shooting Star"))
        # Morning / Evening Star
        if down5 and c[i - 2] < o[i - 2] and body[i - 2] > 0.6 * a[i] and body[i - 1] < 0.3 * body[i - 2] \
                and c[i] > o[i] and c[i] > (o[i - 2] + c[i - 2]) / 2:
            ev.append(dict(p="morning_star", i=i, dir=1, stop=min(l[i - 2:i + 1]), label="Morning Star"))
        if up5 and c[i - 2] > o[i - 2] and body[i - 2] > 0.6 * a[i] and body[i - 1] < 0.3 * body[i - 2] \
                and c[i] < o[i] and c[i] < (o[i - 2] + c[i - 2]) / 2:
            ev.append(dict(p="evening_star", i=i, dir=-1, stop=max(h[i - 2:i + 1]), label="Evening Star"))
    return ev


# ------------------------------------------------------------------ Gaps
def gaps(o, h, l, c, a):
    """Gap-Fill-Hypothese: Lücken über 0,5 ATR werden häufig geschlossen → Gegenrichtung (Fade) handeln."""
    ev = []
    for i in range(1, len(c)):
        if l[i] > h[i - 1] and (l[i] - h[i - 1]) > 0.5 * a[i]:
            ev.append(dict(p="gap_up_fade", i=i, dir=-1, stop=h[i] + 0.5 * a[i], target=h[i - 1], label="Gap up (Fill?)",
                           draw=[{"type": "zone", "i": i - 1, "lo": h[i - 1], "hi": l[i], "kind": "gap"}]))
        if h[i] < l[i - 1] and (l[i - 1] - h[i]) > 0.5 * a[i]:
            ev.append(dict(p="gap_down_fade", i=i, dir=1, stop=l[i] - 0.5 * a[i], target=l[i - 1], label="Gap down (Fill?)",
                           draw=[{"type": "zone", "i": i - 1, "lo": h[i], "hi": l[i - 1], "kind": "gap"}]))
    return ev


def open_gaps(h, l, a, lookback=250, max_n=12):
    """Alle noch nicht geschlossenen Kurslücken der letzten ~12 Monate (Tief > Vortageshoch bzw. Hoch < Vortagestief),
    teilweise Füllung wird berücksichtigt (verbleibende Lücke). Unabhängig von Backtests – reine Chart-Information."""
    n = len(h)
    out = []
    for i in range(max(1, n - lookback), n):
        if l[i] > h[i - 1]:
            lo, hi = h[i - 1], l[i]
            low_after = l[i + 1:].min() if i + 1 < n else hi
            if low_after > lo:
                out.append({"type": "zone", "i": i - 1, "lo": float(lo), "hi": float(min(hi, low_after)), "kind": "gap",
                            "dir": "up", "size_atr": round(float((hi - lo) / a[i]), 2)})
        if h[i] < l[i - 1]:
            lo, hi = h[i], l[i - 1]
            high_after = h[i + 1:].max() if i + 1 < n else lo
            if high_after < hi:
                out.append({"type": "zone", "i": i - 1, "lo": float(max(lo, high_after)), "hi": float(hi), "kind": "gap",
                            "dir": "down", "size_atr": round(float((hi - lo) / a[i]), 2)})
    # die nächstgelegenen zuerst behalten (relevant für den aktuellen Kurs)
    return out[-max_n:]


# ------------------------------------------------------------------ Liquidity Sweep
def sweeps(o, h, l, c, a, ph, pl, look=40):
    """Kurs sticht unter ein bestätigtes Swing-Tief (Stops werden abgeholt) und schließt wieder darüber → long; Spiegelbild short."""
    ev = []
    for i in range(20, len(c)):
        lows = [p for p in _known(pl, i - 1) if i - p[0] <= look]
        if lows:
            lvl = min(p[2] for p in lows[-3:])
            if l[i] < lvl - 0.1 * a[i] and c[i] > lvl and c[i] > o[i]:
                ev.append(dict(p="sweep_low", i=i, dir=1, stop=l[i] - 0.2 * a[i], label="Liquidity Sweep (Tief)",
                               draw=[{"type": "line", "p": [[lows[-1][0], lvl], [i, lvl]], "kind": "sweep"}]))
        highs = [p for p in _known(ph, i - 1) if i - p[0] <= look]
        if highs:
            lvl = max(p[2] for p in highs[-3:])
            if h[i] > lvl + 0.1 * a[i] and c[i] < lvl and c[i] < o[i]:
                ev.append(dict(p="sweep_high", i=i, dir=-1, stop=h[i] + 0.2 * a[i], label="Liquidity Sweep (Hoch)",
                               draw=[{"type": "line", "p": [[highs[-1][0], lvl], [i, lvl]], "kind": "sweep"}]))
    return ev


# ------------------------------------------------------------------ Angebots-/Nachfragezonen + Fair Value Gaps
def zones(o, h, l, c, a, max_age=120):
    """Nachfragezone: ruhige Basiskerze (Range < 0,6 ATR) gefolgt von Impuls (≥ 2 ATR in ≤ 3 Kerzen).
    Signal beim ERSTEN Rücklauf in die Zone. Fair Value Gap (3-Kerzen-Lücke) analog."""
    ev, active = [], []
    n = len(c)
    for i in range(3, n):
        # neue Zonen entstehen am Ende des Impulses (Kerze i), Basis = i-3..i-1 kleinste Range
        j = i - 3
        if h[j] - l[j] < 0.6 * a[j]:
            if c[i] - h[j] >= 2 * a[i]:
                active.append({"kind": "demand", "lo": l[j], "hi": h[j], "born": i, "base": j})
            if l[j] - c[i] >= 2 * a[i]:
                active.append({"kind": "supply", "lo": l[j], "hi": h[j], "born": i, "base": j})
        if l[i] > h[i - 2] and (l[i] - h[i - 2]) > 0.3 * a[i]:
            active.append({"kind": "fvg_bull", "lo": h[i - 2], "hi": l[i], "born": i, "base": i - 1})
        if h[i] < l[i - 2] and (l[i - 2] - h[i]) > 0.3 * a[i]:
            active.append({"kind": "fvg_bear", "lo": h[i], "hi": l[i - 2], "born": i, "base": i - 1})
        keep = []
        for z in active:
            if z["born"] >= i:
                keep.append(z)
                continue
            if i - z["born"] > max_age:
                continue
            bull = z["kind"] in ("demand", "fvg_bull")
            touched = l[i] <= z["hi"] if bull else h[i] >= z["lo"]
            broken = c[i] < z["lo"] if bull else c[i] > z["hi"]
            if touched and not broken:
                name = {"demand": "demand_zone", "supply": "supply_zone", "fvg_bull": "fvg_bull", "fvg_bear": "fvg_bear"}[z["kind"]]
                lab = {"demand": "Nachfragezone", "supply": "Angebotszone", "fvg_bull": "Fair Value Gap (bull)", "fvg_bear": "Fair Value Gap (bear)"}[z["kind"]]
                ev.append(dict(p=name, i=i, dir=1 if bull else -1,
                               stop=(z["lo"] - 0.3 * a[i]) if bull else (z["hi"] + 0.3 * a[i]), label=lab,
                               draw=[{"type": "zone", "i": z["base"], "lo": float(z["lo"]), "hi": float(z["hi"]), "kind": z["kind"]}]))
                continue  # Zone verbraucht
            if broken:
                continue
            keep.append(z)
        active = keep
    return ev, active


# ------------------------------------------------------------------ Trendlinien
def _line(p1, p2):
    m = (p2[1] - p1[1]) / max(p2[0] - p1[0], 1)
    return lambda x: p1[1] + m * (x - p1[0]), m


def trendlines(o, h, l, c, a, ph, pl, look=150):
    """Aufwärtstrendlinie durch die letzten zwei steigenden Swing-Tiefs (dritter Kontakt optional);
    Signal: Schluss mehr als 0,3 ATR unter der Linie (Bruch → short). Abwärtstrendlinie durch fallende Hochs:
    Ausbruch darüber → long."""
    ev = []
    fired = set()
    for i in range(30, len(c)):
        lows = [p for p in _known(pl, i - 1) if i - p[0] <= look]
        if len(lows) >= 2 and lows[-1][2] > lows[-2][2] and lows[-1][0] - lows[-2][0] >= 5:
            f, m = _line((lows[-2][0], lows[-2][2]), (lows[-1][0], lows[-1][2]))
            key = ("up", lows[-2][0], lows[-1][0])
            if key not in fired and c[i] < f(i) - 0.3 * a[i] and c[i - 1] >= f(i - 1) - 0.3 * a[i - 1]:
                fired.add(key)
                ev.append(dict(p="trendline_break_down", i=i, dir=-1, stop=f(i) + 0.5 * a[i], label="Bruch Aufwärtstrendlinie",
                               draw=[{"type": "line", "p": [[lows[-2][0], lows[-2][2]], [i, f(i)]], "kind": "trend_up"}]))
        highs = [p for p in _known(ph, i - 1) if i - p[0] <= look]
        if len(highs) >= 2 and highs[-1][2] < highs[-2][2] and highs[-1][0] - highs[-2][0] >= 5:
            f, m = _line((highs[-2][0], highs[-2][2]), (highs[-1][0], highs[-1][2]))
            key = ("dn", highs[-2][0], highs[-1][0])
            if key not in fired and c[i] > f(i) + 0.3 * a[i] and c[i - 1] <= f(i - 1) + 0.3 * a[i - 1]:
                fired.add(key)
                ev.append(dict(p="trendline_break_up", i=i, dir=1, stop=f(i) - 0.5 * a[i], label="Ausbruch Abwärtstrendlinie",
                               draw=[{"type": "line", "p": [[highs[-2][0], highs[-2][2]], [i, f(i)]], "kind": "trend_dn"}]))
    return ev


def current_trendlines(h, l, c, a, look=250):
    """Aktive Trendlinien am letzten Tag – unabhängig von Backtests, nur Chart-Information.
    Kurzfristig (Pivots k=5) und mittelfristig (Pivots k=10): Unterstützungen durch steigende Swing-Tiefs,
    Widerstände durch fallende Swing-Hochs. Eine Linie gilt als gebrochen, wenn der Schlusskurs > 0,3 ATR jenseits liegt."""
    n = len(c)
    out, seen = [], set()
    for k, tag in ((5, "kurz"), (10, "mittel")):
        ph, pl = pivots(h, l, k)
        lows = [p for p in pl if n - 1 - p[0] <= look]
        highs = [p for p in ph if n - 1 - p[0] <= look]
        # bis zu zwei Unterstützungslinien: jüngstes steigendes Tief-Paar, dazu das tiefste ältere Tief mit dem jüngsten
        pairs = []
        if len(lows) >= 2:
            if lows[-1][2] > lows[-2][2]:
                pairs.append((lows[-2], lows[-1]))
            lo_min = min(lows[:-1], key=lambda p: p[2])
            if lo_min[2] < lows[-1][2] and (lo_min, lows[-1]) not in pairs:
                pairs.append((lo_min, lows[-1]))
        for p1, p2 in pairs:
            key = ("up", p1[0], p2[0])
            if key in seen or p2[0] - p1[0] < 5:
                continue
            seen.add(key)
            f, _ = _line((p1[0], p1[2]), (p2[0], p2[2]))
            out.append({"type": "line", "p": [[p1[0], float(p1[2])], [n - 1, float(f(n - 1))]], "kind": "trend_up",
                        "term": tag, "broken": bool(c[-1] < f(n - 1) - 0.3 * a[-1])})
        pairs = []
        if len(highs) >= 2:
            if highs[-1][2] < highs[-2][2]:
                pairs.append((highs[-2], highs[-1]))
            hi_max = max(highs[:-1], key=lambda p: p[2])
            if hi_max[2] > highs[-1][2] and (hi_max, highs[-1]) not in pairs:
                pairs.append((hi_max, highs[-1]))
        for p1, p2 in pairs:
            key = ("dn", p1[0], p2[0])
            if key in seen or p2[0] - p1[0] < 5:
                continue
            seen.add(key)
            f, _ = _line((p1[0], p1[2]), (p2[0], p2[2]))
            out.append({"type": "line", "p": [[p1[0], float(p1[2])], [n - 1, float(f(n - 1))]], "kind": "trend_dn",
                        "term": tag, "broken": bool(c[-1] > f(n - 1) + 0.3 * a[-1])})
    return out


# ------------------------------------------------------------------ Formationen
def formations(o, h, l, c, a, ph, pl):
    """Doppeltop/-boden, Kopf-Schulter (+ invers), Dreiecke (aufsteigend/absteigend/symmetrisch), Flaggen.
    Signal jeweils beim bestätigenden Schluss jenseits der Nackenlinie bzw. Begrenzung."""
    ev = []
    n = len(c)
    done = set()
    for i in range(40, n):
        H = [p for p in _known(ph, i - 1) if i - p[0] <= 120]
        L = [p for p in _known(pl, i - 1) if i - p[0] <= 120]
        # Doppeltop: zwei Hochs ±2 %, Abstand 10–80 Kerzen, Tal dazwischen ≥ 3 % tiefer; Bruch des Tals
        if len(H) >= 2:
            (i1, _, p1), (i2, _, p2) = H[-2], H[-1]
            if abs(p1 / p2 - 1) <= 0.02 and 10 <= i2 - i1 <= 80:
                trough = l[i1:i2 + 1].min()
                if trough <= min(p1, p2) * 0.97 and ("dt", i1, i2) not in done and c[i] < trough and c[i - 1] >= trough:
                    done.add(("dt", i1, i2))
                    ev.append(dict(p="double_top", i=i, dir=-1, stop=max(p1, p2) + 0.2 * a[i], target=trough - (max(p1, p2) - trough),
                                   label="Doppeltop", draw=[{"type": "mark", "i": i1, "y": p1, "text": "T1"}, {"type": "mark", "i": i2, "y": p2, "text": "T2"},
                                                            {"type": "line", "p": [[i1, trough], [i, trough]], "kind": "neck"}]))
        if len(L) >= 2:
            (i1, _, p1), (i2, _, p2) = L[-2], L[-1]
            if abs(p1 / p2 - 1) <= 0.02 and 10 <= i2 - i1 <= 80:
                peak = h[i1:i2 + 1].max()
                if peak >= max(p1, p2) * 1.03 and ("db", i1, i2) not in done and c[i] > peak and c[i - 1] <= peak:
                    done.add(("db", i1, i2))
                    ev.append(dict(p="double_bottom", i=i, dir=1, stop=min(p1, p2) - 0.2 * a[i], target=peak + (peak - min(p1, p2)),
                                   label="Doppelboden", draw=[{"type": "mark", "i": i1, "y": p1, "text": "B1"}, {"type": "mark", "i": i2, "y": p2, "text": "B2"},
                                                              {"type": "line", "p": [[i1, peak], [i, peak]], "kind": "neck"}]))
        # Kopf-Schulter: drei Hochs, Mitte höchste, Schultern ±4 %; Nackenlinie durch die zwei Tiefs dazwischen
        if len(H) >= 3:
            (a1, _, s1), (a2, _, hd), (a3, _, s2) = H[-3], H[-2], H[-1]
            if hd > max(s1, s2) * 1.02 and abs(s1 / s2 - 1) <= 0.04 and a3 - a1 <= 120:
                t1, t2 = a1 + int(np.argmin(l[a1:a2 + 1])), a2 + int(np.argmin(l[a2:a3 + 1]))
                f, _ = _line((t1, l[t1]), (t2, l[t2]))
                if ("hs", a1, a3) not in done and c[i] < f(i) and c[i - 1] >= f(i - 1) and i - a3 <= 30:
                    done.add(("hs", a1, a3))
                    ev.append(dict(p="head_shoulders", i=i, dir=-1, stop=s2 + 0.2 * a[i], target=f(i) - (hd - f(a2)),
                                   label="Kopf-Schulter", draw=[{"type": "mark", "i": a1, "y": s1, "text": "S"}, {"type": "mark", "i": a2, "y": hd, "text": "K"},
                                                                {"type": "mark", "i": a3, "y": s2, "text": "S"}, {"type": "line", "p": [[t1, l[t1]], [i, f(i)]], "kind": "neck"}]))
        if len(L) >= 3:
            (a1, _, s1), (a2, _, hd), (a3, _, s2) = L[-3], L[-2], L[-1]
            if hd < min(s1, s2) * 0.98 and abs(s1 / s2 - 1) <= 0.04 and a3 - a1 <= 120:
                t1, t2 = a1 + int(np.argmax(h[a1:a2 + 1])), a2 + int(np.argmax(h[a2:a3 + 1]))
                f, _ = _line((t1, h[t1]), (t2, h[t2]))
                if ("ihs", a1, a3) not in done and c[i] > f(i) and c[i - 1] <= f(i - 1) and i - a3 <= 30:
                    done.add(("ihs", a1, a3))
                    ev.append(dict(p="inv_head_shoulders", i=i, dir=1, stop=s2 - 0.2 * a[i], target=f(i) + (f(a2) - hd),
                                   label="Inverse Kopf-Schulter", draw=[{"type": "mark", "i": a1, "y": s1, "text": "S"}, {"type": "mark", "i": a2, "y": hd, "text": "K"},
                                                                        {"type": "mark", "i": a3, "y": s2, "text": "S"}, {"type": "line", "p": [[t1, h[t1]], [i, f(i)]], "kind": "neck"}]))
        # Dreiecke aus den letzten zwei Hochs/Tiefs (innerhalb 60 Kerzen)
        if len(H) >= 2 and len(L) >= 2 and i - min(H[-2][0], L[-2][0]) <= 60:
            (h1i, _, h1), (h2i, _, h2) = H[-2], H[-1]
            (l1i, _, l1), (l2i, _, l2) = L[-2], L[-1]
            fh, mh = _line((h1i, h1), (h2i, h2))
            fl, ml = _line((l1i, l1), (l2i, l2))
            flat_top = abs(h1 / h2 - 1) <= 0.01
            flat_bot = abs(l1 / l2 - 1) <= 0.01
            kind = None
            if flat_top and l2 > l1:
                kind = "asc_triangle"
            elif flat_bot and h2 < h1:
                kind = "desc_triangle"
            elif h2 < h1 and l2 > l1:
                kind = "sym_triangle"
            key = (kind, h1i, l1i)
            if kind and key not in done and fh(i) > fl(i):
                if c[i] > fh(i) + 0.2 * a[i] and c[i - 1] <= fh(i - 1) + 0.2 * a[i - 1]:
                    done.add(key)
                    ev.append(dict(p=f"{kind}_up", i=i, dir=1, stop=fl(i), target=c[i] + (h1 - l1), label={"asc_triangle": "Aufst. Dreieck", "desc_triangle": "Abst. Dreieck", "sym_triangle": "Sym. Dreieck"}[kind] + " ↑",
                                   draw=[{"type": "line", "p": [[h1i, h1], [i, fh(i)]], "kind": "tri"}, {"type": "line", "p": [[l1i, l1], [i, fl(i)]], "kind": "tri"}]))
                elif c[i] < fl(i) - 0.2 * a[i] and c[i - 1] >= fl(i - 1) - 0.2 * a[i - 1]:
                    done.add(key)
                    ev.append(dict(p=f"{kind}_down", i=i, dir=-1, stop=fh(i), target=c[i] - (h1 - l1), label={"asc_triangle": "Aufst. Dreieck", "desc_triangle": "Abst. Dreieck", "sym_triangle": "Sym. Dreieck"}[kind] + " ↓",
                                   draw=[{"type": "line", "p": [[h1i, h1], [i, fh(i)]], "kind": "tri"}, {"type": "line", "p": [[l1i, l1], [i, fl(i)]], "kind": "tri"}]))
        # Flagge: Impuls ≥ 3 ATR in ≤ 10 Kerzen, danach 5–15 Kerzen Konsolidierung mit Rücklauf ≤ 50 %, Ausbruch
        for L_ in range(5, 16):
            s = i - L_
            if s - 10 < 0:
                break
            base_lo, base_hi = l[s - 10:s + 1].min(), h[s - 10:s + 1].max()
            cons_hi, cons_lo = h[s + 1:i].max() if i > s + 1 else h[s], l[s + 1:i].min() if i > s + 1 else l[s]
            imp_up = c[s] - l[s - 10:s].min()
            imp_dn = h[s - 10:s].max() - c[s]
            if imp_up >= 3 * a[s] and cons_lo >= c[s] - 0.5 * imp_up and cons_hi <= h[s] + 0.2 * a[s] and c[i] > cons_hi and c[i - 1] <= cons_hi \
                    and ("bf", s) not in done:
                done.add(("bf", s))
                ev.append(dict(p="bull_flag", i=i, dir=1, stop=cons_lo, target=c[i] + imp_up, label="Bullische Flagge",
                               draw=[{"type": "zone", "i": s, "lo": float(cons_lo), "hi": float(cons_hi), "kind": "flag"}]))
                break
            if imp_dn >= 3 * a[s] and cons_hi <= c[s] + 0.5 * imp_dn and cons_lo >= l[s] - 0.2 * a[s] and c[i] < cons_lo and c[i - 1] >= cons_lo \
                    and ("brf", s) not in done:
                done.add(("brf", s))
                ev.append(dict(p="bear_flag", i=i, dir=-1, stop=cons_hi, target=c[i] - imp_dn, label="Bärische Flagge",
                               draw=[{"type": "zone", "i": s, "lo": float(cons_lo), "hi": float(cons_hi), "kind": "flag"}]))
                break
    return ev


# ------------------------------------------------------------------ Ausbrüche
def breakouts(o, h, l, c, a):
    ev = []
    n = len(c)
    hh = pd.Series(h).rolling(252, min_periods=200).max().shift(1).values
    d20h = pd.Series(h).rolling(20).max().shift(1).values
    d20l = pd.Series(l).rolling(20).min().shift(1).values
    rng20 = (d20h - d20l) / np.maximum(a, 1e-9)
    for i in range(253, n):
        if c[i] > hh[i] and c[i - 1] <= hh[i - 1]:
            ev.append(dict(p="high52_breakout", i=i, dir=1, stop=c[i] - 2 * a[i], label="52-Wochen-Hoch"))
        if rng20[i] < 4 and c[i] > d20h[i] and c[i - 1] <= d20h[i - 1]:
            ev.append(dict(p="range_breakout_up", i=i, dir=1, stop=d20l[i], target=c[i] + (d20h[i] - d20l[i]), label="Range-Ausbruch ↑",
                           draw=[{"type": "zone", "i": i - 20, "lo": float(d20l[i]), "hi": float(d20h[i]), "kind": "range"}]))
        if rng20[i] < 4 and c[i] < d20l[i] and c[i - 1] >= d20l[i - 1]:
            ev.append(dict(p="range_breakout_down", i=i, dir=-1, stop=d20h[i], target=c[i] - (d20h[i] - d20l[i]), label="Range-Ausbruch ↓",
                           draw=[{"type": "zone", "i": i - 20, "lo": float(d20l[i]), "hi": float(d20h[i]), "kind": "range"}]))
    return ev


def nr7(o, h, l, c, a):
    """NR7-Ausbruch (Crabel 1990): Vortag hat die engste Tagesspanne der letzten 7 Tage; heutiger Schluss über dessen
    Hoch → long (Stop = NR7-Tief), unter dessen Tief → short (Stop = NR7-Hoch). Signal erst am Schluss des Ausbruchstags."""
    ev = []
    rng = h - l
    for i in range(8, len(c)):
        j = i - 1
        if not (rng[j] > 0 and rng[j] <= rng[j - 6:j + 1].min()):
            continue
        if c[i] > h[j] and c[i - 1] <= h[j]:
            ev.append(dict(p="nr7_up", i=i, dir=1, stop=l[j], label="NR7-Ausbruch ↑"))
        elif c[i] < l[j] and c[i - 1] >= l[j]:
            ev.append(dict(p="nr7_down", i=i, dir=-1, stop=h[j], label="NR7-Ausbruch ↓"))
    return ev


DETECTORS = ["candles", "gaps", "sweeps", "zones", "trendlines", "formations", "breakouts", "nr7"]


def detect(px, k=5):
    """Alle Muster eines Tickers. Rückgabe: (Ereignisliste, Kontext für den Chart am letzten Tag)."""
    o, h, l, c = (px[x].values.astype(float) for x in ("Open", "High", "Low", "Close"))
    a = atr(h, l, c)
    ph, pl = pivots(h, l, k)
    ev = []
    ev += candles(o, h, l, c, a)
    ev += gaps(o, h, l, c, a)
    ev += sweeps(o, h, l, c, a, ph, pl)
    zev, active = zones(o, h, l, c, a)
    ev += zev
    ev += trendlines(o, h, l, c, a, ph, pl)
    ev += formations(o, h, l, c, a, ph, pl)
    ev += breakouts(o, h, l, c, a)
    ev += nr7(o, h, l, c, a)
    n = len(c)
    ctx = {"trendlines": current_trendlines(h, l, c, a), "gaps": open_gaps(h, l, a),
           "zones": [{"type": "zone", "i": z["base"], "lo": float(z["lo"]), "hi": float(z["hi"]), "kind": z["kind"]}
                     for z in active if n - 1 - z["born"] <= 120][-8:]}
    return ev, ctx, (o, h, l, c, a)


def simulate(ev, ohlca, max_days=20, cost=0.001):
    """Handelt jedes Ereignis: Einstieg Schlusskurs, Stop = Invalidierung (0,5–4 ATR begrenzt), Ziel = Kursziel
    (sonst 2R), Ausstieg spätestens nach max_days. Stop vor Ziel bei gleicher Kerze. Ergebnis in R und in %."""
    o, h, l, c, a = ohlca
    n = len(c)
    rows = []
    for e in ev:
        i, d = e["i"], e["dir"]
        if i >= n - 1:
            continue
        entry = c[i]
        risk = abs(entry - e["stop"]) if e.get("stop") is not None else 2 * a[i]
        risk = min(max(risk, 0.5 * a[i]), 4 * a[i])
        stop = entry - d * risk
        tgt = e.get("target")
        if tgt is None or d * (tgt - entry) <= 0.5 * risk:
            tgt = entry + d * 2 * risk
        out, reason = None, None
        for j in range(i + 1, min(n, i + 1 + max_days)):
            lo, hi = (l[j], h[j]) if d > 0 else (h[j], l[j])
            if d * (lo - stop) <= 0:
                out, reason = stop, "Stop"
                break
            if d * (hi - tgt) >= 0:
                out, reason = tgt, "Ziel"
                break
        if out is None:
            j = min(n - 1, i + max_days)
            if j - i < max_days and j == n - 1:
                continue  # noch offen
            out, reason = c[j], "Zeit"
        ret = d * (out / entry - 1) - cost
        fwd10 = d * (c[min(n - 1, i + 10)] / entry - 1) if i + 10 < n else np.nan
        rows.append({"i": i, "p": e["p"], "dir": d, "R": ret * entry / risk, "ret": ret, "rf": risk / entry,
                     "fwd10": fwd10, "reason": reason, "hold": j - i})
    return rows


def baseline(ohlca, step=5, max_days=20, cost=0.001):
    """Vergleichsmaßstab: Einstieg an jedem 5. Tag, long und short, Stop 1,5 ATR, Ziel 2R – gleiche Mechanik ohne Muster."""
    o, h, l, c, a = ohlca
    ev = []
    for i in range(30, len(c) - 1, step):
        ev.append(dict(p="base_long", i=i, dir=1, stop=c[i] - 1.5 * a[i]))
        ev.append(dict(p="base_short", i=i, dir=-1, stop=c[i] + 1.5 * a[i]))
    return simulate(ev, ohlca, max_days, cost)
