"""
Temporal Parser for MAG (Memory-Augmented Generation) TRINITY system.
Extracts time intervals [start_timestamp, end_timestamp] and temporal intents
from natural language queries in Italian.
"""

import re
import datetime
from typing import Optional, Tuple, Dict, Any, List
try:
    from zoneinfo import ZoneInfo
    ROME_TZ = ZoneInfo("Europe/Rome")
except Exception:
    ROME_TZ = None

ITALIAN_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4,
    "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12
}

def get_current_datetime(tz=ROME_TZ) -> datetime.datetime:
    """Returns current localized datetime."""
    if tz:
        try:
            return datetime.datetime.now(tz)
        except Exception:
            pass
    return datetime.datetime.now()

class MAGTemporalParser:
    """Parses temporal expressions from user queries."""

    @staticmethod
    def parse_time_range(
        text: str, 
        ref_dt: Optional[datetime.datetime] = None
    ) -> Optional[Tuple[float, float, str]]:
        """
        Parses text for temporal references.
        Returns (start_timestamp, end_timestamp, temporal_label) if detected, else None.
        """
        if not text:
            return None

        if ref_dt is None:
            ref_dt = get_current_datetime()

        today_midnight = ref_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = ref_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

        text_lower = text.lower()

        # 1. "Oggi", "stamattina", "stasera", "oggi pomeriggio"
        if re.search(r'\b(oggi|stamattina|stasera|oggi pomeriggio)\b', text_lower):
            return (today_midnight.timestamp(), today_end.timestamp(), "oggi")

        # 2. "L'altro ieri", "altro ieri", "due giorni fa"
        if re.search(r'\b(l\'altro ieri|altro ieri|due giorni fa|2 giorni fa)\b', text_lower):
            day_target = today_midnight - datetime.timedelta(days=2)
            day_target_end = day_target.replace(hour=23, minute=59, second=59, microsecond=999999)
            return (day_target.timestamp(), day_target_end.timestamp(), "l'altro ieri")

        # 3. "Ieri", "ieri sera", "ieri mattina", "ieri pomeriggio"
        if re.search(r'\b(ieri|ieri sera|ieri mattina|ieri pomeriggio)\b', text_lower):
            yesterday_midnight = today_midnight - datetime.timedelta(days=1)
            yesterday_end = yesterday_midnight.replace(hour=23, minute=59, second=59, microsecond=999999)
            return (yesterday_midnight.timestamp(), yesterday_end.timestamp(), "ieri")

        # 4. "N giorni fa" (es. "3 giorni fa")
        m_days_ago = re.search(r'\b(\d+)\s+giorni\s+fa\b', text_lower)
        if m_days_ago:
            n_days = int(m_days_ago.group(1))
            target_start = today_midnight - datetime.timedelta(days=n_days)
            target_end = target_start.replace(hour=23, minute=59, second=59, microsecond=999999)
            return (target_start.timestamp(), target_end.timestamp(), f"{n_days} giorni fa")

        # 5. "Ultimi N giorni"
        m_last_n_days = re.search(r'\b(?:ultimi|negli ultimi)\s+(\d+)\s+giorni\b', text_lower)
        if m_last_n_days:
            n_days = int(m_last_n_days.group(1))
            target_start = today_midnight - datetime.timedelta(days=n_days)
            return (target_start.timestamp(), today_end.timestamp(), f"ultimi {n_days} giorni")

        # 6. "Questa settimana"
        if re.search(r'\b(questa settimana|in settimana)\b', text_lower):
            # Monday of current week
            monday = today_midnight - datetime.timedelta(days=today_midnight.weekday())
            return (monday.timestamp(), today_end.timestamp(), "questa settimana")

        # 7. "Settimana scorsa"
        if re.search(r'\b(settimana scorsa|la scorsa settimana)\b', text_lower):
            monday_this_week = today_midnight - datetime.timedelta(days=today_midnight.weekday())
            monday_last_week = monday_this_week - datetime.timedelta(days=7)
            sunday_last_week = monday_this_week - datetime.timedelta(seconds=1)
            return (monday_last_week.timestamp(), sunday_last_week.timestamp(), "settimana scorsa")

        # 8. Date named with Italian month: "2 ottobre", "02 ottobre 2026", "il 2 di ottobre"
        m_month_name = re.search(
            r'\b(?:il\s+)?(\d{1,2})(?:\s+di|\s+del)?\s+(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)(?:\s+(\d{4}))?\b',
            text_lower
        )
        if m_month_name:
            day = int(m_month_name.group(1))
            month_str = m_month_name.group(2)
            year = int(m_month_name.group(3)) if m_month_name.group(3) else ref_dt.year
            month = ITALIAN_MONTHS.get(month_str, 1)
            try:
                dt_target = datetime.datetime(year, month, day, 0, 0, 0, tzinfo=ref_dt.tzinfo)
                dt_target_end = datetime.datetime(year, month, day, 23, 59, 59, 999999, tzinfo=ref_dt.tzinfo)
                label = f"{day} {month_str}" + (f" {year}" if m_month_name.group(3) else "")
                return (dt_target.timestamp(), dt_target_end.timestamp(), label)
            except ValueError:
                pass

        # 9. Numeric date: DD/MM/YYYY or DD-MM-YYYY or DD/MM
        m_num_date = re.search(r'\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{4}))?\b', text_lower)
        if m_num_date:
            day = int(m_num_date.group(1))
            month = int(m_num_date.group(2))
            year = int(m_num_date.group(3)) if m_num_date.group(3) else ref_dt.year
            try:
                dt_target = datetime.datetime(year, month, day, 0, 0, 0, tzinfo=ref_dt.tzinfo)
                dt_target_end = datetime.datetime(year, month, day, 23, 59, 59, 999999, tzinfo=ref_dt.tzinfo)
                label = f"{day:02d}/{month:02d}/{year}"
                return (dt_target.timestamp(), dt_target_end.timestamp(), label)
            except ValueError:
                pass

        return None

    @staticmethod
    def is_frequency_or_stats_query(text: str) -> bool:
        """Determines if the query is asking about memory statistics, frequencies, or counts."""
        if not text:
            return False
        text_lower = text.lower()
        patterns = [
            r'\bquante\s+volte\b',
            r'\bfrequenza\b',
            r'\bstatistiche\b',
            r'\bquanti\s+ricordi\b',
            r'\bquanti\s+episodi\b',
            r'\bquante\s+interazioni\b',
            r'\banalisi\s+episodi\b',
            r'\bnumero\s+di\s+volte\b',
            r'\bquante\s+domande\b'
        ]
        return any(re.search(p, text_lower) for p in patterns)

    @staticmethod
    def clean_temporal_tokens(text: str) -> str:
        """Removes purely temporal keywords to isolate substantive semantic query tokens."""
        if not text:
            return ""
        temporal_regexes = [
            r'\b(?:oggi|stamattina|stasera|oggi pomeriggio)\b',
            r'\b(?:ieri|ieri sera|ieri mattina|ieri pomeriggio)\b',
            r'\b(?:l\'altro ieri|altro ieri|due giorni fa|\d+\s+giorni\s+fa)\b',
            r'\b(?:ultimi|negli ultimi)\s+\d+\s+giorni\b',
            r'\b(?:questa settimana|in settimana|settimana scorsa|la scorsa settimana)\b',
            r'\b(?:il\s+)?\d{1,2}(?:\s+di|\s+del)?\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)(?:\s+\d{4})?\b',
            r'\b\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{4})?\b',
            r'\b(?:cosa\s+abbiamo\s+fatto|cosa\s+\xc3\xa8\s+successo|cosa\s+e\s+successo|cosa\s+ti\s+ho\s+detto|ti\s+ricordi)\b'
        ]
        res = text
        for pat in temporal_regexes:
            res = re.sub(pat, ' ', res, flags=re.IGNORECASE)
        return re.sub(r'\s+', ' ', res).strip()
