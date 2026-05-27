import psutil

def get_system_stats():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    print(f"CPU Usage: {cpu_usage}%")
    print(f"RAM Usage: {memory.percent}% (Used: {memory.used // (1024**2)}MB / Total: {memory.total // (1024**2)}MB)")

if __name__ == "__main__":
    get_system_stats()