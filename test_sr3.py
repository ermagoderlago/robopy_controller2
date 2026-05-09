import sys
import os

pkg_base = '/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller'
sys.path.insert(0, pkg_base)

from robot_ai.skills.skill_registry import SkillRegistry

active_dir = '/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller/robot_ai/skills/active'
print("Initializing SkillRegistry...")
sr = SkillRegistry()
print("Calling discover_active...")
count = sr.discover_active(active_dir)
print(f"Discovered count: {count}")
