#!/usr/bin/env python3
"""
calculate_and_report_fmea.py
----------------------------
Script deterministico per l'infrastruttura FMEA-Lite Evoluto (AIAG-VDA Standard) di Marcus.
Ricalcola gli RPN iniziali e residui, applica la Regola Override Severità, aggiorna `dfmea.yaml`
e genera un report esecutivo in Markdown `FMEA_EXECUTIVE_REPORT.md`.
"""

import sys
import os
from datetime import datetime
try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML non trovato. Installare con `pip install pyyaml` o `apt install python3-yaml`")
    sys.exit(1)

FMEA_DIR = os.path.dirname(os.path.abspath(__file__))
DFMEA_PATH = os.path.join(FMEA_DIR, "dfmea.yaml")
REPORT_PATH = os.path.join(FMEA_DIR, "FMEA_EXECUTIVE_REPORT.md")

def calculate_rpn(severity, occurrence, detection):
    return int(severity) * int(occurrence) * int(detection)

def determine_risk_level(s_init, s_res, rpn_res):
    # REGOLA OVERRIDE SEVERITÀ: Qualsiasi guasto con S_init >= 9 o S_res >= 9 -> REVISION_MANDATORY
    if s_init >= 9 or s_res >= 9:
        return "REVISION_MANDATORY"
    elif rpn_res <= 50:
        return "LOW"
    elif rpn_res <= 199:
        return "MEDIUM"
    elif rpn_res <= 349:
        return "HIGH"
    else:
        return "CRITICAL"

def process_fmea():
    if not os.path.exists(DFMEA_PATH):
        print(f"[ERROR] Impossibile trovare il file DFMEA: {DFMEA_PATH}")
        sys.exit(1)

    with open(DFMEA_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        print("[ERROR] Il file dfmea.yaml deve contenere una lista YAML di Failure Mode.")
        sys.exit(1)

    stats = {
        "total": len(data),
        "subsystems": {},
        "risk_levels": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "REVISION_MANDATORY": 0},
        "classifications": {"SAFETY": 0, "HW_DAMAGE": 0, "MISSION_CRITICAL": 0, "PERFORMANCE": 0},
        "mitigation_status": {"OPEN": 0, "IN_PROGRESS": 0, "MITIGATED": 0, "CLOSED": 0}
    }

    processed_entries = []

    for entry in data:
        entry_id = entry.get("id", "UNKNOWN")
        subsys = entry.get("subsystem", "General")
        classification = entry.get("classification", "PERFORMANCE")
        status = entry.get("mitigation_status", "OPEN")

        stats["subsystems"][subsys] = stats["subsystems"].get(subsys, 0) + 1
        stats["classifications"][classification] = stats["classifications"].get(classification, 0) + 1
        stats["mitigation_status"][status] = stats["mitigation_status"].get(status, 0) + 1

        # Ricalcolo RPN Iniziale
        init = entry.get("initial_scoring", {})
        s_init = int(init.get("severity", 1))
        o_init = int(init.get("occurrence", 1))
        d_init = int(init.get("detection", 1))
        rpn_init = calculate_rpn(s_init, o_init, d_init)
        init["rpn"] = rpn_init
        entry["initial_scoring"] = init

        # Ricalcolo RPN Residuo
        res = entry.get("residual_scoring", {})
        s_res = int(res.get("severity", 1))
        o_res = int(res.get("occurrence", 1))
        d_res = int(res.get("detection", 1))
        rpn_res = calculate_rpn(s_res, o_res, d_res)
        res["rpn"] = rpn_res
        entry["residual_scoring"] = res

        # Determinazione Livello di Rischio con Override Severità
        risk = determine_risk_level(s_init, s_res, rpn_res)
        entry["risk_level"] = risk
        stats["risk_levels"][risk] = stats["risk_levels"].get(risk, 0) + 1

        # Gestione History
        history = entry.get("history", [])
        if not history:
            history.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "change": "Inizializzazione voce DFMEA",
                "rpn_old": None,
                "rpn_new": rpn_res
            })
        entry["history"] = history

        processed_entries.append(entry)

    # Scrittura DB YAML aggiornato
    with open(DFMEA_PATH, "w", encoding="utf-8") as f:
        yaml.dump(processed_entries, f, sort_keys=False, allow_unicode=True, indent=2)

    # Generazione Report Markdown
    generate_markdown_report(processed_entries, stats)
    print(f"[SUCCESS] Processati {stats['total']} Failure Mode. Database DFMEA e Report Esecutivo aggiornati.")

