import os

def check_env():
    keys = [
        "GEMINI_API_KEY",
        "HA_TOKEN",
        "HA_URL",
        "DEEPSEEK_API_KEY",
        "PICOVOICE_API_KEY",
        "EMAIL_ADDRESS",
        "EMAIL_PASSWORD",
        "IMAP_SERVER",
        "SMTP_SERVER"
    ]
    
    print("🔍 Verifica variabili d'ambiente...")
    all_ok = True
    for key in keys:
        value = os.getenv(key)
        if value:
            # Mask value for security
            masked = value[:4] + "*" * (len(value) - 8) + value[-4:] if len(value) > 8 else "****"
            print(f"✅ {key}: Caricata ({masked})")
        else:
            print(f"❌ {key}: MANCANTE")
            all_ok = False
            
    if all_ok:
        print("\n✨ Tutte le variabili critiche sono caricate correttamente!")
    else:
        print("\n⚠️ Alcune variabili mancano. Assicurati di aver fatto 'source setup_keys.sh'")

if __name__ == "__main__":
    check_env()
