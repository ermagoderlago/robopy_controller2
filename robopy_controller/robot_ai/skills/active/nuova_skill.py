# =============================================================================
# SKILL: NuovaSkill
# Generata il:        2026-09-05T20:15:45.023825
# Iterazione:         1/3
# Versione prompt:    MARCUS_PROMPT_v2.1
# Hash contesto RAK:  sha256:e3b0c44298fc1c14
# Capability:         []
# Topic usati:        SUB=[] PUB=[]
# Stato:              STAGING
# =============================================================================

from robopy_controller.robot_ai.skills.base_skill import (
    BaseSkill,
    SkillMetadata,
    SkillResult,
    SkillErrorCode,
    Capability,
)
from typing import Any, Dict, List, Optional
import asyncio
import logging
import re

logger = logging.getLogger(__name__)


class NuovaSkill(BaseSkill):
    """
    Skill per il calcolo del consumo energetico residuo e della stima
    dell'autonomia operativa su Raspberry Pi 5 e architettura robotica.
    """

    # Valori nominali di default (pacco batteria standard 3S LiFePO4 / Li-Ion o PowerBank 5V/USB-PD)
    DEFAULT_BATTERY_CAPACITY_MAH: float = 5000.0
    DEFAULT_BATTERY_VOLTAGE_V: float = 11.1
    DEFAULT_BASE_POWER_DRAW_W: float = 6.5  # Raspberry Pi 5 idle + periferiche base

    def __init__(self):
        super().__init__()

    def get_metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="consumo_energetico_residuo",
            description="Calcola il consumo energetico residuo e l'autonomia stimata del robot illustrando i passaggi.",
            version="1.0.0",
            author="Marcus AI",
            keywords=[
                "consumo energetico residuo",
                "consumo energetico",
                "consumo residuo",
                "calcola consumo",
                "energia residua",
                "autonomia batteria",
                "stato batteria",
                "autonomia residua",
            ],
            priority=5,
            enabled=True,
            requires_internet=False,
            requires_ha=False,
            requires_nav=False,
            requires_vision=False,
            capabilities=[],
        )

    def match(self, text: str, context: Optional[Dict[str, Any]] = None) -> float:
        if not text:
            return 0.0

        text_lower = text.lower().strip()

        # Match esatto o parziale per frasi chiave
        key_phrases = [
            "consumo energetico residuo",
            "calcolare il consumo energetico",
            "calcola il consumo energetico",
            "consumo residuo",
            "energia residua",
            "autonomia residua",
        ]
        for phrase in key_phrases:
            if phrase in text_lower:
                return 0.95

        # Calcolo score basato su token
        tokens = ["consumo", "energetico", "residuo", "batteria", "autonomia", "energia"]
        matched_tokens = sum(1 for token in tokens if token in text_lower)

        if matched_tokens >= 3:
            return 0.85
        elif matched_tokens == 2:
            return 0.60
        elif matched_tokens == 1:
            return 0.30

        return 0.0

    async def execute(self, text: str, context: Optional[Dict[str, Any]] = None) -> SkillResult:
        logger.info("Avvio calcolo consumo energetico residuo...")
        context = context or {}

        try:
            # Simulazione I/O asincrono per polling telemetria hardware senza bloccare l'event loop
            await asyncio.sleep(0.01)

            # Estrazione parametri con fallback sicuri
            capacity_mah = float(context.get("battery_capacity_mah", self.DEFAULT_BATTERY_CAPACITY_MAH))
            voltage_v = float(context.get("battery_voltage_v", self.DEFAULT_BATTERY_VOLTAGE_V))
            current_soc_percent = float(context.get("battery_soc_percent", 75.0))
            active_power_draw_w = float(context.get("current_power_draw_w", self.DEFAULT_BASE_POWER_DRAW_W))

            # Validazione parametri
            if capacity_mah <= 0 or voltage_v <= 0 or active_power_draw_w <= 0:
                logger.error("Parametri di calcolo energetico non validi.")
                return SkillResult.failure_result(
                    message="Parametri telemetrici batteria non validi per il calcolo.",
                    error_code=SkillErrorCode.INVALID_PARAMETERS,
                    speak="Impossibile completare il calcolo energetico a causa di parametri telemetrici non validi.",
                )

            current_soc_percent = max(0.0, min(100.0, current_soc_percent))

            # Esecuzione passaggi di calcolo
            passaggi: List[str] = []

            # Passaggio 1: Calcolo energia nominale totale
            total_energy_wh = (capacity_mah * voltage_v) / 1000.0
            passaggi.append(
                f"Passaggio 1: Calcolo energia nominale totale del sistema: "
                f"({capacity_mah:.0f} mAh * {voltage_v:.1f} V) / 1000 = {total_energy_wh:.2f} Wh."
            )

            # Passaggio 2: Calcolo energia residua disponibile
            residual_energy_wh = total_energy_wh * (current_soc_percent / 100.0)
            passaggi.append(
                f"Passaggio 2: Valutazione stato di carica residuo ({current_soc_percent:.1f}%): "
                f"Energia residua disponibile = {residual_energy_wh:.2f} Wh."
            )

            # Passaggio 3: Valutazione assorbimento attuale e autonomia stimata
            residual_hours = residual_energy_wh / active_power_draw_w
            residual_minutes = residual_hours * 60.0
            passaggi.append(
                f"Passaggio 3: Calcolo autonomia in base all'assorbimento istantaneo ({active_power_draw_w:.2f} W): "
                f"Autonomia residua = {residual_hours:.2f} ore (~{residual_minutes:.0f} minuti)."
            )

            # Passaggio 4: Margine di sicurezza
            usable_minutes = max(0.0, residual_minutes * 0.85)  # 15% riserva di sicurezza
            passaggi.append(
                f"Passaggio 4: Applicazione riserva di salvaguardia del 15%: "
                f"Tempo operativo effettivo stimato = {usable_minutes:.0f} minuti."
            )

            logger.info("Calcolo energetico completato con successo.")

            passaggi_str = "\n".join(passaggi)
            speak_output = (
                f"Ho calcolato il consumo energetico residuo. Ecco i passaggi:\n"
                f"1. Capacità totale stimata a {total_energy_wh:.2f} Wattora.\n"
                f"2. Con carica al {current_soc_percent:.0f}%, restano {residual_energy_wh:.2f} Wattora.\n"
                f"3. All'assorbimento attuale di {active_power_draw_w:.1f} Watt, l'autonomia teorica è di {residual_minutes:.0f} minuti.\n"
                f"4. Considerando un margine di riserva, l'autonomia sicura è di circa {usable_minutes:.0f} minuti.\n"
                f"Ho finito."
            )

            data_payload = {
                "capacity_mah": capacity_mah,
                "voltage_v": voltage_v,
                "soc_percent": current_soc_percent,
                "power_draw_w": active_power_draw_w,
                "total_energy_wh": round(total_energy_wh, 2),
                "residual_energy_wh": round(residual_energy_wh, 2),
                "theoretical_minutes": round(residual_minutes, 1),
                "safe_operating_minutes": round(usable_minutes, 1),
                "steps": passaggi,
            }

            return SkillResult.success_result(
                message=f"Calcolo completato con successo.\n{passaggi_str}\nHo finito.",
                data=data_payload,
                actions=[],
                speak=speak_output,
            )

        except Exception as exc:
            logger.exception("Errore imprevisto durante il calcolo energetico: %s", exc)
            return SkillResult.failure_result(
                message=f"Errore interno durante il calcolo: {str(exc)}",
                error_code=SkillErrorCode.UNKNOWN_ERROR,
                speak="Si è verificato un errore imprevisto durante il calcolo del consumo energetico.",
            )

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "battery_soc_percent": {
                    "type": "number",
                    "description": "Percentuale corrente della batteria (0-100).",
                },
                "current_power_draw_w": {
                    "type": "number",
                    "description": "Potenza assorbita istantanea in Watt.",
                },
            },
            "required": [],
        }