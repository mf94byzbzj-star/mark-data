"""Black-Scholes, Greeks und Strike-für-Delta auf einem Smile (vektorisiert, nur numpy/scipy)."""
import numpy as np
from scipy.stats import norm

R = 0.04


def _d1(S, K, T, sig, r=R, q=0.0):
    T = np.maximum(T, 1e-6)
    sig = np.maximum(sig, 1e-4)
    return (np.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * np.sqrt(T))


def price(S, K, T, sig, cp, r=R, q=0.0):
    """cp: +1 Call, -1 Put. T in Jahren. T<=0 -> innerer Wert."""
    S, K, T, sig, cp = map(np.asarray, (S, K, T, sig, cp))
    intrinsic = np.maximum(cp * (S - K), 0.0)
    d1 = _d1(S, K, T, sig, r, q)
    d2 = d1 - sig * np.sqrt(np.maximum(T, 1e-6))
    v = cp * (S * np.exp(-q * T) * norm.cdf(cp * d1) - K * np.exp(-r * T) * norm.cdf(cp * d2))
    return np.where(T <= 1e-6, intrinsic, np.maximum(v, intrinsic * 0 + 0.0))


def delta(S, K, T, sig, cp, r=R, q=0.0):
    d1 = _d1(S, K, T, sig, r, q)
    return np.where(np.asarray(cp) > 0, norm.cdf(d1), norm.cdf(d1) - 1.0)


def gamma(S, K, T, sig, r=R, q=0.0):
    T = np.maximum(T, 1e-6)
    d1 = _d1(S, K, T, sig, r, q)
    return norm.pdf(d1) / (S * np.maximum(sig, 1e-4) * np.sqrt(T))


def vega(S, K, T, sig, r=R, q=0.0):
    T = np.maximum(T, 1e-6)
    return S * norm.pdf(_d1(S, K, T, sig, r, q)) * np.sqrt(T) / 100.0


def theta(S, K, T, sig, cp, r=R):
    T = np.maximum(T, 1e-6)
    d1 = _d1(S, K, T, sig, r)
    d2 = d1 - sig * np.sqrt(T)
    a = -S * norm.pdf(d1) * sig / (2 * np.sqrt(T))
    b = cp * r * K * np.exp(-r * T) * norm.cdf(cp * d2)
    return (a - b) / 365.0


def strike_from_delta(S, T, sig, d, r=R):
    """Strike zu einem Call-Delta d (0..1) bei Vola sig (Put-Delta p -> d = 1+p)."""
    T = max(T, 1e-6)
    return S * np.exp(-norm.ppf(np.clip(d, 1e-4, 1 - 1e-4)) * sig * np.sqrt(T) + (r + 0.5 * sig * sig) * T)


class Smile:
    """IV als Funktion des Strikes, aus Stützstellen (Delta-Punkte) in Log-Moneyness interpoliert."""

    def __init__(self, S, T, points):
        # points: Liste (call_delta_equiv, iv), z.B. Put-25-Delta -> 0.75
        self.S, self.T = S, max(T, 1 / 365)
        xs, ys = [], []
        for d, iv in points:
            if iv is None or not np.isfinite(iv) or iv <= 0.01 or iv > 4:
                continue
            k = strike_from_delta(S, self.T, iv, d)
            xs.append(np.log(k / S))
            ys.append(iv)
        o = np.argsort(xs)
        self.x = np.asarray(xs)[o]
        self.y = np.asarray(ys)[o]
        self.ok = len(self.x) >= 1

    def iv(self, K):
        if len(self.x) == 1:
            return np.full_like(np.asarray(K, float), self.y[0])
        return np.interp(np.log(np.asarray(K, float) / self.S), self.x, self.y)

    def strike_for_delta(self, target, cp):
        """target: Delta mit Vorzeichen (Put negativ). Bisektion über den Strike."""
        lo, hi = self.S * 0.2, self.S * 3.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            dl = float(delta(self.S, mid, self.T, float(self.iv(mid)), cp))
            # Delta fällt mit steigendem Strike (Calls und Puts)
            if dl > target:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
