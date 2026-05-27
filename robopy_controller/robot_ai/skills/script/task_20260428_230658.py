import os
import glob

def get_thermal_data():
    thermal_zones = glob.glob('/sys/class/thermal/thermal_zone*')
    results = {}
    
    for zone in thermal_zones:
        try:
            with open(os.path.join(zone, 'temp'), 'r') as f:
                temp = float(f.read().strip()) / 1000.0
            with open(os.path.join(zone, 'type'), 'r') as f:
                name = f.read().strip()
            results[name] = f"{temp:.2f}°C"
        except (IOError, ValueError):
            continue
            
    for name, temp in results.items():
        print(f"{name}: {temp}")

if __name__ == "__main__":
    get_thermal_data()