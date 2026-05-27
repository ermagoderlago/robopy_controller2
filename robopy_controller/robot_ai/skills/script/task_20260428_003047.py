import shutil
import platform

def get_system_info():
    total, used, free = shutil.disk_usage("/")
    print(f"Sistema Operativo: {platform.system()} {platform.release()}")
    print(f"Spazio Totale: {total // (2**30)} GB")
    print(f"Spazio Utilizzato: {used // (2**30)} GB")
    print(f"Spazio Disponibile: {free // (2**30)} GB")

if __name__ == "__main__":
    get_system_info()