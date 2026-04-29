import shutil
import os

def check_disk_usage(paths):
    for path in paths:
        if not os.path.exists(path):
            print(f"Percorso: {path} | Errore: Non montato o inesistente")
            continue
            
        usage = shutil.disk_usage(path)
        total = usage.total / (1024**3)
        used = usage.used / (1024**3)
        free = usage.free / (1024**3)
        percent = (usage.used / usage.total) * 100
        
        print(f"Percorso: {path}")
        print(f"  Totale: {total:.2f} GB | Usato: {used:.2f} GB | Libero: {free:.2f} GB | Utilizzo: {percent:.1f}%")

if __name__ == "__main__":
    check_disk_usage(['/', '/mnt/ssd'])