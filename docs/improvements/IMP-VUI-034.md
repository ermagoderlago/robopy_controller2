# Improvement VUI-034: VAD permissivo mitigato da _is_noise_transcription

- **Scopo:** Bilanciamento della threshold del microfono
- **Modifiche fatte:** in respeaker_vui_node.py ridotti base_clamp a 150/250 e ambient_multiplier a 1.05/1.10. Ridotto multiplier_adaptive a 1.1x.
- **Risultato:** Il VAD si apre correttamente con la voce umana. Il rumore accidentale che supera questa soglia bassa viene ignorato dal LLM grazie al filtro token aggiunto in precedenza.
