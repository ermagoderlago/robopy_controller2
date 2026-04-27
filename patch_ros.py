import sys

path = '/mnt/ssd/ros2_jazzy/build/launch/launch/actions/reset_launch_configurations.py'
with open(path, 'r') as f:
    text = f.read()

# We need to replace exactly the line: 
# evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))

orig = "evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))"
repl = """try:
                evaluated_v = perform_substitutions(context, normalize_to_list_of_substitutions(v))
            except TypeError as te:
                print(f'-------> CRASHING ON TUPLE! KEY: {k}, VALUE: {v} <-------', file=sys.stderr)
                raise te"""

if orig in text:
    text = text.replace(orig, repl)
    with open(path, 'w') as f:
        f.write(text)
    print("Patched successfully!")
else:
    print("Could not find the line to replace.")
