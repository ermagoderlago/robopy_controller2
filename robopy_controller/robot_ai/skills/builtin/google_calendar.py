import os
import datetime
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Tenta l'importazione delle librerie Google
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

SCOPES = ['https://www.googleapis.com/auth/calendar.events']

class GoogleCalendarClient:
    """Client per l'integrazione di Google Calendar API (OAuth2)."""
    
    def __init__(self, credentials_dir: str = None):
        if not credentials_dir:
            if os.name == 'nt':
                credentials_dir = os.path.join(os.path.expanduser("~"), ".gemini")
            else:
                credentials_dir = "/home/robopy"
        self.credentials_dir = credentials_dir
        self.creds_path = os.path.join(self.credentials_dir, "google_token.json")
        self.client_secret_path = os.path.join(self.credentials_dir, "client_secret.json")
        self.creds = None
        
    def _authenticate(self) -> bool:
        """Esegue l'autenticazione OAuth2 tramite token locale o client_secret.json."""
        if not GOOGLE_LIBS_AVAILABLE:
            logger.warning("📧 Google Calendar: librerie google-api-python-client o google-auth-oauthlib mancanti.")
            return False
            
        try:
            if os.path.exists(self.creds_path):
                self.creds = Credentials.from_authorized_user_file(self.creds_path, SCOPES)
                
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                    # Salva il token aggiornato
                    with open(self.creds_path, 'w') as token:
                        token.write(self.creds.to_json())
                else:
                    if not os.path.exists(self.client_secret_path):
                        logger.warning(
                            f"📧 Google Calendar: File {self.client_secret_path} non trovato. "
                            "Sincronizzazione disabilitata. Inserisci il client_secret.json per abilitare."
                        )
                        return False
                    
                    # OAuth2 Flow
                    logger.info("📧 Google Calendar: Avvio flow OAuth2...")
                    flow = InstalledAppFlow.from_client_secrets_file(self.client_secret_path, SCOPES)
                    self.creds = flow.run_local_server(port=0, open_browser=False)
                    
                    # Salva il token per i successivi avvii
                    with open(self.creds_path, 'w') as token:
                        token.write(self.creds.to_json())
            return True
        except Exception as e:
            logger.error(f"📧 Google Calendar: Errore durante l'autenticazione: {e}")
            return False

    def add_event(self, date_str: str, time_str: str, description: str, location: str = "") -> bool:
        """Aggiunge un evento a Google Calendar."""
        if not self._authenticate():
            return False
            
        try:
            service = build('calendar', 'v3', credentials=self.creds)
            
            # Parsing data e ora
            if "/" in date_str:
                day, month, year = map(int, date_str.split("/"))
                if year < 100:
                    year += 2000
                dt = datetime.datetime(year, month, day)
            else:
                dt = datetime.datetime.fromisoformat(date_str)
                
            hour, minute = map(int, time_str.split(":"))
            start_dt = datetime.datetime(dt.year, dt.month, dt.day, hour, minute)
            end_dt = start_dt + datetime.timedelta(hours=1) # Default 1h
            
            event = {
                'summary': description,
                'location': location,
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Europe/Rome',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Europe/Rome',
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 30},
                        {'method': 'email', 'minutes': 24 * 60},
                    ],
                },
            }
            
            created_event = service.events().insert(calendarId='primary', body=event).execute()
            logger.info(f"📧 Google Calendar: Evento creato con successo! Link: {created_event.get('htmlLink')}")
            return True
        except Exception as e:
            logger.error(f"📧 Google Calendar: Errore creazione evento: {e}")
            return False
