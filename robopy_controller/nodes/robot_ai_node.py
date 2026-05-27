#!/usr/bin/env python3
"""
Robot AI Node
=============
Entry point ROS 2 per l'AI Orchestrator.
Il codice di orchestrazione e gestione è stato diviso in moduli dentro robot_ai/orchestration/

Version: 01.00.00 (ECO00003)
"""

__version__ = "01.00.00"
import sys
import os
import signal
import rclpy

# Add proper path if running as script
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

def _load_api_keys_from_setup():
    setup_keys_path = '/home/robopy/robopy/robopi_controller/robopy_controller_host/setup_keys.sh'
    keys_to_load = ['GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'HA_TOKEN']
    if not os.path.exists(setup_keys_path):
        return
    try:
        with open(setup_keys_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                for key_name in keys_to_load:
                    if line.startswith(f'export {key_name}=') and not os.environ.get(key_name):
                        val = line.split('=', 1)[1].strip()
                        if '#' in val: val = val[:val.index('#')].strip()
                        os.environ[key_name] = val.strip('"').strip("'")
                        print(f"✅ {key_name} auto-loaded dalle vecchie configs")
    except Exception as e:
        print(f"⚠️ Could not load API keys da script: {e}")

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    print("✅ Loaded .env file se presente (override system env)")
except ImportError:
    pass

_load_api_keys_from_setup()

# Ora Importiamo l'orchestratore dopo aver eventualmente caricato le chiavi per i moduli interni
from robot_ai.orchestration.orchestrator import AIOrchestrator
import asyncio

def main(args=None):
    rclpy.init(args=args)
    
    orchestrator_node = AIOrchestrator()
    llm_node = orchestrator_node.llm_service
    
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(orchestrator_node)
    executor.add_node(llm_node)
    
    # Setup graceful shutdown su segnali
    def signal_handler(sig, frame):
        print("Ricevuto segnale terminaizione, chiusura sicura...")
        try:
            # Shutdown the asyncio orchestrator correctly if there is an event loop running
            future = asyncio.run_coroutine_threadsafe(orchestrator_node.shutdown(), orchestrator_node._loop)
            future.result(timeout=10.0)
        except Exception as e:
            print(f"Error during node graceful shutdown: {e}")
        finally:
            executor.shutdown()
            rclpy.shutdown()
            sys.exit(0)
            
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Fatal crash in robot_ai_node.py spinning: {e}")
    finally:
        if rclpy.ok():
            try:
                future = asyncio.run_coroutine_threadsafe(orchestrator_node.shutdown(), orchestrator_node._loop)
                future.result(timeout=10.0)
            except Exception:
                pass
            rclpy.shutdown()

if __name__ == '__main__':
    main()