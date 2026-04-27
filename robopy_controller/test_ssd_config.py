
try:
    from robopy_controller.utils import config_utils
except ImportError:
    import sys
    import os
    # Aggiunge il path per trovare config_utils quando lanciato come script
    sys.path.append(os.path.join(os.path.dirname(__file__), 'utils'))
    import config_utils

def test_config():
    print("--- Test Configurazione SSD ---")
    cfg = config_utils.get_config()
    print(f"Configurazione completa: {cfg}")
    
    ssd_path = config_utils.get_path('SSD_PATH')
    rtab_db = config_utils.get_path('RTABMAP_DB_PATH')
    
    print(f"SSD_PATH rilevato: {ssd_path}")
    print(f"RTABMAP_DB_PATH rilevato: {rtab_db}")
    
    if ssd_path == '/ssd/.marcus':
        print("✅ SSD_PATH corretto!")
    else:
        print("❌ Errore in SSD_PATH")
        
    if rtab_db == '/ssd/.marcus/rtabmap.db':
        print("✅ RTABMAP_DB_PATH corretto!")
    else:
        print("❌ Errore in RTABMAP_DB_PATH")

if __name__ == "__main__":
    # Assicuriamoci che il path del progetto sia in sys.path se lo lanciamo localmente
    import sys
    sys.path.append(os.path.join(os.getcwd(), 'robopy_controller'))
    test_config()
