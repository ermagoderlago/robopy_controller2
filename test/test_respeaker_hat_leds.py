#!/usr/bin/env python3
"""
Test per ReSpeaker 2-Mics Pi HAT (Versione GPIO / Hat).
Tenta di pilotare i 3 LED APA102 tramite SPI1 (GPIO 10/11) o SPI0.
"""

import time
try:
    import spidev
except ImportError:
    print("Errore: Libreria 'spidev' non trovata. Installa con: sudo apt install python3-spidev")
    exit(1)

def test_hat_leds():
    # Il Pi HAT usa SPI per i 3 LED APA102
    spi = spidev.SpiDev()
    
    # Prova SPI 0, Device 0 (comune per ReSpeaker Hat)
    try:
        spi.open(0, 0)
        spi.max_speed_hz = 1000000
        print("Aperto SPI 0.0 per test ReSpeaker Hat LEDs")
    except Exception as e:
        print(f"Impossibile aprire SPI 0.0: {e}")
        return

    # Frame APA102: 4 byte di Start (0x00), 4 byte per ogni LED, 4 byte di End (0xFF)
    # Ogni LED: [111 (3 bit) + Brightness (5 bit), Blue, Green, Red]
    
    def set_leds(r, g, b, brightness=31):
        header = [0x00] * 4
        led_frame = [0xE0 | (brightness & 0x1F), b, g, r]
        data = header + (led_frame * 3) + [0xFF] * 4
        spi.xfer2(data)

    try:
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)]
        for r, g, b in colors:
            print(f"Impostazione colore Hat: R={r} G={g} B={b}")
            set_leds(r, g, b)
            time.sleep(1)
        print("Test Hat completato.")
    except KeyboardInterrupt:
        set_leds(0, 0, 0)
    finally:
        spi.close()

if __name__ == "__main__":
    print("Test ReSpeaker 2-Mics Pi HAT (Versione GPIO)")
    print("-------------------------------------------")
    print("Nota: Se hai la versione USB (ReSpeaker Lite), questo script NON funzionerà.")
    test_hat_leds()
