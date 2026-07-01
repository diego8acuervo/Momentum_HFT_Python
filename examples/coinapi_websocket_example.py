#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CoinAPI WebSocket Streaming Example

This example demonstrates how to use the CoinApiDs class for real-time
market data streaming from CoinAPI WebSocket API.

Requirements:
- COINAPI_KEY environment variable must be set
- websocket-client library installed

Author: Diego Ochoa
Created: January 6, 2025
"""

import sys
import os
import queue
import time
from datetime import datetime

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Datos import CoinApiDs
from Eventos import EventoMdo


def example_1_basic_setup():
    """
    Example 1: Basic Setup
    Connect to CoinAPI WebSocket and receive real-time OHLCV data
    """
    print("\n" + "="*70)
    print("EXAMPLE 1: Basic CoinAPI WebSocket Setup")
    print("="*70 + "\n")
    
    # Initialize event queue
    eventos = queue.Queue()
    
    # Define trading pairs
    exchanges = ['BINANCEFTS']
    book_types = ['PERP']
    symbols = ['BTC', 'ETH']
    
    # Create CoinApiDs instance
    print("Initializing CoinAPI WebSocket connection...")
    coinapi_data = CoinApiDs(
        eventos=eventos,
        lista_bolsas=exchanges,
        lista_libros=book_types,
        lista_nemos=symbols,
        interval='1MIN'
    )
    
    print("Listening for data updates (press Ctrl+C to stop)...\n")
    
    try:
        # Process events for 60 seconds
        start_time = time.time()
        event_count = 0
        
        while time.time() - start_time < 60:
            try:
                # Get event from queue (non-blocking with timeout)
                evento = eventos.get(timeout=1)
                event_count += 1
                
                if evento.type == 'MERCADO':
                    print(f"[{evento.timestamp}] {evento.symbol} - "
                          f"Type: {evento.typeEvent} - "
                          f"Close: ${evento.close:.2f} - "
                          f"Volume: {evento.volume:.2f}")
                
            except queue.Empty:
                continue
        
        print(f"\nReceived {event_count} events in 60 seconds")
        
    except KeyboardInterrupt:
        print("\n\nStopping data stream...")
    finally:
        # Clean shutdown
        coinapi_data.disconnect_websocket()
        print("Disconnected from CoinAPI WebSocket")


def example_2_latest_data_access():
    """
    Example 2: Accessing Latest Data
    Demonstrate different methods to access the latest market data
    """
    print("\n" + "="*70)
    print("EXAMPLE 2: Accessing Latest Data")
    print("="*70 + "\n")
    
    eventos = queue.Queue()
    exchanges = ['BINANCEFTS']
    book_types = ['PERP']
    symbols = ['BTC', 'ETH', 'SOL']
    
    coinapi_data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')
    
    print("Waiting for initial data to arrive...")
    time.sleep(10)  # Wait for first candles
    
    try:
        # Access latest candle for each symbol
        for symbol in symbols:
            latest = coinapi_data.get_ultima_vela(symbol)
            if latest:
                print(f"\n{symbol} - Latest Candle:")
                print(f"  Open: ${latest.get('open', 0):.2f}")
                print(f"  High: ${latest.get('high', 0):.2f}")
                print(f"  Low: ${latest.get('low', 0):.2f}")
                print(f"  Close: ${latest.get('close', 0):.2f}")
                print(f"  Volume: {latest.get('volume', 0):.2f}")
                print(f"  Time: {latest.get('open_time', 'N/A')}")
        
        # Get specific values
        print("\n" + "-"*50)
        print("Getting specific values:")
        for symbol in symbols:
            close_price = coinapi_data.get_valor_ultima_vela(symbol, 'close')
            volume = coinapi_data.get_valor_ultima_vela(symbol, 'volume')
            print(f"{symbol}: Close=${close_price:.2f}, Volume={volume:.2f}")
        
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        coinapi_data.disconnect_websocket()


def example_3_generator_pattern():
    """
    Example 3: Using Generator Pattern
    Stream candles with historical context for strategy development
    """
    print("\n" + "="*70)
    print("EXAMPLE 3: Generator Pattern for Strategy Development")
    print("="*70 + "\n")
    
    eventos = queue.Queue()
    exchanges = ['BINANCEFTS']
    book_types = ['PERP']
    symbols = ['BTC']
    
    coinapi_data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')
    
    print("Starting candle generator with 50-candle buffer...")
    print("Press Ctrl+C to stop\n")
    
    try:
        candle_count = 0
        for latest, buffer in coinapi_data.get_kline_generator('BTC', lookback=50):
            candle_count += 1
            
            print(f"\nCandle #{candle_count}:")
            print(f"  Time: {latest.get('close_time', 'N/A')}")
            print(f"  Close: ${latest.get('close', 0):.2f}")
            print(f"  Buffer size: {len(buffer)} candles")
            
            # Example: Calculate simple moving average from buffer
            if len(buffer) >= 20:
                closes = buffer['close'].tail(20)
                sma_20 = closes.mean()
                print(f"  SMA(20): ${sma_20:.2f}")
                
                # Example: Detect trend
                if latest.get('close', 0) > sma_20:
                    print(f"  💹 Price above SMA - BULLISH")
                else:
                    print(f"  📉 Price below SMA - BEARISH")
            
            # Stop after 10 candles for demo
            if candle_count >= 10:
                break
                
    except KeyboardInterrupt:
        print("\n\nStopping generator...")
    finally:
        coinapi_data.disconnect_websocket()


def example_4_multi_symbol_monitoring():
    """
    Example 4: Multi-Symbol Monitoring
    Monitor multiple cryptocurrency pairs simultaneously
    """
    print("\n" + "="*70)
    print("EXAMPLE 4: Multi-Symbol Real-Time Monitoring")
    print("="*70 + "\n")
    
    eventos = queue.Queue()
    exchanges = ['BINANCEFTS']
    book_types = ['PERP']
    symbols = ['BTC', 'ETH', 'SOL', 'ADA', 'DOT']
    
    coinapi_data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')
    
    print("Monitoring 5 cryptocurrency pairs...")
    print("Collecting data for 30 seconds...\n")
    
    try:
        start_time = time.time()
        updates = {symbol: 0 for symbol in symbols}
        
        while time.time() - start_time < 30:
            try:
                evento = eventos.get(timeout=1)
                
                if evento.type == 'MERCADO' and evento.typeEvent == 'ohlcv':
                    updates[evento.symbol] += 1
                    
                    # Print summary every 10 seconds
                    if int(time.time() - start_time) % 10 == 0:
                        print(f"\nUpdate counts after {int(time.time() - start_time)}s:")
                        for sym in symbols:
                            latest = coinapi_data.get_valor_ultima_vela(sym, 'close')
                            print(f"  {sym}: {updates[sym]} updates - Last: ${latest:.2f}")
                        
            except queue.Empty:
                continue
        
        print("\n" + "-"*50)
        print("Final Summary:")
        print("-"*50)
        for symbol in symbols:
            print(f"{symbol}: {updates[symbol]} total updates")
            
    except KeyboardInterrupt:
        print("\n\nStopping monitoring...")
    finally:
        coinapi_data.disconnect_websocket()


def example_5_event_types():
    """
    Example 5: Different Event Types
    Subscribe to OHLCV, trades, quotes, and order book updates
    """
    print("\n" + "="*70)
    print("EXAMPLE 5: Subscribing to Different Event Types")
    print("="*70 + "\n")
    
    eventos = queue.Queue()
    exchanges = ['BINANCEFTS']
    book_types = ['PERP']
    symbols = ['BTC']
    
    # Note: To subscribe to different event types, modify the Hello message
    # in the CoinApiDs class by changing subscribe_data_type list
    coinapi_data = CoinApiDs(eventos, exchanges, book_types, symbols, interval='1MIN')
    
    print("Listening for different event types...")
    print("Press Ctrl+C to stop\n")
    
    event_counts = {'ohlcv': 0, 'trade': 0, 'quote': 0, 'book': 0}
    
    try:
        start_time = time.time()
        
        while time.time() - start_time < 60:
            try:
                evento = eventos.get(timeout=1)
                
                if evento.type == 'MERCADO':
                    event_type = evento.typeEvent
                    event_counts[event_type] = event_counts.get(event_type, 0) + 1
                    
                    if event_type == 'ohlcv':
                        print(f"📊 OHLCV: {evento.symbol} @ ${evento.close:.2f}")
                    elif event_type == 'trade':
                        print(f"💹 Trade: {evento.symbol} @ ${evento.price:.2f} x {evento.quantity:.4f}")
                    elif event_type == 'quote':
                        print(f"📈 Quote: {evento.symbol} Bid: ${evento.best_bid:.2f} Ask: ${evento.best_ask:.2f}")
                    elif event_type == 'book':
                        print(f"📚 Book: {evento.symbol} updated")
                        
            except queue.Empty:
                continue
        
        print("\n" + "-"*50)
        print("Event Type Summary:")
        print("-"*50)
        for event_type, count in event_counts.items():
            print(f"{event_type}: {count} events")
            
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        coinapi_data.disconnect_websocket()


if __name__ == "__main__":
    """
    Run all examples or select specific ones
    """
    print("\n" + "="*70)
    print("CoinAPI WebSocket Streaming Examples")
    print("="*70)
    
    # Check if API key is set
    if not os.environ.get("COINAPI_KEY"):
        print("\n❌ ERROR: COINAPI_KEY environment variable not set!")
        print("Please set your CoinAPI key:")
        print("  export COINAPI_KEY='your-api-key-here'")
        sys.exit(1)
    
    print("\nAvailable examples:")
    print("1. Basic Setup - Connect and receive OHLCV data")
    print("2. Latest Data Access - Access current market data")
    print("3. Generator Pattern - Stream with historical context")
    print("4. Multi-Symbol Monitoring - Monitor multiple pairs")
    print("5. Event Types - Different message types")
    print("\nRunning Example 1 (Basic Setup)...")
    print("To run other examples, modify the __main__ section\n")
    
    try:
        # Run example 1 by default
        example_1_basic_setup()
        
        # Uncomment to run other examples:
        # example_2_latest_data_access()
        # example_3_generator_pattern()
        # example_4_multi_symbol_monitoring()
        # example_5_event_types()
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*70)
    print("Examples completed!")
    print("="*70 + "\n")
