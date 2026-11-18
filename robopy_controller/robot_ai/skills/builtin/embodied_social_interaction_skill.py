#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymorphic Embodied Social Interaction Skill (SPEC-01, SPEC-02, SPEC-03, SPEC-05).
===================================================================================
Integra:
- Percezione fusa posa + bounding box oggetti + mappa (`SpatialPosturalSceneSnapshot`)
- Ragionamento locale Qwen 2.5 via `SLMMetapromptFusion` (<1150 token) e `HailoQwenClient`
- Pattern polimorfico Strategy per risposte sociali a minima energia metabolica:
  1. InhibitedRetreatToDockStrategy  (inibizione da zittimento -> arretramento 0.25m + ritorno in cuccia)
  2. KneelingEyeLevelApproachStrategy (utente inginocchiato -> avvicinamento dolce a 0.50m)
  3. CommandedCompanionFollowStrategy (richiesta vocale o gesto BECKON_COME -> following companion)
  4. WaveGreetingMirrorStrategy       (saluto mano alzata WAVE_HELLO -> mirroring a minimo sforzo + oggetti vicini)
  5. CuriousAutonomousFollowStrategy  (curiosità / slancio sociale alto -> following curioso a tempo limitato)
- Pre-flight check obbligatorio su `TrinityKinematicSandbox` (FM-LLM-004).
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...cognitive.homeostasis_mal import HomeostaticDriveEngine, ProactiveEvent
from ...cognitive.spatial_postural_scene import (
    MetabolicEffortTier,
    SpatialPosturalSceneBuilder,
    SpatialPosturalSceneSnapshot,
)
from ...cognitive.theory_of_mind_engine import ToMState
from ...hailo.hailo_qwen_client import HailoQwenClient
from ...hailo.qwen_prompt_templates import JsonCallExtractor
from ...services.visual_gesture_pointing_engine import GestureType
from ...trinity.cag_environment import EnvironmentSnapshot
from ...trinity.kinematic_sandbox import RobotSafetyContext, TrinityKinematicSandbox
from ...trinity.slm_metaprompt_fusion import SLMMetapromptFusion, SLMPromptContext
from ...utils.logging_utils import get_logger
from ..base_skill import BaseSkill, Capability, SkillErrorCode, SkillMetadata, SkillResult
from .follow_person_skill import FollowPersonSkill

logger = get_logger("EmbodiedSocialInteractionSkill")

_SHUSH_RE = re.compile(
    r"\b(zitto|stai zitto|taci|basta|silenzio|non disturbare|vai in cuccia|vai a cuccia|torna alla base|lascia stare)\b",
    re.IGNORECASE,
)
_FOLLOW_CMD_RE = re.compile(
    r"\b(seguimi|vieni con me|stai con me|vieni qui|follow me|cammina con me)\b",
    re.IGNORECASE,
)
_POSITIVE_DIALOGUE_RE = re.compile(
    r"\b(ciao|buongiorno|buonasera|salve|bravo|grazie|bene|parliamo|come stai|raccontami|piacere|bello|ottimo|ehi marcus)\b",
    re.IGNORECASE,
)


