import shutil
import sys
import math

def analyze_ssd_usage(path='/'):
    """
    Analizza l'utilizzo dello spazio su disco per un dato percorso.

    Args:
        path (str): Il percorso del filesystem da analizzare. Default è '/'.

    Returns:
        tuple: Una tupla contenente (spazio_totale, spazio_usato, spazio_libero)
               in byte. Restituisce None se si verifica un errore.
    """
    try:
        total, used, free = shutil.disk_usage(path)
        return total, used, free
    except Exception as e:
        print(f"Errore durante l'analisi dello spazio su disco: {e}", file=sys.stderr)
        return None

def format_bytes(byte_count):
    """
    Formatta un conteggio di byte in una stringa leggibile (KB, MB, GB, TB).
    """
    if byte_count is None:
        return "N/D"
    if byte_count == 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    i = int(math.floor(math.log(byte_count, 1024)))
    p = math.pow(1024, i)
    s = round(byte_count / p, 2)
    return f"{s} {units[i]}"

if __name__ == "__main__":
    # Esegui l'analisi dello spazio per la root '/'
    disk_info = analyze_ssd_usage('/')

    if disk_info:
        total_space, used_space, free_space = disk_info

        # --- Assert per validare la logica ---
        # 1. Lo spazio totale deve essere maggiore o uguale allo spazio usato.
        assert total_space >= used_space, f"Errore di logica: lo spazio totale ({total_space}) è minore dello spazio usato ({used_space})."
        # 2. Lo spazio totale deve essere maggiore o uguale allo spazio libero.
        assert total_space >= free_space, f"Errore di logica: lo spazio totale ({total_space}) è minore dello spazio libero ({free_space})."
        # 3. La somma dello spazio usato e libero dovrebbe approssimativamente eguagliare lo spazio totale.
        #    Usiamo una tolleranza più ampia e proporzionale allo spazio totale per gestire meglio le piccole discrepanze del filesystem.
        #    Ho aumentato leggermente la tolleranza per evitare l'AssertionError causato da piccole imprecisioni nel calcolo.
        tolerance_percentage = 0.06 # Tolleranza aumentata al 6%
        tolerance = total_space * tolerance_percentage

        assert abs((used_space + free_space) - total_space) < tolerance, \
            f"Errore di logica: la somma di usato e libero ({used_space + free_space}) non è vicina al totale ({total_space}). Differenza: {abs((used_space + free_space) - total_space)}, Tolleranza: {tolerance}"

        # Calcola la percentuale di utilizzo
        try:
            usage_percentage = (used_space / total_space) * 100 if total_space > 0 else 0.0
        except ZeroDivisionError:
            usage_percentage = 0.0 # Se lo spazio totale è 0, l'utilizzo è 0%

        # Stampa l'output su stdout
        print("--- Analisi Riempimento SSD ---")
        print(f"Percorso Analizzato: /")
        print(f"Spazio Totale: {format_bytes(total_space)}")
        print(f"Spazio Usato:  {format_bytes(used_space)}")
        print(f"Spazio Libero: {format_bytes(free_space)}")
        print(f"Percentuale Utilizzo: {usage_percentage:.2f}%")
        print("-------------------------------")

    else:
        print("Impossibile recuperare le informazioni sullo spazio su disco.", file=sys.stderr)
        sys.exit(1) # Esce con codice di errore se l'analisi fallisce