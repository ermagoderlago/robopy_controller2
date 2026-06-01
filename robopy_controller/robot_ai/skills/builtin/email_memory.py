import json
import os
import logging
from typing import Dict, List, Optional
import datetime

logger = logging.getLogger(__name__)

class EmailMemory:
    """Persistenza locale per pacchi tracciati, appuntamenti e statistiche email."""

    def __init__(self, persist_path: str = None):
        if not persist_path:
            if os.name == 'nt':  # Windows
                persist_path = os.path.join(os.path.expanduser("~"), ".gemini", "email_memory.json")
            else:
                persist_path = "/home/robopy/email_memory.json"
        self.persist_path = persist_path
        self.data = {
            "packages": [],
            "appointments": [],
            "dynamic_entities": {
                "carriers": [],
                "vip_senders": []
            },
            "stats": {
                "emails_received": 0,
                "spam_deleted": 0,
                "important_emails": 0,
                "packages_tracked": 0
            }
        }
        self.load()

    def load(self):
        """Carica i dati dal file JSON."""
        try:
            if os.path.exists(self.persist_path):
                with open(self.persist_path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                # Assicurati che tutte le chiavi base esistano
                if "packages" not in self.data:
                    self.data["packages"] = []
                if "appointments" not in self.data:
                    self.data["appointments"] = []
                if "dynamic_entities" not in self.data:
                    self.data["dynamic_entities"] = {
                        "carriers": [],
                        "vip_senders": []
                    }
                if "stats" not in self.data:
                    self.data["stats"] = {
                        "emails_received": 0,
                        "spam_deleted": 0,
                        "important_emails": 0,
                        "packages_tracked": 0
                    }
                logger.info(f"📧 EmailMemory: caricati dati da {self.persist_path}")
            else:
                self.save()
        except Exception as e:
            logger.error(f"📧 EmailMemory load failed: {e}")

    def save(self):
        """Salva i dati su file JSON in modo atomico."""
        try:
            # Crea directory genitore se non esiste
            parent_dir = os.path.dirname(self.persist_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
            
            # Scrittura atomica per evitare file corrotti in caso di crash
            temp_path = self.persist_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.persist_path)
        except Exception as e:
            logger.error(f"📧 EmailMemory save failed: {e}")

    # --- TRACKING PACCHI ---
    def track_package(self, tracking_number: str, carrier: str, order_info: str = "", status: str = "in_transit") -> bool:
        """Aggiunge o aggiorna un pacco da tracciare."""
        tracking_number = tracking_number.strip()
        if not tracking_number:
            return False
        
        # Cerca se già esiste
        for pkg in self.data["packages"]:
            if pkg["tracking_number"] == tracking_number:
                pkg["carrier"] = carrier
                pkg["order_info"] = order_info or pkg.get("order_info", "")
                pkg["status"] = status
                pkg["last_update"] = datetime.datetime.now().isoformat()
                self.save()
                return True
        
        # Aggiunge nuovo
        self.data["packages"].append({
            "tracking_number": tracking_number,
            "carrier": carrier,
            "order_info": order_info,
            "status": status,
            "added_at": datetime.datetime.now().isoformat(),
            "last_update": datetime.datetime.now().isoformat()
        })
        self.data["stats"]["packages_tracked"] += 1
        self.save()
        logger.info(f"📦 Pacco tracciato: {tracking_number} ({carrier})")
        return True

    def get_active_packages(self) -> List[Dict]:
        """Ritorna tutti i pacchi non ancora consegnati."""
        return [pkg for pkg in self.data["packages"] if pkg.get("status") != "delivered"]

    def update_package_status(self, tracking_number: str, status: str) -> bool:
        """Aggiorna lo stato di un pacco specifico."""
        for pkg in self.data["packages"]:
            if pkg["tracking_number"] == tracking_number:
                pkg["status"] = status
                pkg["last_update"] = datetime.datetime.now().isoformat()
                self.save()
                return True
        return False

    # --- APPUNTAMENTI ---
    def save_appointment(self, date_str: str, time_str: str, description: str, location: str = "", source_email: str = "") -> bool:
        """Salva un appuntamento estratto da un'email, evitando duplicati."""
        # Evita duplicati basandosi su data, ora e descrizione
        for app in self.data["appointments"]:
            if app["date"] == date_str and app["time"] == time_str and app["description"].lower() == description.lower():
                app["location"] = location or app.get("location", "")
                app["source_email"] = source_email or app.get("source_email", "")
                self.save()
                return True

        self.data["appointments"].append({
            "date": date_str,
            "time": time_str,
            "description": description,
            "location": location,
            "source_email": source_email,
            "added_at": datetime.datetime.now().isoformat(),
            "status": "scheduled"
        })
        self.save()
        logger.info(f"📅 Appuntamento salvato: {date_str} {time_str} - {description}")

        # Sincronizza in background best-effort con Google Calendar
        try:
            from .google_calendar import GoogleCalendarClient
            cal = GoogleCalendarClient()
            import threading
            threading.Thread(
                target=cal.add_event,
                args=(date_str, time_str, description, location),
                daemon=True
            ).start()
        except Exception as e:
            logger.debug(f"Sincronizzazione Google Calendar non riuscita o non configurata: {e}")

        return True

    def get_upcoming_appointments(self, days: int = 7) -> List[Dict]:
        """Ritorna gli appuntamenti pianificati nei primi N giorni."""
        now = datetime.datetime.now()
        limit = now + datetime.timedelta(days=days)
        upcoming = []

        for app in self.data["appointments"]:
            if app.get("status") == "cancelled":
                continue
            try:
                # Prova a parsare la data (formato standard YYYY-MM-DD o DD/MM/YYYY)
                date_part = app["date"]
                if "/" in date_part:
                    day, month, year = map(int, date_part.split("/"))
                    if year < 100:
                        year += 2000
                    app_date = datetime.datetime(year, month, day)
                else:
                    app_date = datetime.datetime.fromisoformat(date_part)
                
                if now.date() <= app_date.date() <= limit.date():
                    upcoming.append(app)
            except Exception:
                # Se il parsing fallisce, lo includiamo comunque per sicurezza se non è nel passato
                upcoming.append(app)

        return upcoming

    # --- STATISTICHE ---
    def increment_stat(self, key: str, amount: int = 1):
        """Incrementa una statistica."""
        if key in self.data["stats"]:
            self.data["stats"][key] += amount
            self.save()

    def get_stats(self) -> Dict:
        """Ritorna le statistiche correnti."""
        return self.data["stats"]

    # --- APPRENDIMENTO DINAMICO ---
    def learn_carrier(self, name: str) -> bool:
        """Aggiunge un corriere appreso dinamicamente."""
        name = name.strip().lower()
        if not name:
            return False
        if "dynamic_entities" not in self.data:
            self.data["dynamic_entities"] = {"carriers": [], "vip_senders": []}
        if name not in self.data["dynamic_entities"]["carriers"]:
            self.data["dynamic_entities"]["carriers"].append(name)
            self.save()
            logger.info(f"🧠 AI ha appreso un nuovo corriere: {name}")
            return True
        return False

    def learn_vip(self, email: str) -> bool:
        """Aggiunge un mittente VIP appreso dinamicamente."""
        email = email.strip().lower()
        if not email or "@" not in email:
            return False
        if "dynamic_entities" not in self.data:
            self.data["dynamic_entities"] = {"carriers": [], "vip_senders": []}
        if email not in self.data["dynamic_entities"]["vip_senders"]:
            self.data["dynamic_entities"]["vip_senders"].append(email)
            self.save()
            logger.info(f"🧠 AI ha appreso un nuovo VIP sender: {email}")
            return True
        return False

    def get_learned_carriers(self) -> List[str]:
        """Ritorna la lista dei corrieri appresi dinamicamente."""
        return self.data.get("dynamic_entities", {}).get("carriers", [])

    def get_learned_vips(self) -> List[str]:
        """Ritorna la lista dei VIP appresi dinamicamente."""
        return self.data.get("dynamic_entities", {}).get("vip_senders", [])
