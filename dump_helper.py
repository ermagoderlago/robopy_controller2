import sys
import traceback
import signal
import os

def dump_stacks(sig, frame):
    print("\n" + "="*80)
    print(f"STACK DUMP for PID {os.getpid()}")
    print("="*80)
    for threadId, stack in sys._current_frames().items():
        print(f"\n# ThreadID: {threadId}")
        for filename, lineno, name, line in traceback.extract_stack(stack):
            print(f"  File: \"{filename}\", line {lineno}, in {name}")
            if line:
                print(f"    {line.strip()}")
    print("="*80 + "\n")

if __name__ == "__main__":
    # This script is intended to be USED as a library or injected
    # But we can run it standalone to test.
    # To dump a running process, we'd need gdb or to have this pre-installed.
    
    # Let's create a more useful tool: a helper that uses GDB to dump stacks of a PID.
    pass
