import shutil
import sys

def check_ssd_saturation(path='/'):
    """
    Verifica la saturazione di un'unità SSD.

    Args:
        path (str): Il percorso del filesystem da controllare. Default è '/'.

    Returns:
        float: La percentuale di spazio utilizzato sull'SSD.

    Raises:
        IOError: Se il percorso specificato non è valido o non è accessibile.
        AssertionError: Se la logica di calcolo risulta errata.
    """
    try:
        total, used, free = shutil.disk_usage(path)
    except FileNotFoundError:
        raise IOError(f"Il percorso '{path}' non è valido o non è accessibile.")

    # Assert per verificare che i valori siano sensati
    assert total >= 0, "Lo spazio totale non può essere negativo."
    assert used >= 0, "Lo spazio utilizzato non può essere negativo."
    assert free >= 0, "Lo spazio libero non può essere negativo."
    assert total >= used, "Lo spazio utilizzato non può eccedere lo spazio totale."
    assert total >= free, "Lo spazio libero non può eccedere lo spazio totale."
    
    # Tolleranza per piccole imprecisioni nei conteggi del disco.
    # Aumentata leggermente per accomodare le tipiche discrepanze dei filesystem.
    # Usiamo una tolleranza basata su una piccola frazione del totale o un valore fisso,
    # a seconda di quale sia più appropriato per la situazione.
    # Per semplicità, usiamo una tolleranza fissa ma ragionevole.
    tolerance = 1024 * 1024 # Tolleranza di 1MB in byte, più robusta.
    assert abs((used + free) - total) < tolerance, "La somma di utilizzato e libero deve essere molto vicina al totale."

    if total == 0:
        # Gestisce il caso limite di un disco vuoto o con spazio zero
        return 0.0

    saturation_percentage = (used / total) * 100

    # Assert per verificare che la percentuale sia nell'intervallo corretto
    assert 0.0 <= saturation_percentage <= 100.0, "La percentuale di saturazione deve essere tra 0 e 100."

    return saturation_percentage

if __name__ == "__main__":
    try:
        # Puoi cambiare il percorso se necessario, ad esempio 'C:/' su Windows
        ssd_path = '/'
        saturation = check_ssd_saturation(ssd_path)
        print(f"La saturazione dell'SSD per il percorso '{ssd_path}' è: {saturation:.2f}%")
    except IOError as e:
        print(f"Errore: {e}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"Errore di logica negli assert: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Si è verificato un errore imprevisto: {e}", file=sys.stderr)
        sys.exit(1)