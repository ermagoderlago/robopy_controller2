def calcola_area_rettangolo(base, altezza):
    """Calcola l'area di un rettangolo con validazione."""
    assert base > 0 and altezza > 0, "Dimensioni devono essere positive"
    area = base * altezza
    assert isinstance(area, (int, float)), "Il risultato deve essere numerico"
    return area

# Esecuzione e validazione
base, altezza = 10, 5
risultato = calcola_area_rettangolo(base, altezza)

# Assert finale per validare la logica del calcolo
assert risultato == 50, "Calcolo dell'area errato"

print(risultato)