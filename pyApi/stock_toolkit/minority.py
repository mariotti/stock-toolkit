"""
Minority-game crowd — Challet–Zhang bots for the ⏪ Replay page.

The basic minority game: an odd number of players each pick one of two
sides every round; whoever lands on the MINORITY side wins the round.
Each bot holds S random strategy tables (one entry per possible
history of the last m minority outcomes), keeps a virtual score per
table (a point whenever that table would have called the minority),
and always plays its best-scoring table — the canonical "basic
minority strategy". The human is player N+1, which is why the bot
count must be even.

Pure logic, no Streamlit. A crowd is a plain dict the UI keeps in
session state; nothing is persisted. Sides are 1 (up) and 0 (down).
"""

from __future__ import annotations

import random

__all__ = ["new_crowd", "play_round", "leaderboard"]


def new_crowd(n_bots: int, memory: int = 3, n_strategies: int = 2,
              seed: int | None = None) -> dict:
    """Build a crowd of n_bots basic-strategy players.

    n_bots must be even and >= 2 so that bots + the human make an odd
    total — with an odd headcount a minority always exists (no ties).
    """
    if n_bots < 2 or n_bots % 2:
        raise ValueError("n_bots must be even and >= 2 "
                         "(bots + you = odd headcount, so no ties)")
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
        # seed the books with a random past so round 1 has a history
        "history": [rng.randrange(2) for _ in range(memory)],
        "rounds": 0,
        "rng": rng,        # tie-breaks between equally scored tables
    }


def _hist_idx(crowd: dict) -> int:
    m = crowd["memory"]
    bits = crowd["history"][-m:]
    return int("".join(str(b) for b in bits), 2)


def play_round(crowd: dict, human_choice: int) -> dict:
    """One full round, mutating the crowd in place.

    Order matters and follows the canonical game: bots commit to a
    side first (best table on the current history, random tie-break),
    THEN the minority is computed over bots + human, virtual scores
    and points are updated, and the outcome is appended to history.
    """
    if human_choice not in (0, 1):
        raise ValueError("human_choice must be 0 (down) or 1 (up)")
    idx, rng = _hist_idx(crowd), crowd["rng"]

    choices = []
    for bot in crowd["bots"]:
        best = max(bot["vscores"])
        tied = [s for s, v in enumerate(bot["vscores"]) if v == best]
        choices.append(bot["strategies"][rng.choice(tied)][idx])

    ups = sum(choices) + human_choice
    total = len(choices) + 1
    minority = 1 if ups < total - ups else 0

    for bot, choice in zip(crowd["bots"], choices):
        for s, table in enumerate(bot["strategies"]):
            if table[idx] == minority:
                bot["vscores"][s] += 1
        if choice == minority:
            bot["points"] += 1

    crowd["history"].append(minority)
    crowd["rounds"] += 1
    return {
        "ups": ups, "downs": total - ups, "minority": minority,
        "human_won": human_choice == minority,
        "minority_size": min(ups, total - ups),
    }


def leaderboard(crowd: dict, human_points: int) -> dict:
    """Where the human stands: rank 1 = most round wins so far."""
    scores = sorted((b["points"] for b in crowd["bots"]), reverse=True)
    rank = 1 + sum(1 for s in scores if s > human_points)
    return {
        "rank": rank, "players": len(scores) + 1,
        "best_bot": scores[0] if scores else 0,
        "median_bot": scores[len(scores) // 2] if scores else 0,
    }
