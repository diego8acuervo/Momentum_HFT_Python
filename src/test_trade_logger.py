# -*- coding: utf-8 -*-
"""
Test script for TradeLogger
"""

from TradeLogger import TradeLogger
from Eventos import EventoOrden, EventoCalce
import datetime

# Create logger
logger = TradeLogger(('SOL', 'XRP'))

# Simulate some orders and fills
print("\n=== Testing TradeLogger ===\n")

# Order 1: Buy SOL
orden1 = EventoOrden('SOL', 'MKT', 10, 'buy', 137.50)
logger.log_order(orden1)

# Fill 1: Buy SOL
calce1 = EventoCalce(
    datetime.datetime.now(),
    'SOL',
    'SMART',
    10,
    'buy',
    137.52  # Slight slippage
)
logger.log_fill(calce1)

# Order 2: Sell XRP
orden2 = EventoOrden('XRP', 'MKT', 500, 'sell', 2.15)
logger.log_order(orden2)

# Fill 2: Sell XRP
calce2 = EventoCalce(
    datetime.datetime.now(),
    'XRP',
    'SMART',
    500,
    'sell',
    2.14  # Slight slippage
)
logger.log_fill(calce2)

# Order 3: Sell SOL
orden3 = EventoOrden('SOL', 'MKT', 10, 'sell', 138.00)
logger.log_order(orden3)

# Fill 3: Sell SOL
calce3 = EventoCalce(
    datetime.datetime.now(),
    'SOL',
    'SMART',
    10,
    'sell',
    137.98  # Slight slippage
)
logger.log_fill(calce3)

# Build dataframe and print summary
logger.build_dataframe()
logger.print_summary()

# Export to CSV
filepath = logger.export_to_csv()

print(f"\n✅ Test complete! Check file: {filepath}")
print("\nDataFrame preview:")
print(logger.trades_df[['symbol', 'direction', 'quantity', 'expected_price', 'fill_price', 'slippage', 'commission']])
