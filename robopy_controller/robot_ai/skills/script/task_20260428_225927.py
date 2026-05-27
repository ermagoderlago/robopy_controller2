import shutil
import os

def check_disk_usage(paths):
    for path in paths:
        if not os.path.exists(path):
            print(f"Percorso: {path} | Stato: Non montato o inesistente")
            continue
            
        usage = shutil.disk_usage(path)
        total_gb = usage.total / (2**30)
        used_gb = usage.used / (2**30)
        free_gb = usage.free / (2**30)
        percent = (usage.used / usage.total) * 100
        
        print(f"Percorso: {path}")
        print(f"  Totale: {total_gb:.2f} GB | Usato: {used_gb:.2f} GB | Libero: {free_gb:.2f} GB | Utilizzo: {percent:.2f}%")

if __name__ == "__main__":
    check_disk_usage(['/', '/mnt/SSD'])