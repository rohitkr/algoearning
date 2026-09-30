"""Platform market data (phase 10): ONE central feed for every user (ICICI Breeze first, a licensed vendor
later) -> Redis -> WebSocket gateway. Source: marketdata/kite_stream.py (refcount, reconnect, stale
detection), trader/stream.py (tick hub), trading_data/breeze/."""

__version__ = "0.1.0"
