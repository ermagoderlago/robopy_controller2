try:
    import pytz
    print("pytz is installed")
    from datetime import datetime
    rome = pytz.timezone('Europe/Rome')
    print(f"Rome time: {datetime.now(rome)}")
except ImportError:
    print("pytz is NOT installed")
