#!/bin/bash
# TokenCup server calls. Source config from arena.env in the same directory.
#   tc.sh --state          show current game state
#   tc.sh --pgn            show the PGN
#   tc.sh <move>           submit a move (SAN or UCI)
#   tc.sh --resign <side>  end the game, <side> is white|black (used for forfeits)
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/arena.env"

case "$1" in
  --state)
    curl -s -o /tmp/tc.json "$TC_URL/games/$TC_GAME"
    python3 "$D/report.py" state ;;
  --pgn)
    curl -s "$TC_URL/games/$TC_GAME" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pgn"])' ;;
  --resign)
    curl -s -o /tmp/tc.json -X POST "$TC_URL/games/$TC_GAME/resign" \
      -H 'Content-Type: application/json' \
      --data-binary "{\"player\":\"$2\",\"termination\":\"forfeit\"}"
    python3 "$D/report.py" state ;;
  *)
    code=$(curl -s -o /tmp/tc.json -w '%{http_code}' -X POST "$TC_URL/games/$TC_GAME/moves" \
      -H 'Content-Type: application/json' \
      --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"move": sys.argv[1]}))' "$1")")
    python3 "$D/report.py" "$code" ;;
esac
