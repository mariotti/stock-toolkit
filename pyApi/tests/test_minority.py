"""
test_minority.py
================
Unit tests for stock_toolkit.minority — the Challet–Zhang crowd that
competes with the human at calling the next bar on the Replay page.
The real market referees; bots learn from the direction history via
the basic minority-game strategy machinery. Pure logic, no DB.
"""

import pathlib
import sys
import unittest

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit.minority import (                             # noqa: E402
    leaderboard, new_crowd, play_round, warmup,
)


def _rigged_crowd(n_bots=2, memory=2, tables=None):
    """A crowd whose bots' strategy tables are overwritten with known
    values, so round outcomes are fully deterministic."""
    crowd = new_crowd(n_bots, memory=memory, seed=7)
    if tables is not None:
        for bot in crowd["bots"]:
            bot["strategies"] = [list(t) for t in tables]
            bot["vscores"] = [0] * len(tables)
    return crowd


class TestNewCrowd(unittest.TestCase):

    def test_zero_bots_raises(self):
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                new_crowd(bad)

    def test_shape_and_seed_determinism(self):
        a = new_crowd(4, memory=3, seed=42)
        b = new_crowd(4, memory=3, seed=42)
        self.assertEqual(len(a["bots"]), 4)
        self.assertEqual(len(a["history"]), 3)
        for bot in a["bots"]:
            self.assertEqual(len(bot["strategies"]), 2)
            for table in bot["strategies"]:
                self.assertEqual(len(table), 2 ** 3)
                self.assertTrue(set(table) <= {0, 1})
        self.assertEqual(
            [bot["strategies"] for bot in a["bots"]],
            [bot["strategies"] for bot in b["bots"]])
        self.assertEqual(a["history"], b["history"])


class TestWarmup(unittest.TestCase):

    def test_trains_vscores_and_history_without_points(self):
        # Strategy 0 always calls up, strategy 1 always down. Feeding
        # 5 up-days must give table 0 five virtual points, table 1
        # none — and no bot earns ROUND points from studying.
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        h0 = len(crowd["history"])
        warmup(crowd, [1, 1, 1, 1, 1])
        for bot in crowd["bots"]:
            self.assertEqual(bot["vscores"], [5, 0])
            self.assertEqual(bot["points"], 0)
        self.assertEqual(len(crowd["history"]), h0 + 5)
        self.assertEqual(crowd["history"][-5:], [1, 1, 1, 1, 1])
        self.assertEqual(crowd["rounds"], 0)

    def test_bad_outcome_raises(self):
        crowd = new_crowd(2, seed=1)
        with self.assertRaises(ValueError):
            warmup(crowd, [1, 2])


class TestPlayRound(unittest.TestCase):

    def test_market_referees_everyone(self):
        # Bots forced onto "always down" (table 1 has the higher
        # vscore); the market goes up → bots all miss, human up wins.
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        for bot in crowd["bots"]:
            bot["vscores"] = [0, 5]
        res = play_round(crowd, human_choice=1, outcome=1)
        self.assertTrue(res["human_won"])
        self.assertEqual(res["bots_correct"], 0)
        self.assertEqual(res["n_bots"], 2)
        self.assertEqual([b["points"] for b in crowd["bots"]], [0, 0])

    def test_correct_bots_score_and_tables_learn(self):
        # Bots forced onto "always up"; market up → every bot scores,
        # and the up-table gains a virtual point on top.
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        for bot in crowd["bots"]:
            bot["vscores"] = [5, 0]
        res = play_round(crowd, human_choice=0, outcome=1)
        self.assertFalse(res["human_won"])
        self.assertEqual(res["bots_correct"], 2)
        for bot in crowd["bots"]:
            self.assertEqual(bot["points"], 1)
            self.assertEqual(bot["vscores"], [6, 0])

    def test_history_grows_with_market_outcomes(self):
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        h0 = len(crowd["history"])
        play_round(crowd, 1, 0)
        play_round(crowd, 0, 1)
        self.assertEqual(crowd["history"][-2:], [0, 1])
        self.assertEqual(len(crowd["history"]), h0 + 2)
        self.assertEqual(crowd["rounds"], 2)

    def test_adaptation_switches_the_played_table(self):
        # After a long down-streak in warmup, a bot whose tables are
        # "always up" vs "always down" must be playing "always down".
        crowd = _rigged_crowd(n_bots=1,
                              tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        warmup(crowd, [0] * 10)
        res = play_round(crowd, human_choice=1, outcome=0)
        self.assertEqual(res["bots_correct"], 1)   # bot called down

    def test_bad_args_raise(self):
        crowd = new_crowd(2, seed=1)
        with self.assertRaises(ValueError):
            play_round(crowd, 2, 1)
        with self.assertRaises(ValueError):
            play_round(crowd, 1, 5)


class TestLeaderboard(unittest.TestCase):

    def test_rank_counts_only_strictly_better_bots(self):
        crowd = new_crowd(4, seed=3)
        pts = [5, 3, 3, 1]
        for bot, p in zip(crowd["bots"], pts):
            bot["points"] = p
        lb = leaderboard(crowd, 3)
        self.assertEqual(lb["players"], 5)
        self.assertEqual(lb["rank"], 2)      # only the 5 beats us
        self.assertEqual(lb["best_bot"], 5)
        self.assertEqual(lb["median_bot"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
