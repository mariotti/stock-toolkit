"""
Challet–Zhang crowd that competes with YOU at calling the next bar.

Not the self-referential minority game: here the referee is the real
market. Each bot keeps the basic minority-game strategy machinery —
S random lookup tables over the last m outcomes, a virtual score per
table (a point whenever that table would have called the outcome),
always play the best table, random tie-break — but the outcome stream
it learns from and is scored against is the actual daily direction:
1 = up, 0 = down (flat days count as down). Bots and human bet on the
same bar each round; whoever called the market right wins the round.
The bots see only the direction history; the chart is the human's
private edge.

Pure logic, no Streamlit. A crowd is a plain dict the UI keeps in
session state; nothing is persisted.
"""

from __future__ import annotations

import random

__all__ = ["new_crowd", "warmup", "play_round", "leaderboard"]


def new_crowd(n_bots: int, memory: int = 3, n_strategies: int = 2,
              seed: int | None = None) -> dict:
    """Build a crowd of n_bots basic-strategy market callers."""
    if n_bots < 1:
        raise ValueError("n_bots must be >= 1")
    rng = random.Random(seed)
    n_hist = 2 ** memory
    bots = [{
        "strategies": [[rng.randrange(2) for _ in range(n_hist)]
                       for _ in range(n_strategies)],
        "vscores": [0] * n_strategies,
        "points": 0,
    } for _ in range(n_bots)]
    return {
        "memory": memory,
        "bots": bots,
        # random bootstrap history; warmup() overwrites it with real
        # market directions when the UI has bars to spare
        "history": [rng.randrange(2) for _ in range(memory)],
        "rounds": 0,
        "rng": rng,        # tie-breaks between equally scored tables
    }


def _hist_idx(crowd: dict) -> int:
    m = crowd["memory"]
    bits = crowd["history"][-m:]
    return int("".join(str(b) for b in bits), 2)


def _score_tables(crowd: dict, idx: int, outcome: int) -> None:
    """+1 virtual point to every table that called this outcome."""
    for bot in crowd["bots"]:
        for s, table in enumerate(bot["strategies"]):
            if table[idx] == outcome:
                bot["vscores"][s] += 1


def warmup(crowd: dict, outcomes: list[int]) -> None:
    """Replay real pre-game directions so the bots start adapted.

    Only virtual scores and the history evolve — no round points are
    awarded: the game hasn't started, the bots are just studying."""
    for outcome in outcomes:
        if outcome not in (0, 1):
            raise ValueError("outcomes must be 0 (down) or 1 (up)")
        _score_tables(crowd, _hist_idx(crowd), outcome)
        crowd["history"].append(outcome)


def play_round(crowd: dict, human_choice: int, outcome: int) -> dict:
    """One bar, everyone bets, the market referees.

    Bots commit first (best table on the current history, random
    tie-break), then the real outcome scores every player and every
    table, and joins the shared history."""
    if human_choice not in (0, 1) or outcome not in (0, 1):
        raise ValueError("human_choice and outcome must be 0 or 1")
    idx, rng = _hist_idx(crowd), crowd["rng"]

    bots_correct = 0
    for bot in crowd["bots"]:
        best = max(bot["vscores"])
        tied = [s for s, v in enumerate(bot["vscores"]) if v == best]
        if bot["strategies"][rng.choice(tied)][idx] == outcome:
            bot["points"] += 1
            bots_correct += 1

    _score_tables(crowd, idx, outcome)
    crowd["history"].append(outcome)
    crowd["rounds"] += 1
    return {
        "outcome": outcome,
        "human_won": human_choice == outcome,
        "bots_correct": bots_correct,
        "n_bots": len(crowd["bots"]),
    }


def leaderboard(crowd: dict, human_points: int) -> dict:
    """Where the human stands: rank 1 = most correct calls so far."""
    scores = sorted((b["points"] for b in crowd["bots"]), reverse=True)
    rank = 1 + sum(1 for s in scores if s > human_points)
    return {
        "rank": rank, "players": len(scores) + 1,
        "best_bot": scores[0] if scores else 0,
        "median_bot": scores[len(scores) // 2] if scores else 0,
    }
