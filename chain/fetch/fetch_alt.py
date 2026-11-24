#!/usr/bin/env python3
"""Alternative Daten für Agent 10 (Google Trends) und Agent 11 (SEC-Filings). Läuft in GitHub Actions.

Modi:
  insider   SEC Form 3/4/5: Historie aus den vierteljährlichen „Insider Transactions Data Sets“ (ab 2012, nur fehlende
            Quartale) + aktuelle Form-4-Meldungen der Universums-Ticker über data.sec.gov (seit dem letzten Lauf)
  trends    Google Trends: wöchentlicher Suchindex „<TICKER> stock“ (5 Jahre) je Ticker, mit Pausen gegen Sperren
Ablage:
  chain_data/alt/insider/<T>.csv.gz   filed, trade_date, acc, owner_cik, owner, director, officer, ten_pct, title,
                                      code, ad, shares, price, value, plan
  chain_data/alt/trends/<T>.csv       week, svi
  chain_data/alt/_state.json          erledigte Quartale, letzter Abruf je CIK
SEC verlangt einen User-Agent mit Kontakt und max. 10 Anfragen/s.
"""
import datetime as dt
import io
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import pandas as pd
import requests
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = os.path.join(ROOT, "chain_data")
A = os.path.join(D, "alt")
CFG = yaml.safe_load(open(os.path.join(ROOT, "chain", "config.yaml")))
STOCKS = CFG["universe"]["stocks"]
UNIV = CFG["universe"]["etfs"] + STOCKS
SEC_UA = {"User-Agent": "Mark-Agentenkette research mf94byzbzj-star@users.noreply.github.com", "Accept-Encoding": "gzip, deflate"}
MODE = sys.argv[1] if len(sys.argv) > 1 else "insider"
T0 = time.time()
BUDGET = float(os.environ.get("ALT_BUDGET_MIN", "50")) * 60
LOG = []
COLS = ["filed", "trade_date", "acc", "owner_cik", "owner", "director", "officer", "ten_pct", "title", "code", "ad",
        "shares", "price", "value", "plan"]


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


