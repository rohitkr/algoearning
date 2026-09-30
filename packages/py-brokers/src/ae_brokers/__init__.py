"""Broker adapters for EXECUTION only (phase 7): login/token exchange, place/modify/cancel, order book,
positions, margins, symbol mapping. Zerodha first (from zerodha/ + trader/broker.py incl. MCX lots<->units),
then Upstox and Angel One behind the same BrokerAdapter interface."""

__version__ = "0.1.0"
