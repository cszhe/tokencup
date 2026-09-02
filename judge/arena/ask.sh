#!/bin/bash
# Prompt a player agent in a Herdr pane and extract the move from its reply.
# usage: ask.sh <pane-id> <message>
# Prints the move on stdout, or nothing if no move could be found.
# Exit 99 = the pane shows signs the player ran a command or chess engine
# instead of choosing a move itself (see CHEAT_PATTERN below).
PANE="$1"; MSG="$2"

status_of() {
  herdr agent get "$PANE" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["agent"]["agent_status"])' 2>/dev/null
}

# Signatures of a player running a chess engine or shelling out, caught after a
# real match where a player launched Stockfish via python subprocess calls and
# submitted its bestmove output. This is a blocklist, not a proof of innocence:
# it catches an engine invocation left visible in the pane, not a player that
# reasons like an engine without running one, or hides the command better. It
# still needs a human judge to confirm before ruling -- see ply.sh exit 99 and
# JUDGE_AGENT.md's "Detecting engine or tool assistance" section.
CHEAT_PATTERN='stockfish|leela ?chess|\blc0\b|komodo|fairy-stockfish|\bbestmove\b|setoption name|multipv|\buciok\b|\breadyok\b|position fen|go depth [0-9]|go movetime|subprocess\.(Popen|run|call)|chess\.engine|^\$ *(python3?|node|bash|sh|ruby|perl|cargo run|go run)\b'

# One cleaned pane read, reused for both the cheat scan and the move search --
# reading twice per check would risk the two seeing different content.
read_pane() {
  herdr agent read "$PANE" --source recent-unwrapped --lines 60 2>/dev/null \
    | sed 's/[│┃|]//g; s/[█▄▀●❯]//g' \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

# $1 = cleaned pane text. Prints matching lines (for the judge's evidence) and
# returns non-zero if nothing matched.
check_cheat() {
  printf '%s\n' "$1" | grep -iE "$CHEAT_PATTERN"
}

# Long algebraic ("Nc3-e4", "e2-e4", "e7-e8=Q") is a perfectly clear way to name
# a move, but the server speaks only SAN and UCI. Rewrite it to UCI rather than
# rejecting it -- transcribing notation is not the same as choosing a different
# move, and a player that says Nc3-e4 has told us exactly what it wants to play.
to_uci() {
  python3 -c '
import re, sys
s = sys.argv[1]
m = re.fullmatch(r"([KQRBN])?([a-h][1-8])[-x]([a-h][1-8])(?:=([QRBN]))?[+#]?", s)
print(m.group(2) + m.group(3) + (m.group(4) or "").lower() if m else s)
' "$1"
}

# Last line in the pane that is *entirely* a move. Whole-line matching matters:
# our own prompt echoes a move list, but that is many moves on one line, so it
# cannot match. $1 = cleaned pane text (from read_pane).
last_move_from() {
  raw=$(printf '%s\n' "$1" \
    | grep -Ex '([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?[+#]?|[KQRBN]?[a-h][1-8][-x][a-h][1-8](=[QRBN])?[+#]?|O-O(-O)?[+#]?|0-0(-0)?[+#]?)' \
    | tail -1)
  [ -n "$raw" ] && to_uci "$raw"
}

fail_cheat() {
  echo "[ask.sh] SUSPECTED ENGINE/COMMAND USE by $PANE -- matched line(s):" >&2
  printf '%s\n' "$1" | head -5 >&2
  exit 99
}

PANE_TEXT=$(read_pane)
HIT=$(check_cheat "$PANE_TEXT") && fail_cheat "$HIT"
BEFORE=$(last_move_from "$PANE_TEXT")

# herdr writes its structured error JSON to stderr on failure (stdout is
# empty in that case), so capture stderr only -- keeps the blob clean for
# json.load below regardless of anything herdr ever prints to stdout.
RESP=$(herdr agent prompt "$PANE" "$MSG" --wait --timeout 300000 2>&1 1>/dev/null)
STATUS=$?

if [ "$STATUS" -eq 0 ]; then
  # --wait already blocked until the agent settled, so it is idle *now*. Polling
  # for `working` here would never match and would just burn its whole timeout
  # on every ply -- the single biggest source of dead time in a match.
  SUBMITTED_BY_HAND=0
else
  ERR_CODE=$(printf '%s' "$RESP" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("error", {}).get("code", ""))
except Exception:
    print("")
' 2>/dev/null)

  if [ "$ERR_CODE" = "timeout" ]; then
    # The agent was genuinely still working when our budget ran out -- it is
    # not a submission glitch. Interrupt it so it stops burning tokens in the
    # background, and forfeit this ply now instead of polling for a reply
    # that may never come. Same policy as PlayerTimeout on the Python path:
    # immediate forfeit, no retry.
    herdr agent send-keys "$PANE" ctrl+c >/dev/null 2>&1
    exit 124
  fi

  if [ "$ERR_CODE" != "agent_prompt_stalled" ]; then
    echo "[ask.sh] unexpected herdr error: $RESP" >&2
  fi

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
# is still thinking, leaving the previous answer as the last move-line. Each
# re-read is scanned for cheat signatures too, so a mid-turn engine call is
# caught even if it happens after the initial read above.
PANE_TEXT=$(read_pane)
HIT=$(check_cheat "$PANE_TEXT") && fail_cheat "$HIT"
MOVE=$(last_move_from "$PANE_TEXT")
for _ in $(seq 1 20); do
  [ -n "$MOVE" ] && [ "$MOVE" != "$BEFORE" ] && break
  sleep 3
  PANE_TEXT=$(read_pane)
  HIT=$(check_cheat "$PANE_TEXT") && fail_cheat "$HIT"
  MOVE=$(last_move_from "$PANE_TEXT")
done

# Still unchanged after a minute: treat it as no answer, not as a move. Printing
# the stale line would submit a move the player never made this turn -- which
# then gets rejected as illegal and wastes one of its three chances. Returning
# nothing makes the caller re-prompt instead.
if [ -n "$BEFORE" ] && [ "$MOVE" = "$BEFORE" ]; then
  exit 0
fi

printf '%s\n' "$MOVE"
