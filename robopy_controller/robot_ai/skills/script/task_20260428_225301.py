import shutil
import os

def check_disk_usage():
    paths = {'SSD': '/', 'SD Card': '/mnt/sd'}
    
    print(f"{'Mount':<10} | {'Total':<10} | {'Used':<10} | {'Free':<10} | {'Usage %'}")
    print("-" * 60)
    
    for label, path in paths.items():
        if os.path.exists(path):
            usage = shutil.disk_usage(path)
            total = usage.total // (2**30)
            used = usage.used // (2**30)
            free = usage.free // (2**30)
            percent = (usage.used / usage.total) * 100
            print(f"{label:<10} | {total:>7}GB | {used:>7}GB | {free:>7}GB | {percent:>6.1f}%")

if __name__ == "__main__":
    check_disk_usage()