def calcola_fattoriale(n):
    """Calcola il fattoriale di un numero intero non negativo."""
    assert isinstance(n, int) and n >= 0, "L'input deve essere un intero non negativo."
    
    if n == 0 or n == 1:
        return 1
    
    risultato = 1
    for i in range(2, n + 1):
        risultato *= i
    return risultato

# Test di validazione
assert calcola_fattoriale(0) == 1
assert calcola_fattoriale(5) == 120
assert calcola_fattoriale(10) == 3628800

# Esecuzione e output
if __name__ == "__main__":
    numero = 5
    print(f"Il fattoriale di {numero} è {calcola_fattoriale(numero)}")