import os

path = os.path.expanduser('~/.bashrc')
with open(path, 'r') as f:
    lines = f.readlines()

with open(path, 'w') as f:
    for line in lines:
        if '/mnt/ssd/robopy_controller_host/install' in line and 'if [ -d' in line:
            f.write('if [ -d "/mnt/ssd/robopy_controller_host/install" ]; then\n')
        else:
            f.write(line)

print("✅ .bashrc fixed successfully")
