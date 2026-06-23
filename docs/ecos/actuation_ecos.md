# Engineering Change Orders - Attuazione & Controllo

Questo documento raccoglie la cronologia delle modifiche ingegneristiche (ECO) apportate ai sistemi di attuazione, cinematica differenziale e controllo motori di Marcus.

---

## 📈 ECO-2026-06-03-001: Waveshare General Driver (ESP32) Integration
* **Stato:** ✅ **Completato, Sincronizzato e Compilato**
* **Descrizione:** Progettazione ed implementazione di un nuovo nodo ROS 2 standalone in Python chiamato `waveshare_motor_driver` per il controllo a basso livello e odometria della nuova scheda Waveshare General Driver (ESP32) via seriale USB.
* **Modifiche apportate:**
  * Creato `waveshare_motor_driver.py` (comunicazione seriale JSON con ESP32, sottoscrizione `/cmd_vel` ed invio `{"T": 1, "L": v_L, "R": v_R}`, lettura feedback tick encoder, calcolo ed integrazione geometrica odometria con pubblicazione su `/odom` e TF `odom ➔ base_link`, watchdog di stop a 500ms).
  * Creato lo script wrapper `scripts/waveshare_motor_driver` per l'avvio del nodo.
  * Aggiunto l'entry point `waveshare_motor_driver` in `setup.py` e lo script wrapper in `CMakeLists.txt` per l'installazione in `lib/${PROJECT_NAME}`.
