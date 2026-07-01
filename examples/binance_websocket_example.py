#!/usr/bin/env python3
"""
Example usage of BinanceData class with WebSocket streaming.

This script demonstrates the different methods for accessing real-time
kline (candlestick) data from Binance.

Author: Diego Ochoa
Date: 2024
"""

import sys
import os
import queue
import time
import logging

# Add parent directory to path to import Datos
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from Datos import BinanceData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def example_1_basic_setup():
    """
    Example 1: Basic setup with automatic WebSocket streaming.
    
    The simplest way to get real-time data - initialize the class
    and data automatically streams in the background.
    """
    print("\n" + "="*60)
    print("EXAMPLE 1: Basic Setup")
    print("="*60)
    
    # Create event queue and define symbols
    eventos = queue.Queue()
    symbols = ['BTC', 'ETH', 'SOL']
    
    # Initialize connection (WebSocket starts automatically)
    binance_data = BinanceData(eventos, symbols, interval='1m')
    
    # Wait for some data to arrive
    logger.info("Waiting for data...")
    time.sleep(10)
    
    # Get latest completed candle for each symbol
    for symbol in symbols:
        latest = binance_data.get_latest_kline(symbol)
        if latest:
            logger.info(f"{symbol}: Close=${latest['close']:.2f}, Volume={latest['volume']:.2f}")
        else:
            logger.warning(f"{symbol}: No data yet")
    
    # Clean shutdown
    binance_data.disconnect_websocket()
    logger.info("Example 1 completed")


def example_2_generator_pattern():
    """
    Example 2: Using generator for continuous streaming.
    
    The generator pattern is ideal for strategies that need to
    process each candle as it completes.
    """
    print("\n" + "="*60)
    print("EXAMPLE 2: Generator Pattern")
    print("="*60)
    
    eventos = queue.Queue()
    symbols = ['BTC']
    
    binance_data = BinanceData(eventos, symbols, interval='1s')
    
    # Wait for initial connection
    time.sleep(5)
    
    logger.info("Starting kline generator (will run for 5 candles)...")
    
    # Process candles as they arrive
    candle_count = 0
    for kline, buffer in binance_data.get_kline_generator('BTC', lookback=50):
        candle_count += 1
        
        # Calculate simple moving average from buffer
        if len(buffer) >= 5:
            recent_closes = [k['close'] for k in buffer[-5:]]
            sma_5 = sum(recent_closes) / len(recent_closes)
            sma_info = f", SMA(5)={sma_5:.2f}"
        else:
            sma_info = ""
        
        logger.info(
            f"New BTC candle #{candle_count}: "
            f"Time={kline['open_time']}, "
            f"O={kline['open']:.2f}, "
            f"H={kline['high']:.2f}, "
            f"L={kline['low']:.2f}, "
            f"C={kline['close']:.2f}, "
            f"V={kline['volume']:.2f}"
            f"{sma_info} "
            f"(Buffer: {len(buffer)} candles)"
        )
        
        # Example strategy logic
        if kline['close'] > kline['open']:
            logger.info("  → Bullish candle detected!")
        else:
            logger.info("  → Bearish candle detected!")
        
        # Stop after 5 candles for demo purposes
        if candle_count >= 5:
            break
    
    binance_data.disconnect_websocket()
    logger.info("Example 2 completed")


def example_3_multi_symbol_monitoring():
    """
    Example 3: Monitor multiple symbols simultaneously.
    
    Useful for market scanning, arbitrage detection, or
    managing a portfolio of assets.
    """
    print("\n" + "="*60)
    print("EXAMPLE 3: Multi-Symbol Monitoring")
    print("="*60)
    
    eventos = queue.Queue()
    symbols = ['DOGE', 'ADA', 'AVAX', 'ETH', 'XRP']
    
    binance_data = BinanceData(eventos, symbols, interval='1m')

    logger.info("Monitoring 5 symbols for 1 minute...")
    time.sleep(60)

    # Get all latest candles at once
    all_klines = binance_data.get_all_latest_klines()
    
    logger.info(f"\nSnapshot of all {len(all_klines)} symbols:")
    for symbol, kline in sorted(all_klines.items()):
        if kline:
            change_pct = ((kline['close'] - kline['open']) / kline['open']) * 100
            logger.info(
                f"{symbol:5s}: ${kline['close']:10.2f} "
                f"({change_pct:+.2f}%) Vol={kline['volume']:.2f}"
            )
    
    binance_data.disconnect_websocket()
    logger.info("Example 3 completed")


