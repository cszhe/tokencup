# Being the judge agent

Instructions for an AI agent running a chess match between two other AI agents, each
sitting in its own Herdr pane, with the game recorded on the TokenCup server.

Written after judging a full game to checkmate (66 plies, three illegal moves handled).
Everything flagged as a pitfall below is something that actually went wrong.

## Your job in one sentence

You are the only party that talks to the chess server; the players only ever send you a
move string, and you decide what happens when they send a bad one.

Three parties, three responsibilities, and they must not blur:

| Party | Owns | Never does |
| --- | --- | --- |
| **Server** | Chess rules. Whether a move is legal, whether the game is over. | Talk to players. Decide policy. |
| **You (judge)** | Policy. Whose turn, retries, forfeits, telling players what happened. | Decide legality yourself. |
| **Players** | Choosing a move. | Touch the server, the repo, or each other. |

The single rule that matters most: **you never decide whether a move is legal.** You submit
it and let the server answer. If you think a move is illegal but the server accepts it, the
server is right and you are wrong.

## Setup

### 1. Confirm you are inside Herdr

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, stop and say so. Do not try to drive panes from outside Herdr.

### 2. Find the players

```bash
herdr agent list
```

Read `pane_id` for each player out of the JSON. **Pitfall:** do not guess pane IDs from tab
names or sidebar order, and do not target agents by kind name (`opencode`, `agy`) — use the
pane ID, which is unambiguous. Check `agent_status` is `idle` or `done` before you start.

### 3. Confirm the server is up

```bash
curl -s http://127.0.0.1:8000/health
```

Expect `{"status":"ok"}`. If it is not running, start it (`cd backend && python main.py`)
before creating a game.

### 4. Create the game

```bash
curl -s -X POST http://127.0.0.1:8000/games \
  -H 'Content-Type: application/json' \
  -d '{"white_name":"opencode","black_name":"antigravity"}'
```

Save the `id` from the response. That is your game id for the whole match.

### 5. Wire up the helpers

```bash
cd judge/arena
cp arena.env.example arena.env
# edit arena.env: set TC_GAME to the game id, and the two pane IDs
chmod +x *.sh
./tc.sh --state
```

You should see the starting position with `plies: 0`. Now you are ready.

## Brief the players before the first move

Send each player a briefing and get an acknowledgement **before** asking for a move. Do not
combine "say hi" and "give me your move" in one message — weak models will do only one of
the two.

Use this template, swapping WHITE/BLACK and the opponent's name:

> Hi! I am the JUDGE for a real chess game. Your opponent is <NAME>, playing from a
> different terminal. Moves are recorded on TokenCup, a chess server for AI agents.
>
> You are playing WHITE. You move first.
>
> How this works:
> - I am the only one who talks to the chess server. You never call any API, run any
>   command, or edit any file in this repo. You only play chess.
> - When I ask for a move, reply with ONLY the move in standard algebraic notation on its
>   own line. Examples: e4, Nf3, O-O, exd5, Qxf7+, a8=Q. No commentary, no analysis, no
>   explanation.
> - After each of your opponent's turns I will tell you the move they played.
> - If your move is illegal or unreadable I will tell you why and ask again. Three rejected
>   moves in a row and you forfeit the game.
>
> Please reply with a short greeting and the single word READY. Do not send a move yet.

The "you never run any command or edit any file" line matters. These are coding agents
sitting in a repo. Without it, one may decide to be helpful and start reading your source.

## The turn loop

For each ply:

1. `./tc.sh --state` — read `turn` and the `moves` list. **The server tells you whose turn
   it is. Never track the turn in your head.**
2. Prompt the player on move, passing the full move list (see below).
3. Extract the move from its reply.
4. Submit it: `./tc.sh <move>`.
5. Accepted → next ply. Rejected → go to the rejection policy.
6. When `status: finished`, stop and announce the result.

`./round.sh <move-number>` does one full move (White then Black) with all of this applied.
That is the normal way to drive the game.

### Make every prompt self-sufficient

Every prompt should restate the player's colour, the complete move list, and the reply
format — not just the new move:

> You are playing BLACK in a chess game. I am the judge.
>
> Move list so far (SAN, in order): e4 e5 Nf3 Nc6 Bb5 a6 ...
>
> It is your turn as BLACK, move 9. Reply with ONLY the move in algebraic notation,
> nothing else.

**Pitfall:** weak models lose track of the position within a dozen moves. Restating the
whole game each turn is cheap and it is the main thing keeping them honest. Take the list
straight from `./tc.sh --state`, never from your own memory of what was played.

**Pitfall:** a player can compact or truncate its own context mid-game and lose the
briefing entirely — which colour it is, and that it should reply with a bare move. This
happened in the reference match: the White agent (a 200K-window model) exhausted its
context around move 30 and compacted. Because each prompt restates colour and format,
that is survivable and the player just keeps playing. If you only send the new move,
a compaction can silently break the game.

### Watch context growth

Your prompts carry the whole move list, and the exchange also accumulates inside each
player's own conversation, so token use grows roughly quadratically with move count. In
the reference match a 66-ply game cost the two players ~34K and ~89K tokens. A model with
a small window will fill up before a long game ends, get slower as it fills, and then pay
for a compaction.

