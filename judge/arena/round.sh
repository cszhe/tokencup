#!/bin/bash
# Play one full move (White, then Black).
# usage: round.sh <move-number>
# exit 0 = both plies played, 1 = game already over, 2 = a side forfeited.
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/arena.env"
N="$1"

play() {
  local pane="$1" colour="$2" state status moves
  state=$("$D/tc.sh" --state)
  status=$(printf '%s' "$state" | grep '^status:' | awk '{print $2}')
  if [ "$status" != "active" ]; then
    echo "[judge] game is over:"; printf '%s\n' "$state"; return 1
  fi
  moves=$(printf '%s' "$state" | grep '^moves:' | sed 's/^moves: //')
  # Every prompt restates colour and reply format, so it stays self-sufficient
  # if the player's own context is compacted mid-game.
  "$D/ply.sh" "$pane" "$colour" "You are playing $colour in a chess game. I am the judge.

Move list so far (SAN, in order): $moves

It is your turn as $colour, move $N. Reply with ONLY the move in algebraic notation, nothing else."
}

play "$TC_WHITE_PANE" WHITE || exit $?
echo "--- black ---"
play "$TC_BLACK_PANE" BLACK || exit $?
