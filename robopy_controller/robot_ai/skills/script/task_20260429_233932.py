import os

def check_files():
    files = os.listdir('.')
    targets = [f for f in files if 'spotify' in f.lower() or 'skill' in f.lower()]
    
    if targets:
        print("File trovati:")
        for file in targets:
            print(f"- {file}")
    else:
        print("Nessun file relativo a 'spotify' o 'skill' trovato.")

if __name__ == "__main__":
    check_files()