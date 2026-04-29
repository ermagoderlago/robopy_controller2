import shutil
import platform
import psutil

def get_system_info():
    usage = shutil.disk_usage("/")
    print(f"Sistema Operativo: {platform.system()} {platform.release()}")
    print(f"Spazio Totale: {usage.total // (2**30)} GB")
    print(f"Spazio Libero: {usage.free // (2**30)} GB")
    print(f"Utilizzo CPU: {psutil.cpu_percent()}%")
    print(f"Memoria RAM: {psutil.virtual_memory().percent}%")

if __name__ == "__main__":
    get_system_info()