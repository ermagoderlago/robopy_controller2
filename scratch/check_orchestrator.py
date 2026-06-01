import sys
import os

# Aggiungi i percorsi giusti
sys.path.append('/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages')
sys.path.append('/mnt/ssd/robopy_controller_host/install/robopy_controller/lib/python3.11/site-packages/robopy_controller')
sys.path.append('/home/robopy/ros2_jazzy/install/setup.bash')

try:
    import rclpy
    rclpy.init()
    from robot_ai.orchestration.orchestrator import AIOrchestrator
    node = AIOrchestrator()
    print('hasattr(node, "scheduler"):', hasattr(node, 'scheduler'))
    if hasattr(node, 'scheduler'):
        print('type(node.scheduler):', type(node.scheduler))
        print('node.scheduler is:', node.scheduler)
        
        alarm_skill = node.skill_registry.get('alarm')
        if alarm_skill:
            print('alarm_skill is:', alarm_skill)
            print('alarm_skill.scheduler is:', alarm_skill.scheduler)
            print('type(alarm_skill.scheduler):', type(alarm_skill.scheduler))
except Exception as e:
    import traceback
    traceback.print_exc()