@dataclass
class InteractionExecutionPlan:
    """Piano di risposta sociale validato dal Kinematic Sandbox e calibrato sull'energia."""
    strategy_name: str
    effort_tier: MetabolicEffortTier
    speech: str
    tail_angle_deg: float = 0.0
    tail_freq_hz: float = 0.0
    validated_actions: List[Dict[str, Any]] = field(default_factory=list)
    rejected_reasons: List[str] = field(default_factory=list)
    follow_mode: Optional[str] = None
    follow_target_identity: Optional[str] = None
    stop_following: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class SocialInteractionStrategy(ABC):
    """Strategia polimorfica astratta per comportamenti sociali incarnati di Marcus."""

    name: str = "base_social_strategy"

    @abstractmethod
    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        """Restituisce un punteggio di confidenza in [0.0, 1.0]."""

    @abstractmethod
    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        """Costruisce il piano cinematico-sociale validato dal sandbox."""

    @staticmethod
    def _validate_sandbox_step(
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        function_name: str,
        arguments: Dict[str, Any],
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        res = sandbox.validate_action(
            {"call": {"name": function_name, "arguments": arguments}},
            safety_ctx,
        )
        if res.authorized:
            return True, {
                "function_name": function_name,
                "arguments": dict(res.clamped_arguments or arguments),
                "sandbox_authorized": True,
            }, ""
        return False, None, f"{function_name}: {res.error_code.value} ({res.reason})"

    @staticmethod
    def _clamp_tail(angle_deg: float, freq_hz: float) -> Tuple[float, float]:
        # Vincoli SPEC-01 Green Zone: angolo [-45, +45] deg, frequenza <= 3.0 Hz
        return (
            round(max(-45.0, min(45.0, float(angle_deg))), 1),
            round(max(0.0, min(3.0, float(freq_hz))), 2),
        )


class InhibitedRetreatToDockStrategy(SocialInteractionStrategy):
    """
    Se l'utente zittisce ripetutamente Marcus o lo manda in cuccia (`social_inhibition >= 0.50`),
    interrompe qualsiasi inseguimento, abbassa la coda, indietreggia dolcemente e rientra alla base.
    """

    name = "inhibited_retreat_to_dock"

    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        is_shush_text = bool(_SHUSH_RE.search(text or ""))
        inh = max(scene.social_inhibition, drives.social_inhibition)
        if inh >= 0.50:
            return 0.99
        if is_shush_text:
            return 0.97
        return 0.0

    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        actions: List[Dict[str, Any]] = []
        rejected: List[str] = []

        if follow_skill is not None and follow_skill.is_following:
            follow_skill.stop_following()

        # Fase 1: Passo indietro gentile (0.25m) se spazio posteriore libero
        ok_back, cmd_back, err_back = self._validate_sandbox_step(
            sandbox,
            safety_ctx,
            "move_distance",
            {"direction": "indietro", "distance_m": 0.25, "speed": 0.12},
        )
        if ok_back and cmd_back:
            actions.append(cmd_back)
        elif err_back:
            rejected.append(err_back)

        # Fase 2: Rientro in cuccia ('base')
        ok_dock, cmd_dock, err_dock = self._validate_sandbox_step(
            sandbox,
            safety_ctx,
            "navigate_to",
            {"destination": "base"},
        )
        if ok_dock and cmd_dock:
            actions.append(cmd_dock)
        elif err_dock:
            rejected.append(err_dock)

        tail_deg, tail_hz = self._clamp_tail(-20.0, 0.0)
        return InteractionExecutionPlan(
            strategy_name=self.name,
            effort_tier=MetabolicEffortTier.LOCOMOTION_NAV,
            speech=(
                "Scusa per il disturbo, faccio un piccolo passo indietro e torno alla mia cuccia."
            ),
            tail_angle_deg=tail_deg,
            tail_freq_hz=tail_hz,
            validated_actions=actions,
            rejected_reasons=rejected,
            stop_following=True,
            metadata={"social_inhibition": round(drives.social_inhibition, 3)},
        )


class KneelingEyeLevelApproachStrategy(SocialInteractionStrategy):
    """
    Marcus è basso da terra: quando l'utente si inginocchia/abbassa davanti a lui
    (`ToMState.KNEELING_EYE_LEVEL`), si avvicina con cautela fino a esattamente 0.50 m.
    """

    name = "kneeling_eye_level_approach"
    TARGET_PROXEMIC_M: float = 0.50

    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        if max(scene.social_inhibition, drives.social_inhibition) >= 0.50:
            return 0.0
        if scene.tom_state == ToMState.KNEELING_EYE_LEVEL.value:
            return 0.93
        return 0.0

    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        actions: List[Dict[str, Any]] = []
        rejected: List[str] = []

        # Eventuale riallineamento angolare leggero se fuori asse (> 18 deg)
        if abs(scene.person_azimuth_deg) > 18.0:
            rot_dir = "destra" if scene.person_azimuth_deg > 0 else "sinistra"
            rot_deg = round(min(90.0, max(5.0, abs(scene.person_azimuth_deg))), 1)
            ok_rot, cmd_rot, err_rot = self._validate_sandbox_step(
                sandbox,
                safety_ctx,
                "rotate_angle",
                {"direction": rot_dir, "degrees": rot_deg, "speed": 0.45},
            )
            if ok_rot and cmd_rot:
                actions.append(cmd_rot)
            elif err_rot:
                rejected.append(err_rot)

        delta_m = scene.person_distance_m - self.TARGET_PROXEMIC_M
        effort = MetabolicEffortTier.MICRO_PRESENCE

        if delta_m >= 0.10:
            step_m = round(min(1.50, delta_m), 2)
            ok_fwd, cmd_fwd, err_fwd = self._validate_sandbox_step(
                sandbox,
                safety_ctx,
                "move_distance",
                {"direction": "avanti", "distance_m": step_m, "speed": 0.14},
            )
            if ok_fwd and cmd_fwd:
                actions.append(cmd_fwd)
                effort = MetabolicEffortTier.PROXEMIC_STEP
            elif err_fwd:
                rejected.append(err_fwd)
        elif delta_m <= -0.10:
            back_m = round(min(0.40, abs(delta_m)), 2)
            if back_m >= 0.05:
                ok_bck, cmd_bck, err_bck = self._validate_sandbox_step(
                    sandbox,
                    safety_ctx,
                    "move_distance",
                    {"direction": "indietro", "distance_m": back_m, "speed": 0.10},
                )
                if ok_bck and cmd_bck:
                    actions.append(cmd_bck)
                    effort = MetabolicEffortTier.PROXEMIC_STEP
                elif err_bck:
                    rejected.append(err_bck)

        if follow_skill is not None and actions:
            follow_skill.start_following(target_identity=scene.person_name, mode="intimate_kneel")

        tail_deg, tail_hz = self._clamp_tail(28.0, 1.6)
        name = scene.person_name or "amico"
        return InteractionExecutionPlan(
            strategy_name=self.name,
            effort_tier=effort,
            speech=(
                f"Ciao {name}, visto che ti sei abbassato alla mia altezza mi avvicino piano fino a mezzo metro!"
            ),
            tail_angle_deg=tail_deg,
            tail_freq_hz=tail_hz,
            validated_actions=actions,
            rejected_reasons=rejected,
            follow_mode="intimate_kneel" if actions else None,
            follow_target_identity=name,
            metadata={"target_proxemic_distance_m": self.TARGET_PROXEMIC_M},
        )


class CommandedCompanionFollowStrategy(SocialInteractionStrategy):
    """Following esplicito su richiesta vocale o gesto di richiamo (`BECKON_COME`)."""

    name = "commanded_companion_follow"

    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        if max(scene.social_inhibition, drives.social_inhibition) >= 0.50:
            return 0.0
        if _FOLLOW_CMD_RE.search(text or ""):
            return 0.95
        if scene.gesture == GestureType.BECKON_COME.value:
            return 0.88
        return 0.0

    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        actions: List[Dict[str, Any]] = []
        rejected: List[str] = []

        name = scene.person_name or "Utente"
        if follow_skill is not None:
            follow_skill.start_following(target_identity=name, mode="companion")

        # Eventuale passo iniziale dolce se l'utente è > 1.35 m
        if scene.person_distance_m > 1.35:
            step_m = round(min(0.80, scene.person_distance_m - 1.20), 2)
            if step_m >= 0.05:
                ok_fwd, cmd_fwd, err_fwd = self._validate_sandbox_step(
                    sandbox,
                    safety_ctx,
                    "move_distance",
                    {"direction": "avanti", "distance_m": step_m, "speed": 0.18},
                )
                if ok_fwd and cmd_fwd:
                    actions.append(cmd_fwd)
                elif err_fwd:
                    rejected.append(err_fwd)

        tail_deg, tail_hz = self._clamp_tail(30.0, 1.8)
        return InteractionExecutionPlan(
            strategy_name=self.name,
            effort_tier=MetabolicEffortTier.LOCOMOTION_NAV,
            speech=f"Volentieri {name}, ti seguo mantenendo una distanza comoda e sicura.",
            tail_angle_deg=tail_deg,
            tail_freq_hz=tail_hz,
            validated_actions=actions,
            rejected_reasons=rejected,
            follow_mode="companion",
            follow_target_identity=name,
        )


class WaveGreetingMirrorStrategy(SocialInteractionStrategy):
    """
    Risponde al saluto (`WAVE_HELLO`) minimizzando l'energia motoria:
    - se l'utente è già nel cono frontale (|azimuth| <= 22 deg), ZERO moto ruote (`MICRO_PRESENCE`)
    - se è laterale, sola rotazione sul posto (`OCULOMOTOR_PIVOT`) senza avanzare.
    Integrazione naturale degli oggetti vicino alla mano dell'utente.
    """

    name = "wave_greeting_mirror"

    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        if max(scene.social_inhibition, drives.social_inhibition) >= 0.50:
            return 0.0
        if scene.gesture == GestureType.WAVE_HELLO.value:
            return 0.90
        return 0.0

    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        actions: List[Dict[str, Any]] = []
        rejected: List[str] = []
        effort = MetabolicEffortTier.MICRO_PRESENCE

        if abs(scene.person_azimuth_deg) > 22.0:
            rot_dir = "destra" if scene.person_azimuth_deg > 0 else "sinistra"
            rot_deg = round(min(60.0, abs(scene.person_azimuth_deg)), 1)
            ok_rot, cmd_rot, err_rot = self._validate_sandbox_step(
                sandbox,
                safety_ctx,
                "rotate_angle",
                {"direction": rot_dir, "degrees": rot_deg, "speed": 0.45},
            )
            if ok_rot and cmd_rot:
                actions.append(cmd_rot)
                effort = MetabolicEffortTier.OCULOMOTOR_PIVOT
            elif err_rot:
                rejected.append(err_rot)

        tail_deg, tail_hz = self._clamp_tail(35.0, 2.2)
        name = scene.person_name or "ciao"
        obj_hint = ""
        if scene.near_hand_objects:
            obj_hint = f" Noto vicino a te: {', '.join(scene.near_hand_objects[:2])}."
        return InteractionExecutionPlan(
            strategy_name=self.name,
            effort_tier=effort,
            speech=f"Ciao {name}! Ricambio il saluto scodinzolando senza consumare batteria inutilmente.{obj_hint}",
            tail_angle_deg=tail_deg,
            tail_freq_hz=tail_hz,
            validated_actions=actions,
            rejected_reasons=rejected,
        )


class CuriousAutonomousFollowStrategy(SocialInteractionStrategy):
    """
    Following curioso spontaneo: quando curiosità o slancio sociale positivo sono alti
    e ci sono oggetti interessanti o interazione attiva, segue discretamente per max 25s.
    """

    name = "curious_autonomous_follow"

    def matches(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        text: str = "",
    ) -> float:
        inh = max(scene.social_inhibition, drives.social_inhibition)
        if inh >= 0.35 or scene.tom_state == ToMState.DO_NOT_DISTURB.value:
            return 0.0
        curiosity_high = drives.curiosity >= 0.38
        warm_social = max(scene.social_momentum, drives.social_momentum) >= 0.40 and bool(
            scene.near_hand_objects or scene.pointing_target_object
        )
        if curiosity_high or warm_social:
            return 0.78
        return 0.0

    def plan(
        self,
        scene: SpatialPosturalSceneSnapshot,
        drives: HomeostaticDriveEngine,
        sandbox: TrinityKinematicSandbox,
        safety_ctx: RobotSafetyContext,
        text: str = "",
        follow_skill: Optional[FollowPersonSkill] = None,
    ) -> InteractionExecutionPlan:
        actions: List[Dict[str, Any]] = []
        rejected: List[str] = []
        name = scene.person_name or "Utente"

        if follow_skill is not None:
            follow_skill.start_following(
                target_identity=name,
                mode="curious_observer",
                max_duration_s=25.0,
            )

        if scene.person_distance_m > 1.65:
            step_m = round(min(0.60, scene.person_distance_m - 1.40), 2)
            if step_m >= 0.05:
                ok_fwd, cmd_fwd, err_fwd = self._validate_sandbox_step(
                    sandbox,
                    safety_ctx,
                    "move_distance",
                    {"direction": "avanti", "distance_m": step_m, "speed": 0.16},
                )
                if ok_fwd and cmd_fwd:
                    actions.append(cmd_fwd)
                elif err_fwd:
                    rejected.append(err_fwd)

        obj_focus = scene.pointing_target_object or (
            scene.near_hand_objects[0] if scene.near_hand_objects else "quello che stai facendo"
        )
        tail_deg, tail_hz = self._clamp_tail(22.0, 1.4)
        return InteractionExecutionPlan(
            strategy_name=self.name,
            effort_tier=MetabolicEffortTier.PROXEMIC_STEP if actions else MetabolicEffortTier.MICRO_PRESENCE,
            speech=f"Mi incuriosisce {obj_focus}! Ti osservo da vicino per un attimo senza disturbarti.",
            tail_angle_deg=tail_deg,
            tail_freq_hz=tail_hz,
            validated_actions=actions,
            rejected_reasons=rejected,
            follow_mode="curious_observer",
            follow_target_identity=name,
            metadata={"max_duration_s": 25.0, "object_focus": obj_focus},
        )


class EmbodiedSocialInteractionSkill(BaseSkill):
    """
    Skill unificata che orchestra:
    - Costruzione o ricezione di `SpatialPosturalSceneSnapshot`
    - Deliberazione tramite Qwen 2.5 locale (`SLMMetapromptFusion` + `HailoQwenClient`)
    - Arbitraggio polimorfico Strategy + pre-flight validation su `TrinityKinematicSandbox`
    - Isteresi neuro-sociale (`social_momentum` vs `social_inhibition`)
    """

    def __init__(
        self,
        qwen_client: Optional[HailoQwenClient] = None,
        sandbox: Optional[TrinityKinematicSandbox] = None,
        drive_engine: Optional[HomeostaticDriveEngine] = None,
        dopamine_engine: Optional[Any] = None,
        follow_skill: Optional[FollowPersonSkill] = None,
        dialogue_arbiter: Optional[Any] = None,
        slm_fusion: Optional[SLMMetapromptFusion] = None,
        strategies: Optional[List[SocialInteractionStrategy]] = None,
        scene_builder: Optional[SpatialPosturalSceneBuilder] = None,
    ):
        super().__init__()
        self.qwen_client = qwen_client or HailoQwenClient()
        self.sandbox = sandbox or TrinityKinematicSandbox()
        if drive_engine is not None:
            self.drive_engine = drive_engine
        elif dopamine_engine is not None and hasattr(dopamine_engine, "drive_engine"):
            self.drive_engine = dopamine_engine.drive_engine
        else:
            self.drive_engine = HomeostaticDriveEngine()
        self.dopamine_engine = dopamine_engine
        self.follow_skill = follow_skill or FollowPersonSkill()
        self.dialogue_arbiter = dialogue_arbiter
        self.slm_fusion = slm_fusion or SLMMetapromptFusion()
        self.scene_builder = scene_builder or SpatialPosturalSceneBuilder()
        self.strategies: List[SocialInteractionStrategy] = strategies or [
            InhibitedRetreatToDockStrategy(),
            CommandedCompanionFollowStrategy(),
            KneelingEyeLevelApproachStrategy(),
            WaveGreetingMirrorStrategy(),
            CuriousAutonomousFollowStrategy(),
        ]

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="embodied_social_interaction_skill",
            description=(
                "Interazione sociale multimodale posa+oggetti+mappa con deliberazione Qwen 2.5 "
                "su Hailo-10H, strategie polimorfiche a minima energia e isteresi sociale."
            ),
            version="1.0.0",
            author="Marcus Team",
            keywords=[
                "saluto", "inginocchiato", "seguimi", "curioso", "zitto",
                "taci", "silenzio", "cuccia", "posa",
            ],
            priority=9,
            requires_nav=True,
            requires_vision=True,
            capabilities=[Capability.CAMERA_READ],
        )

    @property
    def metadata(self) -> SkillMetadata:
        return self.get_metadata()

    def match(self, text: str, context: Optional[Dict[str, Any]] = None) -> float:
        ctx = context or {}
        if ctx.get("scene") is not None:
            return 0.96
        if _SHUSH_RE.search(text or "") or _FOLLOW_CMD_RE.search(text or ""):
            return 0.90
        if _POSITIVE_DIALOGUE_RE.search(text or ""):
            return 0.65
        return 0.0

    def select_strategy(
        self,
        scene: SpatialPosturalSceneSnapshot,
        text: str = "",
    ) -> Optional[SocialInteractionStrategy]:
        best_strategy: Optional[SocialInteractionStrategy] = None
        best_score = 0.0
        for strategy in self.strategies:
            score = strategy.matches(scene, self.drive_engine, text=text)
            if score > best_score:
                best_score = score
                best_strategy = strategy
        return best_strategy if best_score > 0.0 else None

    def plan_interaction(
        self,
        scene: SpatialPosturalSceneSnapshot,
        text: str = "",
        safety_ctx: Optional[RobotSafetyContext] = None,
    ) -> Optional[InteractionExecutionPlan]:
        strategy = self.select_strategy(scene, text=text)
        if strategy is None:
            return None
        ctx = safety_ctx or RobotSafetyContext(min_forward_obstacle_dist=max(0.35, scene.person_distance_m))
        return strategy.plan(
            scene=scene,
            drives=self.drive_engine,
            sandbox=self.sandbox,
            safety_ctx=ctx,
            text=text,
            follow_skill=self.follow_skill,
        )

    def _update_neuro_social_state(
        self,
        scene: SpatialPosturalSceneSnapshot,
        text: str,
    ) -> Optional[ProactiveEvent]:
        clean_text = (text or "").strip()
        shush_event: Optional[ProactiveEvent] = None

        if _SHUSH_RE.search(clean_text):
            reason = "go_to_dock" if "cuccia" in clean_text.lower() or "base" in clean_text.lower() else "user_shushed"
            shush_event = self.drive_engine.notify_user_shush(severity=0.45, reason=reason)
            if self.dialogue_arbiter and hasattr(self.dialogue_arbiter, "record_user_shush"):
                self.dialogue_arbiter.record_user_shush(
                    inhibition=self.drive_engine.social_inhibition,
                    momentum=self.drive_engine.social_momentum,
                )
        elif clean_text and (_POSITIVE_DIALOGUE_RE.search(clean_text) or len(clean_text) >= 4):
            self.drive_engine.notify_positive_dialogue(intensity=0.25)
            if self.dialogue_arbiter and hasattr(self.dialogue_arbiter, "record_positive_dialogue"):
                self.dialogue_arbiter.record_positive_dialogue(
                    momentum=self.drive_engine.social_momentum,
                    inhibition=self.drive_engine.social_inhibition,
                )
        elif scene.gesture == GestureType.OPEN_PALM.value:
            shush_event = self.drive_engine.notify_user_shush(severity=0.35, reason="open_palm_stop")
        elif scene.gesture == GestureType.WAVE_HELLO.value:
            self.drive_engine.notify_positive_dialogue(intensity=0.20)

        scene.social_momentum = self.drive_engine.social_momentum
        scene.social_inhibition = self.drive_engine.social_inhibition
        return shush_event

    async def deliberate_and_execute_with_qwen(
        self,
        scene: SpatialPosturalSceneSnapshot,
        user_text: str = "",
        safety_ctx: Optional[RobotSafetyContext] = None,
        cag_env: Optional[EnvironmentSnapshot] = None,
        system_prompt: str = "",
    ) -> SkillResult:
        """
        Flusso principale:
        1. Aggiorna isteresi neuro-sociale (`social_momentum` / `social_inhibition`)
        2. Costruisce il contesto compatto su `SLMMetapromptFusion` (<1150 token)
        3. Interroga Qwen 2.5 locale (`HailoQwenClient`) per il ragionamento situazionale
        4. Estrae eventuale `<action>` JSON e lo valida sul `TrinityKinematicSandbox`
        5. Arbitra con la Strategy polimorfica selezionata (es. 0.50m kneeling, wave, retreat to dock)
        """
        t0 = time.perf_counter()
        self._update_neuro_social_state(scene, user_text)

        if cag_env is not None:
            env = cag_env
        else:
            env = EnvironmentSnapshot()
            env.update_location(
                room_name=scene.map_room,
                location=scene.map_pose_xy or (0.0, 0.0),
            )
        env.update_postural_scene(scene.to_compact_cag_string())
        cag_env_text = env.to_text()

        effective_user_text = (user_text or "").strip()
        if not effective_user_text:
            effective_user_text = (
                f"Osserva la scena posturale attuale ({scene.tom_state}, gesto {scene.gesture}) "
                "e decidi la risposta piu naturale ed efficiente in energia."
            )

        sys_prompt = system_prompt or (
            "Sei Marcus, robot sociale su Raspberry Pi 5. Rispondi in italiano naturale e breve, "
            "rispettando il contesto visivo PoseScene e minimizzando lo sforzo motorio. "
            "Se ritieni necessario un movimento sicuro, aggiungi un blocco <action> JSON valido."
        )
        slm_ctx = SLMPromptContext(
            user_text=effective_user_text,
            system_prompt=sys_prompt,
            cag_environment=cag_env_text,
            intent_category="SOCIAL",
        )
        prompt_out = self.slm_fusion.build_prompt_detailed(slm_ctx)

        qwen_raw = ""
        qwen_speech = ""
        qwen_actions: List[Dict[str, Any]] = []
        rejected: List[str] = []

        eff_safety = safety_ctx or RobotSafetyContext(
            min_forward_obstacle_dist=max(0.40, scene.person_distance_m),
            min_rear_obstacle_dist=1.50,
            min_clearance_radius_360=0.80,
        )

        try:
            qwen_raw, _telem = await self.qwen_client.generate_with_fallback(
                prompt=prompt_out.chatml_prompt,
                system_prompt=prompt_out.system_block,
            )
            extracted = JsonCallExtractor.extract_hybrid_response(qwen_raw)
            qwen_speech = extracted.clean_speech or ""
            if extracted.action_call is not None:
                fn = extracted.action_call.function_name
                args = dict(extracted.action_call.arguments or {})
                # Salvaguardia prossemica a 0.50m per KNEELING_EYE_LEVEL
                if (
                    scene.tom_state == ToMState.KNEELING_EYE_LEVEL.value
                    and fn == "move_distance"
                    and str(args.get("direction", "")).lower() == "avanti"
                ):
                    max_safe_step = max(0.05, round(scene.person_distance_m - 0.50, 2))
                    if float(args.get("distance_m", 0.0)) > max_safe_step:
                        args["distance_m"] = max_safe_step

                val = self.sandbox.validate_action(
                    {"call": {"name": fn, "arguments": args}},
                    eff_safety,
                )
                if val.authorized:
                    qwen_actions.append(
                        {
                            "function_name": fn,
                            "arguments": dict(val.clamped_arguments or args),
                            "source": "qwen_local_hailo",
                            "sandbox_authorized": True,
                        }
                    )
                else:
                    rejected.append(f"qwen_{fn}: {val.error_code.value}")
        except Exception as exc:
            logger.warning("Fallback deliberazione Qwen su strategy engine: %s", exc)

        strategy_plan = self.plan_interaction(scene, text=user_text, safety_ctx=eff_safety)

        # Arbitraggio finale fra Qwen e Strategy polimorfica
        final_actions: List[Dict[str, Any]] = []
        final_speech = qwen_speech
        effort_tier = scene.recommended_effort_tier.value
        strategy_name = strategy_plan.strategy_name if strategy_plan else "qwen_direct_social"
        tail_deg = 15.0 if self.drive_engine.social_momentum >= 0.30 else 0.0
        tail_hz = 1.2 if self.drive_engine.social_momentum >= 0.30 else 0.0
        follow_mode = None

        if strategy_plan is not None:
            effort_tier = strategy_plan.effort_tier.value
            tail_deg = strategy_plan.tail_angle_deg
            tail_hz = strategy_plan.tail_freq_hz
            follow_mode = strategy_plan.follow_mode
            rejected.extend(strategy_plan.rejected_reasons)

            if strategy_plan.strategy_name == "inhibited_retreat_to_dock":
                # Richiede rigorosamente la doppia fase arretramento + base
                final_actions = list(strategy_plan.validated_actions)
                if not final_speech:
                    final_speech = strategy_plan.speech
            elif qwen_actions:
                final_actions = qwen_actions
            else:
                final_actions = list(strategy_plan.validated_actions)

            if not final_speech:
                final_speech = strategy_plan.speech
        else:
            final_actions = qwen_actions
            if not final_speech:
                final_speech = "Ti osservo e ti ascolto con attenzione!"

        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return SkillResult.success(
            message=final_speech,
            speak=final_speech,
            actions=final_actions,
            data={
                "strategy": strategy_name,
                "effort_tier": effort_tier,
                "tail_angle_deg": tail_deg,
                "tail_freq_hz": tail_hz,
                "follow_mode": follow_mode,
                "social_momentum": round(self.drive_engine.social_momentum, 3),
                "social_inhibition": round(self.drive_engine.social_inhibition, 3),
                "slm_token_estimate": prompt_out.token_estimate,
                "slm_within_budget": prompt_out.is_within_budget,
                "qwen_raw": qwen_raw,
                "rejected_actions": rejected,
                "pose_scene_cag": scene.to_compact_cag_string(),
            },
            duration_ms=elapsed_ms,
        )

    async def execute(
        self,
        text_or_params: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        if isinstance(text_or_params, dict):
            params = dict(text_or_params)
            text = str(params.get("text", ""))
        else:
            text = str(text_or_params or "")
            params = dict(context or {})

        scene = params.get("scene")
        if scene is None:
            keypoints = params.get("keypoints")
            detected_objects = params.get("detected_objects") or []
            scene = self.scene_builder.build_scene(
                keypoints=keypoints,
                detected_objects=detected_objects,
                person_name=str(params.get("person_name") or params.get("user_name") or "Utente"),
                person_distance_m=float(params.get("person_distance_m", 1.5)),
                tom_state=str(params.get("tom_state", ToMState.ENGAGED.value)),
                gesture=str(params.get("gesture", GestureType.NONE.value)),
                gaze_at_robot=bool(params.get("gaze_at_robot", True)),
                map_room=str(params.get("map_room", "Salotto")),
                social_momentum=self.drive_engine.social_momentum,
                social_inhibition=self.drive_engine.social_inhibition,
            )

        if not isinstance(scene, SpatialPosturalSceneSnapshot):
            return SkillResult.failure(
                error_code=SkillErrorCode.INVALID_PARAMETERS,
                error_message="Contesto scenico posturale non valido per EmbodiedSocialInteractionSkill.",
            )

        return await self.deliberate_and_execute_with_qwen(
            scene=scene,
            user_text=text,
            safety_ctx=params.get("safety_ctx"),
            cag_env=params.get("cag_env"),
            system_prompt=str(params.get("system_prompt", "")),
        )
