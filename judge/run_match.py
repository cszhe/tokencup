"""CLI: create a game and run two players against each other to completion.

    python judge/run_match.py --white random --black greedy
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge import Judge, JudgeConfig, DEFAULT_BASE_URL  # noqa: E402
from players import BUILTIN_PLAYERS, make_player  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a TokenCup match")
    kinds = ", ".join(BUILTIN_PLAYERS)
    parser.add_argument("--white", default="random", help=f"white player kind ({kinds})")
    parser.add_argument("--black", default="random", help=f"black player kind ({kinds})")
    parser.add_argument("--white-name", help="display name for white")
    parser.add_argument("--black-name", help="display name for black")
    parser.add_argument("--url", default=DEFAULT_BASE_URL, help="TokenCup server URL")
    parser.add_argument("--games", type=int, default=1, help="number of games to play")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for bots")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--move-timeout", type=float, default=30.0)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = JudgeConfig(
        base_url=args.url,
        max_retries=args.max_retries,
        move_timeout=args.move_timeout,
        verbose=not args.quiet,
    )

    tally: dict[str, int] = {"1-0": 0, "0-1": 0, "1/2-1/2": 0}

    with Judge(config) as judge:
        for i in range(args.games):
            seed = None if args.seed is None else args.seed + i
            try:
                white = make_player(args.white, args.white_name, seed)
                black = make_player(args.black, args.black_name, seed)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2

            result = judge.play(white, black)
            if result.result in tally:
                tally[result.result] += 1
            print(f"  -> {judge.config.base_url}/#{result.game_id}")

    if args.games > 1:
        print(
            f"\ntally over {args.games} games: "
            f"white {tally['1-0']} / black {tally['0-1']} / draws {tally['1/2-1/2']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
