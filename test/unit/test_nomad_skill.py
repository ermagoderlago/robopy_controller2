#!/usr/bin/env python3
"""
test_nomad_skill.py - Unit tests for NOMAD Exploration Skill
===========================================================
Tests voice pattern matching, skill metadata, and start/stop execution loops.
"""

import unittest
import asyncio
from unittest.mock import MagicMock

from robopy_controller.robot_ai.skills.builtin.nomad_exploration_skill import NomadExplorationSkill


class TestNomadSkill(unittest.TestCase):

    def setUp(self):
        self.mock_ros_node = MagicMock()
        self.skill = NomadExplorationSkill(ros_node=self.mock_ros_node)

    def test_skill_metadata(self):
        meta = self.skill.get_metadata()
        self.assertEqual(meta.name, "nomad_exploration")
        self.assertTrue(meta.requires_nav)
        self.assertGreater(meta.priority, 10)

    def test_pattern_matching(self):
        # Exploration triggers
        self.assertGreater(self.skill.match("Marcus, esplora con nomad la casa"), 0.9)
        self.assertGreater(self.skill.match("esplora con nomad"), 0.9)
        self.assertGreater(self.skill.match("esplora con nomade"), 0.9)
        self.assertGreater(self.skill.match("esplora"), 0.9)
        self.assertGreater(self.skill.match("fai una ricognizione autonoma"), 0.9)
        self.assertGreater(self.skill.match("inizia a esplorare la stanza"), 0.9)
        self.assertGreater(self.skill.match("mappa con nomad"), 0.9)
        self.assertGreater(self.skill.match("perlustra la casa"), 0.9)
        self.assertGreater(self.skill.match("fai un giro"), 0.9)

        # Stop triggers
        self.assertGreater(self.skill.match("ferma esplorazione"), 0.9)
        self.assertGreater(self.skill.match("stop esplorazione"), 0.9)
        self.assertGreater(self.skill.match("fermati"), 0.9)
        self.assertGreater(self.skill.match("stop nomad"), 0.9)

        # Non-matching inputs
        self.assertEqual(self.skill.match("raccontami una barzelletta"), 0.0)
        self.assertEqual(self.skill.match("che ore sono"), 0.0)

    def test_start_and_stop_execution(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Start exploration
        result_start = loop.run_until_complete(self.skill.execute("Marcus esplora con nomad"))
        self.assertTrue(result_start.success)
        self.assertTrue(self.skill.is_exploring)
        self.assertIn("NOMAD", result_start.speak)

        # Stop exploration
        result_stop = loop.run_until_complete(self.skill.execute("ferma esplorazione"))
        self.assertTrue(result_stop.success)
        self.assertFalse(self.skill.is_exploring)
        self.assertIn("fermo", result_stop.speak)

        loop.close()


if __name__ == '__main__':
    unittest.main()
