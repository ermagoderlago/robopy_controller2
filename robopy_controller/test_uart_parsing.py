
import base64

def clean_line(line):
    return "".join(c for c in line if c.isprintable()).strip()

def handle_rx(line):
    print(f"Testing line: {repr(line)}")
    cleaned = clean_line(line)
    print(f"Cleaned: {repr(cleaned)}")
    
    if cleaned == "TRIGGER_JARVIS" or cleaned == "TRG":
        print("MATCH: TRIGGER")
    elif cleaned == "HEARTBEAT" or cleaned == "HB" or cleaned.endswith("HB"):
        print("MATCH: HEARTBEAT")
    elif cleaned == "READY":
        print("MATCH: READY")
    else:
        print(f"NO MATCH. HEX: {cleaned.encode('utf-8').hex('-')}")
    print("-" * 20)

test_cases = [
    "HB\n",
    "\0HB\n",
    "B\n",
    "READY\n",
    "\x01HB\n",
    "  HB  \n",
    "TRG\n"
]

for case in test_cases:
    handle_rx(case)
