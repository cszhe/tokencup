#!/bin/bash
# One ply, applying the judge's retry policy.
# usage: ply.sh <pane-id> <WHITE|BLACK> <context message>
# exit 0 = move accepted, exit 2 = player burned its retry budget (forfeit).
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/arena.env"
PANE="$1"; COLOUR="$2"; CTX="$3"; MSG="$CTX"

for attempt in $(seq 1 "${TC_RETRIES:-3}"); do
  MOVE=$("$D/ask.sh" "$PANE" "$MSG")
  if [ -z "$MOVE" ]; then
    echo "[judge] $COLOUR sent no readable move (attempt $attempt)"
    MSG="I could not find a move in your reply. Reply with ONLY the move in algebraic notation and nothing else.

$CTX"
    continue
  fi
  echo "[$COLOUR] $MOVE"
  OUT=$("$D/tc.sh" "$MOVE"); echo "$OUT"
  case "$OUT" in
    REJECTED*)
      DETAIL=$(printf '%s' "$OUT" | sed 's/^REJECTED [0-9]*: //')
      echo "[judge] rejected; telling $COLOUR (attempt $attempt)"
      MSG="Your move \"$MOVE\" was REJECTED by the server: $DETAIL

Pick a different, legal move.

$CTX" ;;
    *) exit 0 ;;
  esac
done

echo "[judge] $COLOUR exhausted its retry budget -- FORFEIT"
exit 2
