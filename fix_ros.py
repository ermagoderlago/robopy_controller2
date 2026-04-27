import sys

path = '/mnt/ssd/ros2_jazzy/build/launch/launch/actions/reset_launch_configurations.py'
with open(path, 'r') as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    if "try:" in lines[i]:
        indent = lines[i].split("try:")[0]
        # Reconstruct the try/except with correct relative indentation
        new_lines.append(indent + "try:\n")
        new_lines.append(indent + "    evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))\n")
        new_lines.append(indent + "except TypeError as te:\n")
        new_lines.append(indent + "    print(f'-------> CRASHING ON TUPLE! KEY: {k}, VALUE: {v} <-------', file=sys.stderr)\n")
        new_lines.append(indent + "    raise te\n")
        
        # Skip the lines that were incorrectly inserted previously.
        # Previously we did a string replace of the ONE line with a big block.
        # But wait! If the file ON DISK currently has bad indentation, what does it look like?
        # It looks like:
        # try:
        #                 evaluated_v = ...
        #             except TypeError ...
        #                 print(...)
        #                 raise te
        
        # To be safe, just restore the original line!
        print("Wait, let's just restore the original line completely to be safe.")
        new_lines = []
        break
    i += 1

if not new_lines:
    # Just restore the file from the git repo or by simple string replacing the bad block
    pass

with open(path, 'r') as f:
    text = f.read()

import re
# Find the bad block and replace it with the original line properly indented
bad_block_pattern = r"( *)try:\s*evaluated_v = perform_substitutions\(context, normalize_to_list_of_substitutions\(v\)\)\s*except TypeError as te:\s*print\(f'-------> CRASHING ON TUPLE! KEY: \{k\}, VALUE: \{v\} <-------', file=sys\.stderr\)\s*raise te"

match = re.search(bad_block_pattern, text)
if match:
    indent = match.group(1)
    # The original line:
    orig_line = indent + "evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))"
    
    # We want to replace it with a properly indented try/catch
    good_block = f"""{indent}try:
{indent}    evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))
{indent}except TypeError as te:
{indent}    print(f'-------> CRASHING ON TUPLE! KEY: {{k}}, VALUE: {{v}} <-------', file=sys.stderr)
{indent}    raise te"""
    
    text = text[:match.start()] + good_block + text[match.end():]
    with open(path, 'w') as f:
        f.write(text)
    print("Fixed indentation successfully!")
else:
    print("Could not find the bad block")
