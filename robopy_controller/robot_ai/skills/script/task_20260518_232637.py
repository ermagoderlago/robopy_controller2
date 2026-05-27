def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo e valida gli input."""
    assert isinstance(base, (int, float)) and base > 0, "Base non valida"
    assert isinstance(altezza, (int, float)) and altezza > 0, "Altezza non valida"
    
    area = base * altezza
    
    # Test di validazione logica
    assert area > 0, "Il calcolo dell'area è fallito"
    return area

# Esecuzione e output
if __name__ == "__main__":
    b, h = 10.5, 5.0
    risultato = calcola_area_rettangolo(b, h)
    
    # Verifica finale del risultato atteso
    assert risultato == 52.5, "Risultato matematico errato"
    print(risultato)