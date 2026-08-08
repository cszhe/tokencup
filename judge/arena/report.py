"""Render /tmp/tc.json (the last TokenCup response) as compact text."""

import json
import sys

code = sys.argv[1]
d = json.load(open("/tmp/tc.json"))


def summarise(d):
    ms = d["moves"]
    print("status: %s | turn: %s | result: %s %s"
          % (d["status"], d["turn"], d["result"], d["termination"] or ""))
    print("plies: %d" % len(ms))
    print("moves: %s" % " ".join(m["san"] for m in ms))
    print("fen: %s" % d["fen"])


if code in ("state", "200", "201"):
    if code == "200" and d.get("moves"):
        last = d["moves"][-1]
        print("ACCEPTED ply %s: %s (%s)" % (last["ply"], last["san"], last["uci"]))
    summarise(d)
else:
    print("REJECTED %s: %s" % (code, d.get("detail")))
