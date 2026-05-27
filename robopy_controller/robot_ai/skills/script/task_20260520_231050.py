def calcola_fattoriale(n):
    """Calcola il fattoriale di un numero non negativo."""
    assert isinstance(n, int) and n >= 0, "Input deve essere un intero non negativo"
    if n == 0 or n == 1:
        return 1
    risultato = 1
    for i in range(2, n + 1):
        risultato *= i
    return risultato

# Validazione tramite TDD
assert calcola_fattoriale(0) == 1
assert calcola_fattoriale(5) == 120
assert calcola_fattoriale(10) == 3628800

# Esecuzione
print(calcola_fattoriale(5))