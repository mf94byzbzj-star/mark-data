"""Prüft eine Entscheidungsdatei des Claude-Entscheidungslaufs vor dem Commit: python -m arena.validate <Tag>
Meldet Formfehler, unbekannte Netze/Kandidaten, Stops auf der falschen Seite, Risiko > 10 %, fehlende Begründungen."""
import json
import os
import sys

from arena import engine as E


def main(day):
    dec = E.jl(os.path.join(E.DEC, f"{day}.json"), None)
    prop = E.jl(os.path.join(E.PROP, f"{day}.json"), None)
    if dec is None or prop is None:
        print("Entscheidungen oder Vorschläge fehlen für", day)
        return 1
    cids = {c["cid"] for c in prop["candidates"]}
    st = E.jl(os.path.join(E.A, "state.json"), {})
    err = 0
    for nid, d in dec.items():
        n = st["nets"].get(nid)
        if not n:
            print("✗", nid, "unbekanntes Netz"); err += 1; continue
        if n["kind"] in E.CONTROLS:
            continue
        if not d.get("why"):
            print("✗", nid, "Begründung (why) fehlt"); err += 1
        for o in d.get("orders", []):
            t = o.get("type")
            if t not in ("option", "equity", "add", "modify", "close"):
                print("✗", nid, "unbekannter Order-Typ", t); err += 1; continue
            if float(o.get("risk", 0.0) or 0) > E.MAX_RISK:
                print("✗", nid, "Risiko > 10 %", o); err += 1
            if t == "option" and o.get("cid") not in cids:
                print("✗", nid, "Kandidat unbekannt", o.get("cid")); err += 1
            if t == "equity":
                px = E.last_close(o["ticker"], E.dt.date.fromisoformat(day))
                sgn = 1 if o.get("direction") == "long" else -1
                if not px or (px - float(o["stop"])) * sgn <= 0:
                    print("✗", nid, "Stop auf der falschen Seite", o); err += 1
                if "max_days" not in o:
                    print("✗", nid, "Haltedauer max_days fehlt", o["ticker"]); err += 1
    pending = [i for i, x in prop["nets"].items() if i not in dec and not x.get("mutation_pending")]
    if pending:
        print("Hinweis: keine Entscheidung für", ", ".join(pending), "(= bewusst nichts tun?)")
    print("OK" if not err else f"{err} Fehler")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
