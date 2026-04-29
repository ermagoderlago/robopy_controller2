import subprocess

script_content = """#!/bin/bash
echo "Punto di montaggio | Totale | Usato | Libero | Uso %"
echo "----------------------------------------------------"
for mount in "/" "/mnt/ssd"; do
    if mountpoint -q "$mount" 2>/dev/null || [ "$mount" == "/" ]; then
        df -h "$mount" | awk 'NR==2 {printf "%s | %s | %s | %s | %s\\n", $6, $2, $3, $4, $5}'
    else
        echo "$mount | Non trovato"
    fi
done
"""

with open("disk_usage.sh", "w") as f:
    f.write(script_content)

subprocess.run(["chmod", "+x", "disk_usage.sh"])
result = subprocess.run(["./disk_usage.sh"], capture_output=True, text=True)
print(result.stdout)