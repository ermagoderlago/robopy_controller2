#!/usr/bin/env python3
import pty
import os
import sys
import select
import subprocess

def run_with_password(cmd_args, password):
    """Esegue un comando e inserisce la password quando richiesto (tramite PTY)."""
    pid, fd = pty.fork()
    
    if pid == 0:  # Child
        os.execvp(cmd_args[0], cmd_args)
    
    # Parent
    output = b""
    while True:
        try:
            r, w, e = select.select([fd], [], [], 600.0)
            if not r:
                break
            
            data = os.read(fd, 8192)
            if not data:
                break
            
            output += data
            # Log progress to stderr for visibility
            sys.stderr.buffer.write(data)
            sys.stderr.buffer.flush()
            
            if b"password:" in data.lower() or b"Password:" in data:
                os.write(fd, (password + '\n').encode())
        except OSError:
            break
            
    os.close(fd)
    _, exit_status = os.waitpid(pid, 0)
    return exit_status, output

PASSWORD = "sys15895"
TARGET_HOST = "marcus"
TARGET_DIR = "/mnt/ssd/robopy_controller_host"
# Sulla base dei log dell'errore, ROS 2 Jazzy è installato qui:
ROS_SOURCE = "source /home/robopy/ros2_jazzy/install/setup.bash"

def main():
    steps = [
        {
            "name": "STEP 0: Pulizia Workspace (Clean Build)",
            "cmd": ["ssh", "-o", "StrictHostKeyChecking=no", TARGET_HOST, 
                    f"cd {TARGET_DIR} && rm -rf build/ install/ log/ 2>/dev/null || true"],
            "ignore_error": True
        },
        {
            "name": "STEP 1: Recupero dati dal Robot (Skills & Logs)",
            "cmd": ["rsync", "-avz", "-e", "ssh -o StrictHostKeyChecking=no", 
                    "--include=robopy_controller/", "--include=robopy_controller/logs/", "--include=robopy_controller/logs/**",
                    "--include=robopy_controller/robot_ai/", "--include=robopy_controller/robot_ai/skills/", "--include=robopy_controller/robot_ai/skills/**",
                    "--exclude=__pycache__/", "--exclude=*.pyc", "--exclude=*",
                    f"{TARGET_HOST}:{TARGET_DIR}/", "./"],
            "ignore_error": True
        },
        {
            "name": "STEP 2: Aggiornamento Workspace completo (PC -> Robot)",
            "cmd": ["rsync", "-avz", "-e", "ssh -o StrictHostKeyChecking=no", "--delete", 
                    "--exclude=.git/", "--exclude=.agent/", "--exclude=.cache/", "--exclude=build/", "--exclude=install/", 
                    "--exclude=log/", "--exclude=__pycache__/", "--exclude=*.pyc",
                    "./", f"{TARGET_HOST}:{TARGET_DIR}/"],
            "ignore_error": False
        },
        {
            "name": "STEP 3: Compilazione remota (Colcon Build)",
            "cmd": ["ssh", "-o", "StrictHostKeyChecking=no", TARGET_HOST, 
                    f"bash -c '{ROS_SOURCE} && cd {TARGET_DIR} && colcon build --symlink-install --event-handlers console_direct+ --packages-select robopy_controller'"],
            "ignore_error": False
        },
        {
            "name": "STEP 4: Permessi e Volume",
            "cmd": ["ssh", "-o", "StrictHostKeyChecking=no", TARGET_HOST, 
                    f"chmod +x {TARGET_DIR}/scripts/* {TARGET_DIR}/*.sh {TARGET_DIR}/robopy_controller/nodes/*.py 2>/dev/null || true && amixer sset 'Playback' 5% && amixer sset 'Capture' 100%"],
            "ignore_error": True
        }
    ]
    
    for step in steps:
        print(f"\n🚀 {step['name']}")
        status, _ = run_with_password(step["cmd"], PASSWORD)
        
        if status != 0 and not step["ignore_error"]:
            print(f"❌ Errore critico durante {step['name']} (status: {status})")
            sys.exit(1)
    
    print("\n✅ Operazione completata! Marcus è aggiornato e compilato.")

if __name__ == "__main__":
    main()
