def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo e valida gli input."""
    assert isinstance(base, (int, float)) and base > 0, "La base deve essere un numero positivo"
    assert isinstance(altezza, (int, float)) and altezza > 0, "L'altezza deve essere un numero positivo"
    
    area = base * altezza
    
    # Validazione logica del risultato
    assert area > 0, "Il calcolo dell'area è fallito"
    return area

# Esecuzione e output
if __name__ == "__main__":
    b, h = 10.0, 5.0
    risultato = calcola_area_rettangolo(b, h)
    
    # Verifica finale
    assert risultato == 50.0, "Risultato matematico errato"
    print(risultato)