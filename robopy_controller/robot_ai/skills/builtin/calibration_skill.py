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
        self.wheel_x = 0.0
        self.wheel_y = 0.0
        self.wheel_yaw = 0.0
        
        # Subscribe to /odom (Visual Odometry / VIO)
        self._odom_sub = self.node.create_subscription(
            Odometry, 
            '/odom', 
            self._odom_callback, 
            10
        )
        
        # Subscribe to /odom_wheel (Waveshare ESP32 Wheel Odometry)
        self._wheel_odom_sub = self.node.create_subscription(
            Odometry, 
            '/odom_wheel', 
            self._wheel_odom_callback, 
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

    def _wheel_odom_callback(self, msg: Odometry):
        self.wheel_x = msg.pose.pose.position.x
        self.wheel_y = msg.pose.pose.position.y
        
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        self.wheel_yaw = math.atan2(siny_cosp, cosy_cosp)
        
    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="calibration",
            description="Run automatic motor and wheel-VIO calibration routines",
            version="2.0.0",
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
                    "description": "Avvia la routine di calibrazione motori e confronto VIO/Ruote"
                },
                "user_confirmed": {
                    "type": "boolean",
                    "description": "Devi PRIMA chiedere all'utente se è sicuro di voler far muovere il robot."
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
            intent = context.get("tool_args", {}) if context else {}
            user_confirmed = intent.get("user_confirmed", False)
            
            if not user_confirmed:
                self._waiting_confirmation = True
                return SkillResult(
                    success=False,
                    message="In attesa di conferma utente",
                    speak="Sei sicuro di voler avviare la calibrazione dei motori? Fai attenzione, mi muoverò delicatamente per la stanza.",
                    error_code=SkillErrorCode.INVALID_PARAMETERS
                )

        self.is_calibrating = True
        asyncio.create_task(self._run_calibration())
        
        return SkillResult(
            success=True,
            message="Avvio routine di calibrazione confermato",
            speak="Ricevuto. Avvio la calibrazione VIO e motori. Fatti indietro.",
        )

    async def _run_calibration(self):
        try:
            self.node.ai_logger.info("Starting Motor & VIO Calibration Routine...")
            
            report_lines = [
                f"# Robot VIO & Wheel Motor Calibration Report",
                f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                ""
            ]
            
            await self._wait(2.0)
            
            # --- Test 1: Linear Test (0.15 m/s, 3.0s) ---
            self.node.ai_logger.info("Test 1: Linear Movements (0.15 m/s)")
            report_lines.append("## 1. Linear Movement Test (v_x = 0.15 m/s, 3.0s)")
            
            vio_linear = []
            wheel_linear = []
            
            for _ in range(3):
                d_vio, d_wheel = await self._test_move(linear_x=0.15, duration=3.0)
                vio_linear.append(d_vio)
                wheel_linear.append(d_wheel)
                await self._wait(1.0)
                
            avg_vio_lin = sum(vio_linear) / len(vio_linear) if vio_linear else 0.0
            avg_wheel_lin = sum(wheel_linear) / len(wheel_linear) if wheel_linear else 0.0
            scale_lin = (avg_vio_lin / avg_wheel_lin) if avg_wheel_lin > 0.001 else 1.0
            
            report_lines.append(f"- **VIO Linear Distances (m):** {[round(d, 3) for d in vio_linear]} (Avg: `{avg_vio_lin:.3f}`m)")
            report_lines.append(f"- **Wheel Linear Distances (m):** {[round(d, 3) for d in wheel_linear]} (Avg: `{avg_wheel_lin:.3f}`m)")
            report_lines.append(f"- **Calculated Linear Scale Ratio (VIO/Wheel):** `{scale_lin:.4f}`")
            report_lines.append(f"- 💡 **Suggested `ticks_per_rev` adjustment:** `280 * {scale_lin:.4f} = {round(280 * scale_lin)}`")
            report_lines.append("")
            
            # --- Test 2: Angular Test (0.4 rad/s, 4.0s) ---
            self.node.ai_logger.info("Test 2: Angular Rotations (0.4 rad/s)")
            report_lines.append("## 2. Angular Movement Test (w_z = 0.4 rad/s, 4.0s)")
            
            vio_angular = []
            wheel_angular = []
            
            for _ in range(3):
                a_vio, a_wheel = await self._test_move(angular_z=0.4, duration=4.0)
                vio_angular.append(a_vio)
                wheel_angular.append(a_wheel)
                await self._wait(1.0)
                
            avg_vio_ang = sum(vio_angular) / len(vio_angular) if vio_angular else 0.0
            avg_wheel_ang = sum(wheel_angular) / len(wheel_angular) if wheel_angular else 0.0
            scale_ang = (avg_vio_ang / avg_wheel_ang) if avg_wheel_ang > 0.001 else 1.0
            
            report_lines.append(f"- **VIO Angular Radians:** {[round(r, 3) for r in vio_angular]} (Avg: `{avg_vio_ang:.3f}` rad)")
            report_lines.append(f"- **Wheel Angular Radians:** {[round(r, 3) for r in wheel_angular]} (Avg: `{avg_wheel_ang:.3f}` rad)")
            report_lines.append(f"- **Calculated Angular Scale Ratio (VIO/Wheel):** `{scale_ang:.4f}`")
            report_lines.append(f"- 💡 **Suggested `rotational_wheel_separation` adjustment:** `0.285 / {scale_ang:.4f} = {round(0.285 / scale_ang, 4)}`m")
            report_lines.append("")
            
            # --- Save Report ---
            home = os.path.expanduser("~")
            report_path = os.path.join(home, "robopy", "logs", "calibration_report.md")
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            with open(report_path, "w") as f:
                f.write("\n".join(report_lines))
                
            self.node.ai_logger.info(f"Calibration finished. Report saved to {report_path}")
            await self.node.tts_service.speak("Calibrazione VIO e motori completata. Ho salvato il report con i parametri aggiornati.")
            
        except Exception as e:
            self.node.ai_logger.error(f"Calibration failed: {e}")
        finally:
            self.is_calibrating = False
            
    async def _test_move(self, linear_x=0.0, angular_z=0.0, duration=3.0) -> tuple:
        start_x, start_y, start_yaw = self.current_x, self.current_y, self.current_yaw
        start_wx, start_wy, start_wyaw = self.wheel_x, self.wheel_y, self.wheel_yaw
        
        tw = Twist()
        tw.linear.x = float(linear_x)
        tw.angular.z = float(angular_z)
        
        with self.node._reactive_cmd_vel_lock:
            self.node._reactive_cmd_vel = tw
            
        await asyncio.sleep(duration)
            
        with self.node._reactive_cmd_vel_lock:
            self.node._reactive_cmd_vel = Twist()
            
        await asyncio.sleep(0.5)
        
        end_x, end_y, end_yaw = self.current_x, self.current_y, self.current_yaw
        end_wx, end_wy, end_wyaw = self.wheel_x, self.wheel_y, self.wheel_yaw
        
        if linear_x != 0.0:
            dist_vio = math.sqrt((end_x - start_x)**2 + (end_y - start_y)**2)
            dist_wheel = math.sqrt((end_wx - start_wx)**2 + (end_wy - start_wy)**2)
            return dist_vio, dist_wheel
        elif angular_z != 0.0:
            dyaw_vio = math.atan2(math.sin(end_yaw - start_yaw), math.cos(end_yaw - start_yaw))
            dyaw_wheel = math.atan2(math.sin(end_wyaw - start_wyaw), math.cos(end_wyaw - start_wyaw))
            return abs(dyaw_vio), abs(dyaw_wheel)
        return 0.0, 0.0
            
    async def _wait(self, sec: float):
        await asyncio.sleep(sec)
