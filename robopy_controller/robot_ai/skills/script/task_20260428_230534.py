import glob

def get_thermal_data():
    for path in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
        try:
            with open(path, 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                print(f"{path}: {temp:.2f}°C")
        except (IOError, ValueError):
            continue

if __name__ == "__main__":
    get_thermal_data()