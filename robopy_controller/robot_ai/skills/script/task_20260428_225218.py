import shutil
import os

def check_disk_usage():
    paths = ['/', '/media', '/mnt']
    print(f"{'Percorso':<20} | {'Utilizzato':<12} | {'Disponibile':<12} | {'Percentuale':<10}")
    print("-" * 65)
    
    for path in paths:
        if os.path.exists(path):
            try:
                total, used, free = shutil.disk_usage(path)
                percent = (used / total) * 100
                print(f"{path:<20} | {used // (2**30):<10} GB | {free // (2**30):<10} GB | {percent:>8.1f}%")
            except OSError:
                continue

if __name__ == "__main__":
    check_disk_usage()