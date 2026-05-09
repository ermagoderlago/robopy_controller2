import os

def find_spotify_skill():
    search_terms = ['spotify_skill', 'spotify']
    search_paths = ['.', './skills', './modules']
    
    found_files = []
    
    for path in search_paths:
        if os.path.exists(path):
            for root, dirs, files in os.walk(path):
                for file in files:
                    if any(term in file.lower() for term in search_terms):
                        found_files.append(os.path.join(root, file))
    
    if found_files:
        print("File trovati:")
        for f in found_files:
            print(f)
    else:
        print("Nessun file corrispondente trovato.")

if __name__ == "__main__":
    find_spotify_skill()