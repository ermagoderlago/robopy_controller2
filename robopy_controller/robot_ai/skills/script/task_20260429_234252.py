import os

def find_spotify_files(root_dir='.'):
    for root, dirs, files in os.walk(root_dir):
        for file in files:
            if 'spotify' in file.lower():
                print(os.path.join(root, file))

if __name__ == "__main__":
    find_spotify_files()