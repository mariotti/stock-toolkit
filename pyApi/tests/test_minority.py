"""
test_minority.py
================
Unit tests for stock_toolkit.minority — the Challet–Zhang bot crowd
behind the Replay page's 👥 Minority mode. Pure logic, no DB.
"""

import pathlib
import sys
import unittest

SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from stock_toolkit.minority import leaderboard, new_crowd, play_round  # noqa: E402


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

    def test_odd_or_tiny_bot_count_raises(self):
        for bad in (1, 3, 5, 0, -2):
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


class TestPlayRound(unittest.TestCase):

    def test_human_on_minority_side_wins(self):
        # Both bots always vote down (all-zero tables): human voting up
        # is a minority of one and wins; bots (majority) get nothing.
        crowd = _rigged_crowd(tables=[[0, 0, 0, 0], [0, 0, 0, 0]])
        res = play_round(crowd, 1)
        self.assertEqual((res["ups"], res["downs"]), (1, 2))
        self.assertEqual(res["minority"], 1)
        self.assertTrue(res["human_won"])
        self.assertEqual(res["minority_size"], 1)
        self.assertEqual([b["points"] for b in crowd["bots"]], [0, 0])

    def test_unanimous_round_has_empty_minority(self):
        # Everyone votes down → the up side is the (empty) minority:
        # canonical MG, nobody wins the round.
        crowd = _rigged_crowd(tables=[[0, 0, 0, 0], [0, 0, 0, 0]])
        res = play_round(crowd, 0)
        self.assertEqual((res["ups"], res["downs"]), (0, 3))
        self.assertEqual(res["minority"], 1)
        self.assertFalse(res["human_won"])
        self.assertEqual(res["minority_size"], 0)
        self.assertEqual([b["points"] for b in crowd["bots"]], [0, 0])

    def test_virtual_scores_reward_would_be_minority_calls(self):
        # Strategy 0 always says up, strategy 1 always says down. The
        # bots play... whichever, but after a round whose minority was
        # UP, every bot's strategy 0 must gain a virtual point and
        # strategy 1 must not.
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        # both bots vote up (tie-break picks either table; both bots'
        # table 0 and 1 disagree, but vscores are equal → rng decides).
        # Rig further: make vscores force table 1 (down) so the round
        # is deterministic: bots down, human up → minority up.
        for bot in crowd["bots"]:
            bot["vscores"] = [0, 5]
        res = play_round(crowd, 1)
        self.assertEqual(res["minority"], 1)
        for bot in crowd["bots"]:
            self.assertEqual(bot["vscores"][0], 1)   # 0 + reward
            self.assertEqual(bot["vscores"][1], 5)   # unchanged

    def test_history_grows_and_best_table_is_played(self):
        crowd = _rigged_crowd(tables=[[1, 1, 1, 1], [0, 0, 0, 0]])
        for bot in crowd["bots"]:
            bot["vscores"] = [10, 0]                 # force "always up"
        h0 = len(crowd["history"])
        res = play_round(crowd, 0)
        # bots up (2), human down (1) → minority down, human wins
        self.assertEqual((res["ups"], res["downs"]), (2, 1))
        self.assertTrue(res["human_won"])
        self.assertEqual(len(crowd["history"]), h0 + 1)
        self.assertEqual(crowd["history"][-1], res["minority"])
        self.assertEqual(crowd["rounds"], 1)

    def test_bad_human_choice_raises(self):
        crowd = new_crowd(2, seed=1)
        with self.assertRaises(ValueError):
            play_round(crowd, 2)


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
