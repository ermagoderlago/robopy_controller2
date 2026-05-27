def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo con validazione."""
    assert base > 0 and altezza > 0, "Dimensioni devono essere positive"
    area = base * altezza
    assert isinstance(area, (int, float)), "Il risultato deve essere numerico"
    return area

# Esecuzione e validazione
if __name__ == "__main__":
    b, h = 10, 5
    risultato = calcola_area_rettangolo(b, h)
    
    # Verifica logica post-calcolo
    assert risultato == 50, f"Errore di calcolo: atteso 50, ottenuto {risultato}"
    
    print(risultato)