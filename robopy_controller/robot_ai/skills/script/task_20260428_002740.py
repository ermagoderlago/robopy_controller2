import shutil
import os

def get_system_info():
    total, used, free = shutil.disk_usage("/")
    print(f"Spazio Totale: {total // (2**30)} GB")
    print(f"Spazio Usato: {used // (2**30)} GB")
    print(f"Spazio Libero: {free // (2**30)} GB")
    print(f"Sistema Operativo: {os.name}")

if __name__ == "__main__":
    get_system_info()