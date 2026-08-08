#!/bin/bash
# Prompt a player agent in a Herdr pane and extract the move from its reply.
# usage: ask.sh <pane-id> <message>
# Prints the move on stdout, or nothing if no move could be found.
herdr agent prompt "$1" "$2" --wait --timeout 300000 >/dev/null || exit 1
herdr agent read "$1" --source recent-unwrapped --lines 60 \
  | sed 's/[│┃|]//g; s/[█▄▀]//g' \
  | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
  | grep -Ex '([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?[+#]?|O-O(-O)?[+#]?|0-0(-0)?[+#]?)' \
  | tail -1
