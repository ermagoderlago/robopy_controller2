import os

file_path = "robopy_controller/robot_ai/orchestration/conversation.py"
with open(file_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "self._set_vui_speaking(True)" in line:
        start_idx = i + 2
        break

for i, line in enumerate(lines[start_idx:]):
    if "self._set_vui_speaking(False)" in line:
        end_idx = start_idx + i
        return_idx = end_idx + 2
        break

new_lines = lines[:start_idx]
new_lines.append("        try:\n")

for line in lines[start_idx:end_idx]:
    new_lines.append("    " + line)

new_lines.append("        finally:\n")
new_lines.append("            # Fine processamento: riapri il microfono\n")
new_lines.append("            self._set_vui_speaking(False)\n")
new_lines.append("\n")
new_lines.append("        return True\n")

new_lines.extend(lines[return_idx:])

with open(file_path, "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("Patch applicata con successo.")
