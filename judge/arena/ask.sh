#!/bin/bash
# Prompt a player agent in a Herdr pane and extract the move from its reply.
# usage: ask.sh <pane-id> <message>
# Prints the move on stdout, or nothing if no move could be found.
PANE="$1"; MSG="$2"

status_of() {
  herdr agent get "$PANE" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["agent"]["agent_status"])' 2>/dev/null
}

# Last line in the pane that is *entirely* a move. Whole-line matching matters:
# our own prompt echoes a move list, but that is many moves on one line, so it
# cannot match. Box-drawing and bullet glyphs are stripped first.
last_move() {
  herdr agent read "$PANE" --source recent-unwrapped --lines 60 2>/dev/null \
    | sed 's/[│┃|]//g; s/[█▄▀●❯]//g' \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
    | grep -Ex '([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?[+#]?|O-O(-O)?[+#]?|0-0(-0)?[+#]?)' \
    | tail -1
}

BEFORE=$(last_move)

if herdr agent prompt "$PANE" "$MSG" --wait --timeout 300000 >/dev/null 2>&1; then
  # --wait already blocked until the agent settled, so it is idle *now*. Polling
  # for `working` here would never match and would just burn its whole timeout
  # on every ply -- the single biggest source of dead time in a match.
  SUBMITTED_BY_HAND=0
else
  # Some harnesses (GitHub Copilot CLI) never submit the prompt on their own,
  # which surfaces as agent_prompt_stalled. Press Enter for them.
  herdr agent send-keys "$PANE" enter >/dev/null 2>&1
  SUBMITTED_BY_HAND=1
fi

if [ "$SUBMITTED_BY_HAND" = 1 ]; then
  # Only on the hand-submitted path do we have to wait for the work to start,
  # then to settle. Reading a working pane fails outright, since alternate-screen
  # history is only capturable while idle.
  for _ in $(seq 1 15); do
    [ "$(status_of)" = "working" ] && break
    sleep 1
  done
  for _ in $(seq 1 90); do
    case "$(status_of)" in
      idle|done|blocked) break ;;
    esac
    sleep 2
  done
fi

# Phase 3: the reply must be NEW. Herdr cannot always tell that a given harness
# is working, so the settle poll above may fall straight through while the agent
# is still thinking, leaving the previous answer as the last move-line.
MOVE=$(last_move)
for _ in $(seq 1 20); do
  [ -n "$MOVE" ] && [ "$MOVE" != "$BEFORE" ] && break
  sleep 3
  MOVE=$(last_move)
done

# Still unchanged after a minute: treat it as no answer, not as a move. Printing
# the stale line would submit a move the player never made this turn -- which
# then gets rejected as illegal and wastes one of its three chances. Returning
# nothing makes the caller re-prompt instead.
if [ -n "$BEFORE" ] && [ "$MOVE" = "$BEFORE" ]; then
  exit 0
fi

printf '%s\n' "$MOVE"
