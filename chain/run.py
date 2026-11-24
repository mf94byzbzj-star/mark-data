#!/usr/bin/env python3
"""Agentenkette – kompletter Rechenlauf (Stufen 1–6).

  python -m chain.run            alle Stufen
  python -m chain.run a3 a4      nur bestimmte Stufen (nutzt vorhandene Ergebnisse der übrigen)
  python -m chain.run --refresh  Merkmals- und Backtest-Cache neu aufbauen
"""
import sys
import time
import traceback

STAGES = ["l0", "a1", "a2", "a10", "a11", "a9", "a3", "a8", "a4", "a5", "a7", "a0", "a6"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--refresh" in sys.argv:
        from chain.stages import common
        common.all_features(refresh=True)
        common.all_trades(refresh=True)
    todo = args or STAGES
    from chain.stages import a0_audit, a1_triggers, a2_tickers, a3_live, a4_price, a5_macro, a6_dashboard, a7_positions, a8_patterns, a9_events, a10_social, a11_filings, a0_learn
    mods = {"a0": a0_audit, "a1": a1_triggers, "a2": a2_tickers, "a3": a3_live, "a4": a4_price, "a5": a5_macro, "a6": a6_dashboard, "a7": a7_positions, "a8": a8_patterns, "a9": a9_events, "a10": a10_social, "a11": a11_filings, "l0": a0_learn}
    ok = True
    for s in todo:
        t0 = time.time()
        try:
            mods[s].run()
            print(f"[{s}] ok ({time.time()-t0:.0f}s)", flush=True)
        except Exception:
            ok = False
            print(f"[{s}] FEHLER\n{traceback.format_exc()}", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