If a player's context is a concern: run one game per session, and prefer the move list
over any longer position description. Do not try to save tokens by dropping the move list
— that trades a survivable slowdown for players that forget where the pieces are.

## Reading a move out of a player's reply

This is the fiddliest part of the job. Player agents run full-screen TUIs and their replies
arrive wrapped in box-drawing characters, spinners, thinking indicators, and token counters.

```bash
herdr agent read <pane> --source recent-unwrapped --lines 60
```

- Use `recent-unwrapped`, not `visible` — it joins soft wraps.
- Ask for plenty of lines. A short read often shows only the footer, with the actual reply
  already scrolled past.
- Strip `│ ┃ | █ ▄ ▀` and surrounding whitespace, then match lines that are *entirely* a
  move, and take the **last** one. Matching anywhere in a line will catch the move you
  quoted back in your own prompt.

`ask.sh` does exactly this. The regex it uses:

```
([KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](=[QRBN])?[+#]?|O-O(-O)?[+#]?|0-0(-0)?[+#]?)
```

If extraction returns nothing, that is **not** a forfeit-worthy chess error. Re-prompt with
"I could not find a move in your reply. Reply with ONLY the move and nothing else."

**Pitfall:** if a completed reply will not appear no matter how many lines you request, the
agent is drawing on the terminal's alternate screen and those rows are gone. Fall back to
asking that agent to write its answer to a file and reading the file.

## When a move is rejected

The server returns three failure codes, and they mean different things:

| Code | Meaning | What you do |
| --- | --- | --- |
| **400** | Unparseable — not chess notation at all (`Zz9`, `0000`, prose) | Retry; the player sent garbage |
| **409** | Parses fine, but not legal in this position | Retry; quote the server's reason back |
| **404** | No such game | Your bug, not the player's. Fix your game id |
| **422** | Malformed request body | Your bug. Fix your JSON |

For 400 and 409, send the player the server's own `detail` string verbatim:

> Your move "Bxb3" was REJECTED by the server: 'Bxb3' is not legal in this position (black
> to move)
>
> Pick a different, legal move.
>
> Move list so far (SAN, in order): ...

Then re-ask. **Three rejections in a row and that side forfeits:**

```bash
./tc.sh --resign black    # the side that forfeited; the other side wins
```

Both real illegal moves in this match were self-blocks — a bishop whose diagonal was
occupied by its own knight, and a rook that could not reach a square its own rook already
sat on. Both players fixed it on the first retry once told the reason. **Quoting the exact
server message is what makes the retry work**; a bare "that was illegal" is much weaker.

### Things you must not do when a move is rejected

- **Do not correct the move for them.** If a player says `Bxb3` and you think it meant
  `Nxb3`, that is your move, not theirs. Ask again.
- **Do not skip their turn** or play for them.
- **Do not restart the retry count** because you find their reasoning persuasive.
- **Do not argue with the server.** It replays the entire move history to decide legality
  and it is authoritative.

## Ending the game

The server ends the game by itself on checkmate, stalemate, insufficient material,
threefold repetition, and the fifty-move rule. You will see it in the response:

```
status: finished | turn: w | result: 0-1 checkmate
```

You only end a game manually in two cases:

- **Forfeit** — `./tc.sh --resign <side>` after the retry budget is gone.
- **Adjudication** — a draw you are imposing rather than one the rules reached, e.g. a ply
  cap. `POST /games/<id>/adjudicate {"result":"1/2-1/2","termination":"ply_limit"}`.
  Use this rather than pinning a loss on whoever happens to be on move.

Then tell both players. Send each one the result, the final PGN
(`./tc.sh --pgn`), and one honest sentence about how it went. Say plainly whether they won
or lost — do not soften a loss into ambiguity.

## Pitfalls that cost time

- **Do not batch many rounds into one shell command.** Each ply waits on a model, so five
  rounds can exceed a two-minute command timeout and get killed mid-retry. Two to four
  rounds per command is comfortable; raise the timeout if you go higher.
- **After any timeout or crash, re-read `./tc.sh --state` before doing anything.** The
  server is the source of truth for what actually landed. In this match a reporting script
  crashed *after* a successful submit, which briefly looked like a failed move.
- **Ignore check/mate suffixes from players.** A player may send `Rd2+` when it is not
  check. The server normalises SAN when it stores the move; do not "correct" the player and
  do not rely on their suffix to tell you anything about the position.
- **Take pane IDs from JSON, every time.** They are stable, but a pane moved between
  workspaces gets a new ID.
- **Use `--no-focus` for anything that would steal the user's screen.** Prompting an agent
  does not move focus; splitting panes can.

## Checklist

```
[ ] HERDR_ENV=1 confirmed
[ ] both player panes found via `herdr agent list`, status idle
[ ] /health returns ok
[ ] game created, id saved into arena.env
[ ] both players briefed and acknowledged READY
[ ] loop: state -> prompt player on move -> extract -> submit -> handle rejection
[ ] full move list resent in every prompt, taken from the server
[ ] server's own rejection text quoted back to the player on 400/409
[ ] 3 strikes -> forfeit via ./tc.sh --resign <side>
[ ] game reaches status: finished
[ ] both players told the result and given the PGN
```
