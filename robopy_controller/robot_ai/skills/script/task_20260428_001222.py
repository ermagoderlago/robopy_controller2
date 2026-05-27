import shutil
import psutil

def get_system_info():
    disk = shutil.disk_usage("/")
    mem = psutil.virtual_memory()
    
    print(f"Spazio disco totale: {disk.total // (2**30)} GB")
    print(f"Spazio libero: {disk.free // (2**30)} GB")
    print(f"Memoria RAM totale: {mem.total // (2**20)} MB")
    print(f"Memoria RAM disponibile: {mem.available // (2**20)} MB")

if __name__ == "__main__":
    get_system_info()