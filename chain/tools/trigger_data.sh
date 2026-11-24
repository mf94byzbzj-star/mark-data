#!/bin/bash
# Startet Daten-Workflows per Marker-Push (GitHub-Zeitpläne feuern in diesem Repo nicht) und wartet auf ihr Ende.
# Nutzung: bash chain/tools/trigger_data.sh daily [backfill] [alt] [intraday]   (Wartezeit max. WAIT_MIN, Standard 25)
cd "$(dirname "$0")/../.." || exit 1
API=https://api.github.com/repos/mf94byzbzj-star/Mark
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for k in "$@"; do date -u > "chain_data/.run_$k"; git add "chain_data/.run_$k"; done
git commit -qm "Daten: Workflows starten ($*)" && (git pull -q --rebase || true) && git push -q
declare -A NAME=([daily]="chain-daily.yml" [backfill]="chain-dolt.yml" [alt]="chain-alt.yml" [intraday]="chain-intraday.yml")
WAIT=$(( ${WAIT_MIN:-25} * 60 )); T0=$(date +%s)
for k in "$@"; do
  [ "$k" = "backfill" ] || [ "$k" = "alt" ] && continue   # lange Läufe nicht abwarten
  while :; do
    st=$(curl -s -m 30 "$API/actions/workflows/${NAME[$k]}/runs?per_page=3&created=>=$START" | python3 -c "
import sys,json
r=json.load(sys.stdin).get('workflow_runs',[])
print(r[0]['status']+' '+str(r[0]['conclusion']) if r else 'none')" 2>/dev/null)
    echo "$k: $st"
    case "$st" in completed*) break;; esac
    [ $(( $(date +%s) - T0 )) -gt $WAIT ] && { echo "$k: Zeitlimit"; break; }
    sleep 30
  done
done
git pull -q --rebase || git pull -q
