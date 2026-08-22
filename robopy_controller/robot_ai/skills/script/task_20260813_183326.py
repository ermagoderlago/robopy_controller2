import sys

def say_and_print(text_to_say):
    """
    Ripete il testo fornito e lo stampa su stdout.
    Simula l'uso della funzionalità 'say' del respeaker.

    Args:
        text_to_say (str): Il testo da dire e stampare.

    Raises:
        AssertionError: Se l'input non è una stringa o è vuoto.
    """
    assert isinstance(text_to_say, str), "L'input deve essere una stringa."
    assert text_to_say, "L'input non può essere vuoto."

    # Simula l'output della funzionalità 'say'
    print(f"Speakin': {text_to_say}")
    # In un'applicazione reale, qui ci sarebbe la chiamata alla funzione 'say' del respeaker.

if __name__ == "__main__":
    # Gestione dell'input da riga di comando
    if len(sys.argv) > 1:
        user_input = sys.argv[1]
        try:
            say_and_print(user_input)
        except AssertionError as e:
            print(f"Errore di validazione: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Se non viene fornito alcun argomento, eseguiamo i test interni
        print("Nessun input da riga di comando fornito. Esecuzione dei test interni.")

        # Test con assert per validare la logica
        try:
            # Test positivo
            print("\nEsecuzione test positivo...")
            say_and_print("Ciao, mondo!")
            print("Test positivo superato!")

            # Test negativo: input non stringa
            print("\nEsecuzione test negativo (input non stringa)...")
            try:
                say_and_print(123)
            except AssertionError as e:
                print(f"Test negativo (input non stringa) fallito come previsto: {e}")

            # Test negativo: input vuoto
            print("\nEsecuzione test negativo (input vuoto)...")
            try:
                say_and_print("")
            except AssertionError as e:
                print(f"Test negativo (input vuoto) fallito come previsto: {e}")

        except Exception as e: # Cattura qualsiasi altra eccezione imprevista durante i test
            print(f"Errore inatteso durante i test: {e}", file=sys.stderr)
            sys.exit(1)