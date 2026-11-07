@echo off
set PYTHONIOENCODING=utf-8
cd /d "d:\LUCA\robopy"
echo ============================================================================
echo [1/2] ESECUZIONE SUITE DI VALIDAZIONE PYTEST (24 TEST AUTOMATICI)
echo ============================================================================
"%USERPROFILE%\.local\bin\uv.exe" run --python 3.11 --with pytest --with numpy --with pyyaml --with httpx --with pydantic python -m pytest test/sim/ -v
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo.
echo ============================================================================
echo [2/2] ESECUZIONE MISSIONE AUTONOMA CLOSED-LOOP IN SMART HOME 3D
echo ============================================================================
"%USERPROFILE%\.local\bin\uv.exe" run --python 3.11 --with numpy --with pyyaml --with httpx python robot_simulation/scripts/run_closed_loop_mission_sim.py
