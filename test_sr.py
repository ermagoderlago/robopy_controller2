import sys
import logging
logging.basicConfig(level=logging.DEBUG)
from robopy_controller.robot_ai.skills.skill_registry import SkillRegistry
r = SkillRegistry()
count = r.discover_active("/mnt/ssd/robopy_controller_host/robopy_controller/robot_ai/skills/active")
print(f"CARICATE: {count}")
print("SKILLS:", r.get_all())
