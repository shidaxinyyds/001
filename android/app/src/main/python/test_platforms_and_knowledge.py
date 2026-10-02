# -*- coding: utf-8 -*-
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import platforms
from platforms import (
    PLATFORMS,
    DEFAULT_PLATFORM,
    get_platform,
    get_hand_roi,
    get_river_zones,
    get_supported_modes,
    load_platform,
    set_platform_explicit,
)
from knowledge_base import KnowledgeBase


class TestPlatforms(unittest.TestCase):
    def test_default_platform(self):
        self.assertEqual(DEFAULT_PLATFORM, "tencent")
        p = get_platform("tencent")
        self.assertIn("name", p)
        self.assertIn("hand_roi", p)
        self.assertIn("river_zones", p)
        self.assertIn("supported_modes", p)

    def test_all_five_platforms(self):
        expected_keys = ["tencent", "tuyou", "weile", "jj", "generic"]
        for k in expected_keys:
            self.assertIn(k, PLATFORMS)
            p = get_platform(k)
            self.assertEqual(p["key"], k)
            roi = get_hand_roi(k)
            self.assertEqual(len(roi), 4)
            # top < bottom
            self.assertLess(roi[0], roi[1])
            zones = get_river_zones(k)
            self.assertEqual(len(zones), 4)
            modes = get_supported_modes(k)
            self.assertGreater(len(modes), 0)

    def test_set_platform_explicit(self):
        ok = set_platform_explicit("tuyou")
        self.assertTrue(ok)
        self.assertEqual(load_platform(), "tuyou")
        # Invalid platform should fail safely
        bad = set_platform_explicit("non_existent_platform_999")
        self.assertFalse(bad)
        # Restore to tencent
        set_platform_explicit("tencent")
        self.assertEqual(load_platform(), "tencent")


class TestKnowledgeBase(unittest.TestCase):
    def test_tile_id_parsing(self):
        self.assertEqual(KnowledgeBase.parse_tile_id("1m"), 0)
        self.assertEqual(KnowledgeBase.parse_tile_id("9m"), 8)
        self.assertEqual(KnowledgeBase.parse_tile_id("1p"), 9)
        self.assertEqual(KnowledgeBase.parse_tile_id("9p"), 17)
        self.assertEqual(KnowledgeBase.parse_tile_id("1s"), 18)
        self.assertEqual(KnowledgeBase.parse_tile_id("9s"), 26)
        self.assertEqual(KnowledgeBase.parse_tile_id("7z"), 33)
        self.assertIsNone(KnowledgeBase.parse_tile_id("invalid"))

    def test_suji_map(self):
        # 1m (id 0) -> suji 4m (id 3)
        self.assertIn(3, KnowledgeBase.SUJI_MAP[0])
        # 4m (id 3) -> suji 1m (id 0) and 7m (id 6)
        self.assertIn(0, KnowledgeBase.SUJI_MAP[3])
        self.assertIn(6, KnowledgeBase.SUJI_MAP[3])

    def test_evaluate_tactics_general(self):
        hand_counts = [0] * 34
        hand_counts[0] = 3  # 1m
        hand_counts[1] = 1  # 2m
        hand_counts[2] = 2  # 3m (Gold 3/7)
        disc_counts = [0] * 34
        meld_counts = [0] * 34

        advice = [
            {"tile": "2m", "ukeire": 4, "shanten": 1, "ev": 100.0, "reason": "进张"},
            {"tile": "3m", "ukeire": 6, "shanten": 1, "ev": 105.0, "reason": "进张"},
        ]

        res = KnowledgeBase.evaluate_tactics(
            hand_counts=hand_counts,
            disc_counts=disc_counts,
            meld_counts=meld_counts,
            mode="sc_hz",
            shanten=1,
            advice_list=advice,
            mood_state="steady"
        )

        self.assertIn("doctrine", res)
        self.assertIn("tips", res)
        self.assertIn("ev_adjustments", res)
        for item in advice:
            self.assertIn("tactical_tip", item)
            self.assertIn("tactical_ev_boost", item)
            self.assertTrue(len(item["tactical_tip"]) > 0)

    def test_evaluate_tactics_defensive_genbutsu(self):
        hand_counts = [0] * 34
        hand_counts[0] = 1  # 1m
        hand_counts[3] = 1  # 4m
        disc_counts = [0] * 34
        disc_counts[0] = 1  # 1m is genbutsu (现物)
        meld_counts = [0] * 34

        advice = [
            {"tile": "4m", "ukeire": 4, "shanten": 2, "ev": 50.0, "reason": "进张", "defense_level": "DANGER"},
            {"tile": "1m", "ukeire": 1, "shanten": 2, "ev": 40.0, "reason": "现物", "defense_level": "SAFE"},
        ]

        res = KnowledgeBase.evaluate_tactics(
            hand_counts=hand_counts,
            disc_counts=disc_counts,
            meld_counts=meld_counts,
            mode="std_tdh",
            shanten=2,
            advice_list=advice,
            mood_state="defensive"
        )

        self.assertIn("防守总纲", res["doctrine"])
        adv_1m = [a for a in advice if a["tile"] == "1m"][0]
        adv_4m = [a for a in advice if a["tile"] == "4m"][0]
        self.assertIn("现物防守", adv_1m["tactical_tip"])
        self.assertIn("高危预警", adv_4m["tactical_tip"])
        # In defensive mode, genbutsu EV boost should push 1m ahead of dangerous 4m!
        self.assertEqual(advice[0]["tile"], "1m")


if __name__ == "__main__":
    unittest.main()
