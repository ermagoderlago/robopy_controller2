import shutil
import platform
import psutil

def get_system_info():
    try:
        total, used, free = shutil.disk_usage("/")
        info = {
            "Sistema Operativo": platform.system(),
            "Versione": platform.release(),
            "CPU": platform.processor(),
            "RAM Totale (GB)": round(psutil.virtual_memory().total / (1024**3), 2),
            "Disco Totale (GB)": round(total / (1024**3), 2),
            "Disco Libero (GB)": round(free / (1024**3), 2)
        }
        for key, value in info.items():
            print(f"{key}: {value}")
    except Exception as e:
        print(f"Errore durante il recupero delle informazioni: {e}")

if __name__ == "__main__":
    get_system_info()