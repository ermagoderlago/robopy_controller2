try:
    from zoneinfo import ZoneInfo
    from datetime import datetime
    rome = ZoneInfo('Europe/Rome')
    print(f"Rome time: {datetime.now(rome)}")
except ImportError:
    print("zoneinfo is NOT available")
except Exception as e:
    print(f"Error: {e}")
