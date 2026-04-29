import shutil

def check_disk_usage(paths):
    for path in paths:
        try:
            total, used, free = shutil.disk_usage(path)
            percent = (used / total) * 100
            print(f"Percorso: {path}")
            print(f"  Totale: {total // (2**30)} GB")
            print(f"  Usato:  {used // (2**30)} GB")
            print(f"  Libero: {free // (2**30)} GB")
            print(f"  Uso:    {percent:.2f}%")
            print("-" * 20)
        except FileNotFoundError:
            print(f"Errore: Percorso '{path}' non trovato.")
        except Exception as e:
            print(f"Errore su '{path}': {e}")

if __name__ == "__main__":
    check_disk_usage(['/', '/mnt/SSD'])