def example_4_dynamic_subscriptions():
    """
    Example 4: Add and remove symbols dynamically.
    
    Demonstrates how to adjust the symbol list during runtime
    without restarting the connection.
    """
    print("\n" + "="*60)
    print("EXAMPLE 4: Dynamic Subscriptions")
    print("="*60)
    
    eventos = queue.Queue()
    initial_symbols = ['BTC', 'ETH']
    
    binance_data = BinanceData(eventos, initial_symbols, interval='1m')
    
    logger.info(f"Initial symbols: {initial_symbols}")
    time.sleep(10)
    
    # Add new symbol
    logger.info("Adding SOL to stream...")
    binance_data.subscribe_symbol('SOL', interval='1m')
    time.sleep(10)
    
    # Check all symbols
    all_data = binance_data.get_all_latest_klines()
    logger.info(f"Now tracking: {list(all_data.keys())}")
    
    # Remove a symbol
    logger.info("Removing ETH from stream...")
    binance_data.unsubscribe_symbol('ETH', interval='1m')
    time.sleep(5)
    
    binance_data.disconnect_websocket()
    logger.info("Example 4 completed")


def example_5_event_driven_strategy():
    """
    Example 5: Event-driven strategy using the event queue.
    
    Shows how to integrate with the event queue system for
    triggering strategy logic when new market data arrives.
    """
    print("\n" + "="*60)
    print("EXAMPLE 5: Event-Driven Strategy")
    print("="*60)
    
    eventos = queue.Queue()
    symbols = ['BTC', 'ETH']
    
    binance_data = BinanceData(eventos, symbols, interval='1m')
    
    logger.info("Waiting for market events (30 seconds)...")
    
    event_count = 0
    start_time = time.time()
    
    while time.time() - start_time < 30:
        try:
            # Non-blocking get with timeout
            event = eventos.get(timeout=1)
            event_count += 1
            
            # Process the event - check all symbols
            all_klines = binance_data.get_all_latest_klines()
            logger.info(f"Event #{event_count}: Market update received")
            
            for symbol, kline in all_klines.items():
                if kline:
                    logger.info(f"  {symbol}: ${kline['close']:.2f}")
            
            # Example: Simple moving average crossover detection
            # (In real strategy, you'd maintain state and calculate MAs)
            
        except queue.Empty:
            continue
    
    binance_data.disconnect_websocket()
    logger.info(f"Example 5 completed - Processed {event_count} events")


def example_6_error_handling():
    """
    Example 6: Handling errors and reconnections.
    
    Demonstrates the automatic reconnection features and
    how to monitor connection health.
    """
    print("\n" + "="*60)
    print("EXAMPLE 6: Error Handling & Reconnection")
    print("="*60)
    
    eventos = queue.Queue()
    symbols = ['BTC']
    
    binance_data = BinanceData(eventos, symbols, interval='1m')
    
    # Monitor connection for 60 seconds
    logger.info("Monitoring connection health...")
    
    for i in range(6):
        time.sleep(10)
        
        # Check if we're still getting data
        latest = binance_data.get_latest_kline('BTC')
        if latest:
            age = time.time() - binance_data.last_message_time.get('BTC', 0)
            logger.info(f"Check {i+1}/6: Last BTC update was {age:.1f}s ago")
            
            if age > 120:
                logger.warning("Data seems stale, connection may have issues")
            
            if not binance_data.is_running:
                logger.error("WebSocket is not running!")
                break
        else:
            logger.warning(f"Check {i+1}/6: No data received yet")
    
    binance_data.disconnect_websocket()
    logger.info("Example 6 completed")


def main():
    """
    Main function to run examples.
    
    Uncomment the examples you want to run.
    """
    print("\n" + "="*60)
    print("BinanceData WebSocket Streaming Examples")
    print("="*60)
    
    # Run individual examples (uncomment as needed)
    #example_1_basic_setup()
    #example_2_generator_pattern()
    example_3_multi_symbol_monitoring()
    #example_4_dynamic_subscriptions()
    # example_5_event_driven_strategy()
    # example_6_error_handling()
    
    logger.info("\n" + "="*60)
    logger.info("All examples completed!")
    logger.info("="*60)


if __name__ == "__main__":
    main()