def state():
    p = os.path.join(A, "_state.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def save_state(s):
    os.makedirs(A, exist_ok=True)
    json.dump(s, open(os.path.join(A, "_state.json"), "w"), indent=1, sort_keys=True)


def commit(msg):
    os.makedirs(os.path.join(D, "logs"), exist_ok=True)
    open(os.path.join(D, "logs", f"fetch_alt_{MODE}.log"), "w").write("\n".join(LOG[-400:]))
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    subprocess.run("git add chain_data", shell=True)
    if subprocess.run("git diff --cached --quiet", shell=True).returncode == 0:
        return
    subprocess.run(f'git commit -q -m "{msg}"', shell=True)
    for _ in range(3):
        if subprocess.run("git pull --rebase -q -X theirs && git push -q", shell=True).returncode == 0:
            return
        time.sleep(5)


_last = [0.0]
BLOCKED = [0]   # SEC verweigert den Zugriff (403) → nicht weiter anfragen, Fair-Access-Regeln respektieren
SEC_UA["User-Agent"] = " ".join((os.environ.get("SEC_USER_AGENT") or SEC_UA["User-Agent"]).split())   # Zeilenumbrüche entfernen


def sec_get(url, **kw):
    if BLOCKED[0] >= 2:
        return None
    for k in range(4):
        wait = 0.15 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(url, headers=SEC_UA, timeout=60, **kw)
            if r.status_code == 200:
                return r
            if r.status_code == 403:
                BLOCKED[0] += 1
                log("SEC 403 (Zugriff verweigert)", url[:90])
                return None
            if r.status_code in (429, 503):
                log("SEC", r.status_code, url[:90])
                time.sleep(5 * (k + 1))
                continue
            log("SEC", r.status_code, url[:90])
            return None
        except Exception as e:
            log("SEC-Fehler", url, type(e).__name__)   # keine Fehlertexte loggen (könnten Header enthalten)
            time.sleep(3)
    return None


def merge(t, df):
    os.makedirs(os.path.join(A, "insider"), exist_ok=True)
    p = os.path.join(A, "insider", f"{t}.csv.gz")
    if os.path.exists(p):
        df = pd.concat([pd.read_csv(p, dtype={"acc": str, "owner_cik": str}), df], ignore_index=True)
    df = df.drop_duplicates(["acc", "owner_cik", "trade_date", "code", "shares", "price"]).sort_values(["filed", "trade_date"])
    df[COLS].to_csv(p, index=False, compression="gzip")


# ---------------------------------------------------------------- Insider: Historie (Quartals-Datensätze)
def quarters(start=2012):
    today = dt.date.today()
    out = []
    for y in range(start, today.year + 1):
        for q in range(1, 5):
            if dt.date(y, 3 * q, 1) + dt.timedelta(days=45) < today:   # Datensatz erscheint nach Quartalsende
                out.append(f"{y}q{q}")
    return out


def tsv(z, name):
    n = [x for x in z.namelist() if x.upper().endswith(name)]
    if not n:
        return None
    df = pd.read_csv(z.open(n[0]), sep="\t", dtype=str, low_memory=False, on_bad_lines="skip")
    df.columns = [c.upper() for c in df.columns]
    return df


def rel_flags(s):
    s = str(s or "").lower()
    return int("director" in s), int("officer" in s), int("tenpercent" in s.replace(" ", "") or "10%" in s)


def backfill_insider(st):
    done = set(st.get("insider_quarters", []))
    univ = set(STOCKS)
    for q in sorted(set(quarters()) - done, reverse=True):
        if time.time() - T0 > BUDGET:
            log("Zeitbudget erreicht – Fortsetzung beim nächsten Lauf")
            break
        url = f"https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
        r = sec_get(url)
        if r is None:
            log("Quartal nicht abrufbar:", q)
            continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        sub, own, nd = tsv(z, "SUBMISSION.TSV"), tsv(z, "REPORTINGOWNER.TSV"), tsv(z, "NONDERIV_TRANS.TSV")
        if sub is None or own is None or nd is None:
            log("Quartal unvollständig:", q, z.namelist())
            continue
        sub["T"] = sub["ISSUERTRADINGSYMBOL"].str.upper().str.strip()
        sub = sub[sub["T"].isin(univ) & sub["DOCUMENT_TYPE"].isin(["4", "4/A"])]
        own = own.drop_duplicates("ACCESSION_NUMBER")
        m = nd.merge(sub, on="ACCESSION_NUMBER").merge(own, on="ACCESSION_NUMBER", how="left")
        if m.empty:
            st.setdefault("insider_quarters", []).append(q)
            continue
        def col(name):
            return m[name] if name in m.columns else pd.Series([""] * len(m), index=m.index)
        fl = col("RPTOWNER_RELATIONSHIP").map(rel_flags)
        out = pd.DataFrame({
            "filed": pd.to_datetime(m["FILING_DATE"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d"),
            "trade_date": pd.to_datetime(m["TRANS_DATE"], format="mixed", errors="coerce").dt.strftime("%Y-%m-%d"),
            "acc": m["ACCESSION_NUMBER"], "owner_cik": col("RPTOWNERCIK"), "owner": col("RPTOWNERNAME"),
            "director": [f[0] for f in fl], "officer": [f[1] for f in fl], "ten_pct": [f[2] for f in fl],
            "title": col("RPTOWNER_TITLE"), "code": m["TRANS_CODE"], "ad": col("TRANS_ACQUIRED_DISP_CD"),
            "shares": pd.to_numeric(m["TRANS_SHARES"], errors="coerce"),
            "price": pd.to_numeric(col("TRANS_PRICEPERSHARE"), errors="coerce"),
            "plan": col("AFF10B5ONE").fillna("").astype(str).str.lower().isin(["1", "true", "y"]).astype(int)})
        out["value"] = (out["shares"] * out["price"]).round(0)
        for t, g in out.groupby(m["T"].values):
            merge(t, g)
        st.setdefault("insider_quarters", []).append(q)
        save_state(st)
        log(q, len(out), "Transaktionen,", m["T"].nunique(), "Ticker")
        commit(f"Daten: Insider {q}")


# ---------------------------------------------------------------- Insider: aktuelle Form 4 (data.sec.gov)
def cik_map():
    r = sec_get("https://www.sec.gov/files/company_tickers.json")
    if r is None:
        log("CIK-Liste nicht abrufbar")
        return {}
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in r.json().values()}


def txt(e, path):
    x = e.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_form4(xml, filed, acc):
    root = ET.fromstring(xml)
    ro = root.find("reportingOwner")
    rel = ro.find("reportingOwnerRelationship") if ro is not None else None
    base = {"filed": filed, "acc": acc,
            "owner_cik": txt(ro, "reportingOwnerId/rptOwnerCik") if ro is not None else "",
            "owner": txt(ro, "reportingOwnerId/rptOwnerName") if ro is not None else "",
            "director": int(txt(rel, "isDirector") in ("1", "true")) if rel is not None else 0,
            "officer": int(txt(rel, "isOfficer") in ("1", "true")) if rel is not None else 0,
            "ten_pct": int(txt(rel, "isTenPercentOwner") in ("1", "true")) if rel is not None else 0,
            "title": txt(rel, "officerTitle") if rel is not None else "",
            "plan": int(txt(root, "aff10b5One") in ("1", "true"))}
    rows = []
    for tr in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        sh = pd.to_numeric(txt(tr, "transactionAmounts/transactionShares/value"), errors="coerce")
        px = pd.to_numeric(txt(tr, "transactionAmounts/transactionPricePerShare/value"), errors="coerce")
        rows.append({**base, "trade_date": txt(tr, "transactionDate/value")[:10], "code": txt(tr, "transactionCoding/transactionCode"),
                     "ad": txt(tr, "transactionAmounts/transactionAcquiredDisposedCode/value"), "shares": sh, "price": px,
                     "value": round(sh * px, 0) if pd.notna(sh) and pd.notna(px) else None})
    return rows


def recent_insider(st):
    cm = cik_map()
    seen = st.setdefault("insider_recent", {})
    since_default = (dt.date.today() - dt.timedelta(days=150)).isoformat()
    n_new = 0
    for t in STOCKS:
        if time.time() - T0 > BUDGET:
            break
        cik = cm.get(t.replace(".", "-")) or cm.get(t)
        if not cik:
            continue
        r = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if r is None:
            continue
        rec = r.json().get("filings", {}).get("recent", {})
        since = seen.get(t, since_default)
        rows = []
        for form, acc, fd, doc in zip(rec.get("form", []), rec.get("accessionNumber", []), rec.get("filingDate", []),
                                      rec.get("primaryDocument", [])):
            if form not in ("4", "4/A") or fd <= since:
                continue
            raw = doc.split("/")[-1]
            x = sec_get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{raw}")
            if x is None:
                continue
            try:
                rows += parse_form4(x.content, fd, acc)
            except Exception as e:
                log("Form-4-Parsefehler", t, acc, e)
        if rows:
            merge(t, pd.DataFrame(rows))
            n_new += len(rows)
        dates = [fd for form, fd in zip(rec.get("form", []), rec.get("filingDate", [])) if form in ("4", "4/A")]
        if dates:
            seen[t] = max(dates + [since])
    save_state(st)
    log("Neue Form-4-Transaktionen:", n_new)


# ---------------------------------------------------------------- Google Trends
def trends(st):
    from pytrends.request import TrendReq
    os.makedirs(os.path.join(A, "trends"), exist_ok=True)
    done = st.setdefault("trends_done", {})
    today = dt.date.today().isoformat()
    py = TrendReq(hl="en-US", tz=300, timeout=(10, 30))
    order = sorted(STOCKS, key=lambda t: done.get(t, ""))   # am längsten nicht aktualisierte zuerst
    n_ok, fails = 0, 0
    for t in order:
        if time.time() - T0 > BUDGET or fails >= 6:
            break
        if done.get(t) == today:
            continue
        kw = f"{t} stock"
        try:
            py.build_payload([kw], timeframe="today 5-y", geo="US")
            df = py.interest_over_time()
            if df is None or df.empty:
                raise RuntimeError("leer")
            out = pd.DataFrame({"week": df.index.strftime("%Y-%m-%d"), "svi": df[kw].values})
            p = os.path.join(A, "trends", f"{t}.csv")
            if os.path.exists(p):   # alte Wochen behalten (Google skaliert je Abruf neu → nur jüngster Abruf zählt)
                pass
            out.to_csv(p, index=False)
            done[t] = today
            n_ok += 1
            fails = 0
            if n_ok % 10 == 0:
                save_state(st)
                commit(f"Daten: Google Trends ({n_ok})")
            time.sleep(8)
        except Exception as e:
            fails += 1
            log("Trends-Fehler", t, str(e)[:120])
            save_state(st)
            commit("Daten: Google Trends (Fehlerprotokoll)")
            time.sleep(30 * fails)
    save_state(st)
    log("Trends aktualisiert:", n_ok, "Ticker")


if __name__ == "__main__":
    st = state()
    if MODE == "insider":
        recent_insider(st)
        commit("Daten: Insider aktuell")
        backfill_insider(st)
    elif MODE == "trends":
        trends(st)
    save_state(st)
    commit(f"Daten: {MODE}")
