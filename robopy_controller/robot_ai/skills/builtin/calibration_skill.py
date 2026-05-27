"""
Robot AI Skills - Calibration Skill
====================================
Skill per calibrare i motori, l'attrito statico e i coefficienti PID usando dati VO/IMU.
"""

import re
import time
import asyncio
import os
import math
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..base_skill import BaseSkill, SkillMetadata, SkillResult, SkillErrorCode
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry

class CalibrationSkill(BaseSkill):
    """
    Skill for robot motor calibration.
    
    Handles commands like:
    - "Avvia calibrazione motori"
    - "Testa i motori"
    - "Calibrazione"
    """
    
    CALIBRATION_PATTERNS = [
        re.compile(r'\b(calibra|calibrazione|test|testa)\b.*\b(motori|movimento|cingoli)\b', re.IGNORECASE),
        re.compile(r'\b(avvia calibrazione)\b', re.IGNORECASE),
    ]
    
    def __init__(self, node):
        super().__init__()
        self.node = node
        self.is_calibrating = False
        self._waiting_confirmation = False
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_yaw = 0.0
        
        # Subscribe to /vo/odom to track the robot's movement reliably
        self._odom_sub = self.node.create_subscription(
            Odometry, 
            '/vo/odom', 
            self._odom_callback, 
            10
        )
        
    def _odom_callback(self, msg: Odometry):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        
        # Extract yaw from quaternion
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        self.current_yaw = math.atan2(siny_cosp, cosy_cosp)
        
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="calibration",
            description="Run automatic motor calibration routines",
            version="1.0.0",
            keywords=["calibrazione", "test motori", "calibra"],
            priority=9,
            requires_nav=False
        )
        
    def match(self, text: str, context: Dict[str, Any] = None) -> float:
        if self._waiting_confirmation:
            if re.search(r'\b(si|sì|certo|ok|va bene|yes|procedi|avvia|vai)\b', text, re.IGNORECASE):
                return 1.0
            else:
                self._waiting_confirmation = False
                return 0.0
                
        if any(p.search(text) for p in self.CALIBRATION_PATTERNS):
            return 1.0
        return 0.0

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start_calibration"],
                    "description": "Avvia la routine di calibrazione motori"
                },
                "user_confirmed": {
                    "type": "boolean",
                    "description": "Devi PRIMA chiedere all'utente se è sicuro di voler far muovere il robot. Imposta questo a true SOLO se l'utente ha risposto esplicitamente di sì nel turno o prompt corrente."
                }
            },
            "required": ["action", "user_confirmed"]
        }
        
    async def execute(self, text: str, context: Dict[str, Any] = None) -> SkillResult:
        if self.is_calibrating:
            return SkillResult.failure_result("La calibrazione è già in corso.", SkillErrorCode.SERVICE_UNAVAILABLE)

        if self._waiting_confirmation:
            self._waiting_confirmation = False
            user_confirmed = True
        else:
            # Parse intent from LLM args if available (when called as function)
            intent = context.get("tool_args", {}) if context else {}
            user_confirmed = intent.get("user_confirmed", False)
            
            if not user_confirmed:
                self._waiting_confirmation = True
                return SkillResult(
                    success=False,
                    message="In attesa di conferma utente",
                    speak="Sei sicuro di voler avviare la calibrazione dei motori? Fai attenzione, mi muoverò da solo per la stanza.",
                    error_code=SkillErrorCode.INVALID_PARAMETERS
                )

        self.is_calibrating = True
        
        # Lancia la calibrazione in background per non bloccare AI
        asyncio.create_task(self._run_calibration())
        
        return SkillResult(
            success=True,
            message="Avvio routine di calibrazione confermato",
            speak="Ricevuto. Avvio la calibrazione dei motori. Fatti indietro.",
        )

    async def _run_calibration(self):
        try:
            self.node.ai_logger.info("Starting Motor Calibration Routine...")
            
            report_lines = [
                f"# Robot Calibration Report",
                f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ""
            ]
            
            await self._wait(2.0)
            
            # --- Test 1: Linear Forward/Backward ---
            self.node.ai_logger.info("Test 1: Linear Movements")
            report_lines.append("## Linear Movement Test (vs 1.0 m/s command)")
            
            fwd_dists = []
            bwd_dists = []
            
            for _ in range(5):
                dist = await self._test_move(linear_x=1.0, duration=10.0)
                fwd_dists.append(dist)
                await self._wait(1.0)
                
                dist = await self._test_move(linear_x=-1.0, duration=10.0)
                bwd_dists.append(dist)
                await self._wait(1.0)
                
            avg_fwd = sum(fwd_dists)/len(fwd_dists)
            avg_bwd = sum(bwd_dists)/len(bwd_dists)
            
            report_lines.append(f"- **Forward List (m):** {[round(d, 3) for d in fwd_dists]}")
            report_lines.append(f"- **Avg Forward (m/s):** `{avg_fwd:.3f}`")
            report_lines.append(f"- **Backward List (m):** {[round(d, 3) for d in bwd_dists]}")
            report_lines.append(f"- **Avg Backward (m/s):** `{avg_bwd:.3f}`")
            report_lines.append("")
            
            # --- Test 2: Angular Rotations ---
            self.node.ai_logger.info("Test 2: Angular Rotations")
            report_lines.append("## Angular Movement Test (vs 2.0 rad/s command)")
            
            left_rads = []
            right_rads = []
            
            for _ in range(5):
                rad = await self._test_move(angular_z=2.0, duration=10.0)
                left_rads.append(rad)
                await self._wait(1.0)
                
                rad = await self._test_move(angular_z=-2.0, duration=10.0)
                right_rads.append(rad)
                await self._wait(1.0)
                
            avg_left = sum(left_rads)/len(left_rads)
            avg_right = sum(right_rads)/len(right_rads)
            
            report_lines.append(f"- **Left List (rad):** {[round(r, 3) for r in left_rads]}")
            report_lines.append(f"- **Avg Left (rad/s):** `{avg_left:.3f}`")
            report_lines.append(f"- **Right List (rad):** {[round(r, 3) for r in right_rads]}")
            report_lines.append(f"- **Avg Right (rad/s):** `{avg_right:.3f}`")
            report_lines.append("")
            
            # --- Save Report ---
            home = os.path.expanduser("~")
            report_path = os.path.join(home, "robopy", "logs", "calibration_report.md")
            with open(report_path, "w") as f:
                f.write("\n".join(report_lines))
                
            self.node.ai_logger.info(f"Calibration finished. Report saved to {report_path}")
            
            # Use ROS 2 TTS if available
            await self.node.tts_service.speak("Calibrazione terminata, ho salvato i risultati.")
            
        except Exception as e:
            self.node.ai_logger.error(f"Calibration failed: {e}")
        finally:
            self.is_calibrating = False
            
    async def _test_move(self, linear_x=0.0, angular_z=0.0, duration=10.0) -> float:
        # Start tracking /vo/odom
        start_x = self.current_x
        start_y = self.current_y
        start_yaw = self.current_yaw
        
        tw = Twist()
        tw.linear.x = float(linear_x)
        tw.angular.z = float(angular_z)
        
        start_time = time.time()
        
        # Pass the twist to the reactive loop to ensure 50Hz publishing
        with self.node._reactive_cmd_vel_lock:
            self.node._reactive_cmd_vel = tw
            
        await asyncio.sleep(duration)
            
        # Stop
        with self.node._reactive_cmd_vel_lock:
            self.node._reactive_cmd_vel = Twist()
            
        # Wait for inertia
        await asyncio.sleep(0.5)
        
        end_x = self.current_x
        end_y = self.current_y
        end_yaw = self.current_yaw
        
        if linear_x != 0.0:
            dist = math.sqrt((end_x - start_x)**2 + (end_y - start_y)**2)
            return dist
        elif angular_z != 0.0:
            dyaw = end_yaw - start_yaw
            # Normalize angle to -pi..pi
            dyaw = math.atan2(math.sin(dyaw), math.cos(dyaw))
            return abs(dyaw)
        return 0.0
            
    async def _wait(self, sec: float):
        await asyncio.sleep(sec)