def generate_markdown_report(entries, stats):
    sorted_entries = sorted(entries, key=lambda x: x["residual_scoring"]["rpn"], reverse=True)

    report_content = f"""# 📊 Report Esecutivo DFMEA - Marcus AI Robot Platform
**Data Generazione:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Metodologia:** AIAG-VDA FMEA Standard con Regola Override Severità ($S \\ge 9 \\implies$ REVISION_MANDATORY)

---

## 📈 Sintesi Statistica Rischio

| Metrica | Valore | Note / Impatto |
| :--- | :---: | :--- |
| **Totale Modalità di Guasto (FM)** | **{stats['total']}** | Copertura integrata dei sottosistemi Marcus |
| **🟢 Risk Level LOW** | **{stats['risk_levels']['LOW']}** | $RPN_{{res}} \\le 50$ (Sotto controllo) |
| **🟡 Risk Level MEDIUM** | **{stats['risk_levels']['MEDIUM']}** | $51 \\le RPN_{{res}} \\le 199$ (Monitoraggio attivo) |
| **🟠 Risk Level HIGH** | **{stats['risk_levels']['HIGH']}** | $200 \\le RPN_{{res}} \\le 349$ (Mitigazione obbligatoria) |
| **🔴 Risk Level CRITICAL** | **{stats['risk_levels']['CRITICAL']}** | $RPN_{{res}} \\ge 350$ (Blocco rilasci) |
| **🚨 REVISION_MANDATORY** | **{stats['risk_levels']['REVISION_MANDATORY']}** | **Override Severità ($S \\ge 9$)** - Massima Priorità Ingegneristica |

### Ripartizione per Sottosistema:
"""
    for sub, count in stats['subsystems'].items():
        report_content += f"- **{sub}:** {count} failure modes\n"

    report_content += """\n---

## 🚨 Modalità di Guasto ad Alta Priorità (REVISION_MANDATORY / HIGH / CRITICAL)

| ID FM | Sottosistema | Componente | Modo di Guasto | S_init ➔ S_res | RPN_init ➔ RPN_res | Livello Rischio | Stato Mitigazione | ECO Ref |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
"""

    high_priority = [e for e in sorted_entries if e["risk_level"] in ["REVISION_MANDATORY", "CRITICAL", "HIGH"]]
    for e in high_priority:
        eco = f"[`{e['eco_ref']}`]({e['eco_ref']})" if e.get('eco_ref') else "N/A"
        report_content += f"| **{e['id']}** | {e['subsystem']} | `{e['component']}` | {e['failure_mode']} | {e['initial_scoring']['severity']} ➔ {e['residual_scoring']['severity']} | {e['initial_scoring']['rpn']} ➔ **{e['residual_scoring']['rpn']}** | `{e['risk_level']}` | `{e['mitigation_status']}` | {eco} |\n"

    report_content += """\n---

## 📋 Registro Completo Failure Modes (Ordinato per RPN Residuo Decrescente)

| ID FM | Sottosistema | Componente | Modo di Guasto | S_res | O_res | D_res | RPN Residuo | Livello Rischio | Stato | Lesson Ref |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
"""

    for e in sorted_entries:
        lesson = f"[`{e['lesson_ref']}`]({e['lesson_ref']})" if e.get('lesson_ref') else "N/A"
        report_content += f"| **{e['id']}** | {e['subsystem']} | `{e['component']}` | {e['failure_mode']} | {e['residual_scoring']['severity']} | {e['residual_scoring']['occurrence']} | {e['residual_scoring']['detection']} | **{e['residual_scoring']['rpn']}** | `{e['risk_level']}` | `{e['mitigation_status']}` | {lesson} |\n"

    report_content += """\n---

## 🔒 Protocollo Anti-Regressione & Regola Operativa per lo Sviluppatore AI

Per ogni futura modifica al codice di Marcus, l'agente di sviluppo DEVI attenersi al seguente ciclo ad anello chiuso:
1. **Consultazione DFMEA:** Prima di modificare un nodo ROS 2 o uno script, ispezionare `fmea/dfmea.yaml` per individuare i guasti correlati.
2. **Aggiornamento o Creazione Entry:** Se la modifica introduce un nuovo potenziale guasto o ne mitiga uno esistente, aggiornare il punteggio `residual_scoring` ed aggiungere un elemento in `history`.
3. **Esecuzione Ricalcolo:** Eseguire `python fmea/calculate_and_report_fmea.py` per sincronizzare RPN e report.
4. **Verifica Override:** Assicurarsi che nessuna voce con $S \\ge 9$ rimanga con `mitigation_status: OPEN` senza una misura di contenimento architetturale testata e registrata nel corrispondente file ECO under `docs/ecos/`.
"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_content)

if __name__ == "__main__":
    process_fmea()
