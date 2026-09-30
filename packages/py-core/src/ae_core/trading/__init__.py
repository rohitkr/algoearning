"""Strategy execution logic shared by the engine (and later the backtester): pure, no I/O, deterministic.

rules.py     the trading rules as small functions (ported from algo-trading-claude backtest/rules.py)
model.py     contracts, market view, positions and order intents
runners.py   one state machine per strategy kind: time_based (builder), range_breakout, zero_dte
risk.py      pre-trade checks: user risk settings, plan limits, kill switches
"""
