import shutil

paths = ['/', '/mnt/ssd']
for path in paths:
    try:
        total, used, free = shutil.disk_usage(path)
        print(f'{path}: {total/1e9:.2f} GB total, {used/1e9:.2f} GB used, {free/1e9:.2f} GB free')
    except FileNotFoundError:
        print(f'{path}: Percorso non trovato.')
    except Exception as e:
        print(f'{path}: Errore durante l\'accesso ({e})')