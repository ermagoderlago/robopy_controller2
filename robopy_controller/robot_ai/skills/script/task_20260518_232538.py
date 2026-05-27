def calcola_fattoriale(n):
    """Calcola il fattoriale di un numero intero non negativo."""
    assert isinstance(n, int) and n >= 0, "Input deve essere un intero non negativo"
    if n == 0:
        return 1
    
    risultato = 1
    for i in range(1, n + 1):
        risultato *= i
    return risultato

# Test-Driven Development: Validazione logica
assert calcola_fattoriale(0) == 1
assert calcola_fattoriale(5) == 120
assert calcola_fattoriale(10) == 3628800

# Esecuzione e stampa su stdout
print(calcola_fattoriale(5))