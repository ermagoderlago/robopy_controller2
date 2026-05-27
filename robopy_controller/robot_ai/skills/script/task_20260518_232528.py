def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo con validazione."""
    assert isinstance(base, (int, float)) and base >= 0, "Base non valida"
    assert isinstance(altezza, (int, float)) and altezza >= 0, "Altezza non valida"
    
    area = base * altezza
    
    # Validazione logica del risultato
    assert area >= 0, "L'area non può essere negativa"
    return area

# Esecuzione e test
base_test, altezza_test = 10.5, 5.0
risultato = calcola_area_rettangolo(base_test, altezza_test)

# Verifica finale post-condizione
assert risultato == 52.5, f"Errore di calcolo: atteso 52.5, ottenuto {risultato}"

print(f"Area calcolata: {risultato}")