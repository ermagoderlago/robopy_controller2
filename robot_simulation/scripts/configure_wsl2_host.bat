@echo off
REM ==============================================================================
REM Marcus AI - Configurazione Host WSL2 (Ubuntu 24.04) + ROS 2 Jazzy + Gazebo
REM Ottimizzato per PC AMD Ryzen 5 3500U (RAM cap 4500MB, networkingMode=mirrored)
REM ==============================================================================

echo ================================================================
echo  MARCUS DIGITAL TWIN - SETUP WSL2 E GAZEBO HARMONIC
echo ================================================================

echo [1/2] Verifica file %USERPROFILE%\.wslconfig ...
if exist "%USERPROFILE%\.wslconfig" (
    echo [OK] File .wslconfig presente:
    type "%USERPROFILE%\.wslconfig"
) else (
    (
        echo [wsl2]
        echo memory=4500MB
        echo processors=6
        echo swap=4GB
        echo localhostForwarding=true
        echo networkingMode=mirrored
        echo guiApplications=true
        echo pageReporting=true
    ) > "%USERPROFILE%\.wslconfig"
    echo [OK] Creato %USERPROFILE%\.wslconfig con limite 4500MB RAM.
)

echo.
echo [2/2] Per installare/avviare Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic:
echo   1. Apri PowerShell o Prompt dei Comandi come Amministratore:
echo      wsl --install -d Ubuntu-24.04
echo   2. Riavvia Windows se richiesto per abilitare il kernel WSL2.
echo   3. Dentro Ubuntu WSL2 esegui:
echo      sudo bash /mnt/d/LUCA/robopy/robot_simulation/scripts/install_deps.sh /mnt/d/LUCA/robopy
echo ================================================================
