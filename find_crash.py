import os
import sys
import glob

log_base = os.path.expanduser("~/.ros/log")
if not os.path.isdir(log_base):
    print(f"Log dir not found: {log_base}")
    sys.exit(1)

directories = [d for d in glob.glob(os.path.join(log_base, "*")) if os.path.isdir(d) and not os.path.basename(d).startswith("latest")]
directories.sort(key=os.path.getmtime, reverse=True)

if not directories:
    print("No log directories found")
    sys.exit(1)

latest_dir = directories[0]
print(f"Looking in: {latest_dir}")

for file_path in glob.glob(os.path.join(latest_dir, "*.log")):
    filename = os.path.basename(file_path)
    if filename == "launch.log":
        continue
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            if "Traceback" in content or "Exception" in content:
                print(f"\n======================================")
                print(f"CRASH TRACE IN {filename}:")
                # Just print the last 40 lines of the file so we see the full traceback and the crash reason
                lines = content.split('\n')
                print('\n'.join(lines[-40:]))
                print(f"======================================\n")
    except Exception as e:
        pass
