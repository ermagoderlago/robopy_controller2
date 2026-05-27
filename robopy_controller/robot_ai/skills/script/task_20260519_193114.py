def calcola_somma_quadrati(n):
    """Calcola la somma dei quadrati dei primi n numeri interi."""
    assert isinstance(n, int) and n >= 0, "L'input deve essere un intero non negativo"
    
    risultato = sum(i**2 for i in range(1, n + 1))
    
    # Validazione tramite formula matematica nota: n(n+1)(2n+1)/6
    assert risultato == (n * (n + 1) * (2 * n + 1)) // 6, "Errore nel calcolo logico"
    
    return risultato

if __name__ == "__main__":
    n = 10
    output = calcola_somma_quadrati(n)
    print(output)