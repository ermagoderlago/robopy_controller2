# Progetto di Miglioramento: IMP-BIO-001 (Hailo VDevice Arbitration & Yield Service)

- **Failure Mode Correlato:** `FM-BIO-001`
- **Sottosistema:** Vision/Hailo
- **Classificazione:** MISSION_CRITICAL
- **Stato:** COMPLETED

## Descrizione del Problema
Sul Raspberry Pi 5 con Hailo-10H, `hailo_bridge_node_cpp` detiene il device PCIe `/dev/hailo0`. L'invocazione di `hailo-ollama` (Qwen2-VL / Qwen2.5) per compiti VQA locali generava `HAILO_OUT_OF_PHYSICAL_DEVICES (status=74)` a causa della contesa del dispositivo fisico in assenza di rilascio esplicito.

## Soluzione Architetturale
1. Implementato il servizio ROS 2 `/hailo_bridge/yield_vdevice` (`std_srvs/srv/SetBool`) in `hailo_bridge_node_cpp`.
2. Quando il Governor richiede l'uso del VLM locale offline (solo in stato `STATIONARY` per Invariante 8), invoca `yield_vdevice(True)`.
3. Il nodo C++ rilascia in sicurezza il `VDevice` mantenendo in cache l'ultima posa/identità.
4. Al termine del VLM (o su arrivo di un intento di navigazione `PREP_NAV` con Break-Before-Make), il Governor chiama `yield_vdevice(False)` riagganciando la visione su Hailo-10H.
