
import os

def get_config():
    """Legge il file config.txt e restituisce un dizionario delle impostazioni."""
    config = {
        'SSD_PATH': '/mnt/ssd/.marcus' if os.path.exists('/mnt/ssd') else '/home/robopy/.marcus',
        'RTABMAP_DB_PATH': '/mnt/ssd/.marcus/rtabmap.db' if os.path.exists('/mnt/ssd') else '/home/robopy/.ros/rtabmap.db',
        'LOGS_DIR': '/mnt/ssd/.marcus/logs' if os.path.exists('/mnt/ssd') else '/home/robopy/.ros/log'
    }
    
    # Cerca config.txt nella root del progetto o in home
    search_paths = [
        os.path.join(os.getcwd(), 'config.txt'),
        '/mnt/ssd/severus_host/config.txt',
        '/home/robopy/robopy/robopi_controller/severus_host/config.txt',
        '/home/robopy/robopy/antigravity/config.txt',
        '/home/robopy/config.txt'
    ]
    
    config_file = None
    for p in search_paths:
        if os.path.exists(p):
            config_file = p
            break
            
    if config_file:
        try:
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
        except Exception as e:
            print(f"Errore lettura config.txt: {e}")
            
    return config

def get_path(key, default=None):
    cfg = get_config()
    return cfg.get(key, default)
