#!/bin/bash
# Play one full move (White, then Black).
# usage: round.sh <move-number>
# exit 0 = both plies played, 1 = game already over, 2 = a side forfeited,
# 3 = ply.sh flagged suspected engine/command use -- go review the pane
# before ruling, do not treat this as a forfeit on its own.
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
  #
  # Kept to a SINGLE LINE on purpose: some harnesses (GitHub Copilot CLI) treat
  # Enter on multi-line input as a newline rather than a submit, so a multi-line
  # prompt silently piles up in the input box and is never sent.
  "$D/ply.sh" "$pane" "$colour" \
    "You are playing $colour in a chess game, I am the judge. Move list so far (SAN, in order): ${moves:-(none, this is the first move)} -- it is your turn as $colour, move $N. Reply with ONLY the move in algebraic notation, nothing else."
}

play "$TC_WHITE_PANE" WHITE || exit $?
echo "--- black ---"
play "$TC_BLACK_PANE" BLACK || exit $?
