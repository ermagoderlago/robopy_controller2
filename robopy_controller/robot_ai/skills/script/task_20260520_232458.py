def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo con validazione."""
    assert isinstance(base, (int, float)) and base > 0, "Base non valida"
    assert isinstance(altezza, (int, float)) and altezza > 0, "Altezza non valida"
    
    area = base * altezza
    
    # Test logico: l'area deve essere maggiore di zero
    assert area > 0, "Errore logico nel calcolo dell'area"
    return area

if __name__ == "__main__":
    b, h = 10.5, 5.0
    risultato = calcola_area_rettangolo(b, h)
    
    # Verifica finale del risultato atteso
    assert risultato == 52.5, "Il calcolo non corrisponde all'attesa"
    
    print(f"Area calcolata: {risultato}")