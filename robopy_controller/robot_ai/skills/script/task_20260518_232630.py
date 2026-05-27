def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo con validazione."""
    assert base > 0 and altezza > 0, "Dimensioni devono essere positive"
    area = base * altezza
    assert isinstance(area, (int, float)), "Il calcolo deve restituire un numero"
    return area

if __name__ == "__main__":
    # Esecuzione e validazione
    b, h = 10, 5
    risultato = calcola_area_rettangolo(b, h)
    
    # Test assertivo finale
    assert risultato == 50, f"Errore logico: atteso 50, ottenuto {risultato}"
    
    print(risultato)