def calcola_fattoriale(n: int) -> int:
    """Calcola il fattoriale di un numero intero non negativo."""
    assert isinstance(n, int) and n >= 0, "L'input deve essere un intero non negativo."
    
    if n == 0 or n == 1:
        return 1
    
    risultato = 1
    for i in range(2, n + 1):
        risultato *= i
    return risultato

# Test di validazione
if __name__ == "__main__":
    test_val = 5
    risultato_atteso = 120
    risultato_ottenuto = calcola_fattoriale(test_val)
    
    assert risultato_ottenuto == risultato_atteso, f"Errore: atteso {risultato_atteso}, ottenuto {risultato_ottenuto}"
    
    print(f"Il fattoriale di {test_val} è {risultato_ottenuto}")