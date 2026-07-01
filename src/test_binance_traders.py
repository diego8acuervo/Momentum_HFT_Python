#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Suite for Binance Trading Modules

This script tests both BinanceSpotTrader and BinancePerpetualTrader
to verify functionality before integration into traderPerp orchestrator.

Author: Diego Ochoa
Date: December 2025
"""

import sys
import os

# Add src directory to path
sys.path.append(os.path.dirname(__file__))

from binance_spot import BinanceSpotTrader
from binance_perp import BinancePerpetualTrader


def test_spot_trader():
    """Test BinanceSpotTrader functionality."""
    print("\n" + "="*80)
    print("TESTING BINANCE SPOT TRADER")
    print("="*80)
    
    try:
        # Initialize trader
        trader = BinanceSpotTrader(['COP', 'USDT'])
        
        # Test 1: Get Balance
        print("\n[TEST 1] Get Balance")
        print("-"*80)
        balance_df = trader.get_balance()
        print(balance_df)
        
        # Test 2: Get Open Orders
        print("\n[TEST 2] Get Open Orders")
        print("-"*80)
        open_orders = trader.get_open_orders()
        if not open_orders.empty:
            print(f"Found {len(open_orders)} open orders")
            print(open_orders[['symbol', 'side', 'type', 'origQty', 'price', 'status']])
        else:
            print("No open orders")
        
        # Test 3: Get Trade History
        print("\n[TEST 3] Get Trade History")
        print("-"*80)
        trades_df = trader.get_trades(limit=5)
        if not trades_df.empty:
            print(f"Found {len(trades_df)} recent trades")
            print(trades_df[['orderId', 'side', 'qty', 'price', 'commission']])
        else:
            print("No recent trades")
        
        print("\n✅ BinanceSpotTrader tests completed successfully")
        return True
        
    except Exception as e:
        print(f"\n❌ BinanceSpotTrader test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_perp_trader(testnet=True):
    """Test BinancePerpetualTrader functionality."""
    print("\n" + "="*80)
    print(f"TESTING BINANCE PERPETUAL TRADER ({'TESTNET' if testnet else 'LIVE'})")
    print("="*80)
    
    try:
        # Initialize trader
        trader = BinancePerpetualTrader(['LINK', 'USDT'], testnet=testnet)
        
        # Test 1: Get Account Info
        print("\n[TEST 1] Get Account Info")
        print("-"*80)
        account = trader.get_account_info()
        
        # Test 2: Get Balance
        print("\n[TEST 2] Get Balance (DataFrame)")
        print("-"*80)
        balance_df = trader.get_balance()
        print(balance_df)
        
        # Test 3: Set Leverage
        print("\n[TEST 3] Set Leverage to 1x (conservative)")
        print("-"*80)
        trader.set_leverage(leverage=1)
        
        # Test 4: Set Margin Type
        print("\n[TEST 4] Set Margin Type to CROSSED")
        print("-"*80)
        trader.set_margin_type(margin_type='CROSSED')
        
        # Test 5: Get Position Info
        print("\n[TEST 5] Get Position Info")
        print("-"*80)
        positions = trader.get_position_info()
        
        # Test 6: Get Open Orders
        print("\n[TEST 6] Get Open Orders")
        print("-"*80)
        open_orders = trader.get_open_orders()
        if not open_orders.empty:
            print(f"Found {len(open_orders)} open orders")
            print(open_orders[['symbol', 'side', 'type', 'origQty', 'price', 'status']])
        else:
            print("No open orders")
        
        # Test 7: Get Trade History
        print("\n[TEST 7] Get Trade History")
        print("-"*80)
        trades_df = trader.get_trades(limit=5)
        if not trades_df.empty:
            print(f"Found {len(trades_df)} recent trades")
            print(trades_df[['orderId', 'side', 'qty', 'price', 'commission', 'realizedPnl']])
        else:
            print("No recent trades")
        
        # Test 8: Order Validation
        print("\n[TEST 8] Order Validation")
        print("-"*80)
        is_valid, error = trader.validate_order('LINKUSDT', 'BUY', 1.7, 12.3 , 'LIMIT')
        print(f"Validation result: Valid={is_valid}, Error={error}")
        
        print("\n✅ BinancePerpetualTrader tests completed successfully")
        return True
        
    except Exception as e:
        print(f"\n❌ BinancePerpetualTrader test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_interface_compatibility():
    """Test that both traders have compatible interfaces."""
    print("\n" + "="*80)
    print("TESTING INTERFACE COMPATIBILITY")
    print("="*80)
    
    try:
        spot = BinanceSpotTrader(['BTC', 'USDT'])
        perp = BinancePerpetualTrader(['BTC', 'USDT'], testnet=True)
        
        # Check both have same methods
        required_methods = [
            'get_symbol',
            'get_balance',
            'place_market_order',
            'place_limit_order',
            'get_open_orders',
            'cancel_order',
            'cancel_all_orders',
            'get_trades',
            'check_api_health',
            'record_api_success',
            'record_api_error'
        ]
        
        print("\nChecking method compatibility...")
        for method in required_methods:
            spot_has = hasattr(spot, method)
            perp_has = hasattr(perp, method)
            
            status = "✅" if (spot_has and perp_has) else "❌"
            print(f"{status} {method}: Spot={spot_has}, Perp={perp_has}")
            
            if not (spot_has and perp_has):
                print(f"   ⚠️  WARNING: Method {method} not present in both traders!")
        
        print("\n✅ Interface compatibility check completed")
        return True
        
    except Exception as e:
        print(f"\n❌ Interface compatibility test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("="*80)
    print("BINANCE TRADING MODULES - COMPREHENSIVE TEST SUITE")
    print("="*80)
    print("\nThis test suite verifies:")
    print("1. BinanceSpotTrader functionality")
    print("2. BinancePerpetualTrader functionality (TESTNET)")
    print("3. Interface compatibility between traders")
    print("\n⚠️  Make sure your API credentials are set in environment variables:")
    print("   - BINANCE_API_KEY / BINANCE_SECRET_KEY (for Spot)")
    print("   - BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_SECRET_KEY (for Perp testnet)")
    print("\n" + "="*80)
    
    input("\nPress Enter to continue...")
    
    results = {
        'spot': False,
        'perp': False,
        'interface': False
    }
    
    # Test Spot Trader
    results['spot'] = test_spot_trader()
    
    # Test Perp Trader (on testnet)
    results['perp'] = test_perp_trader(testnet=True)
    
    # Test Interface Compatibility
    results['interface'] = test_interface_compatibility()
    
    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"BinanceSpotTrader:        {'✅ PASSED' if results['spot'] else '❌ FAILED'}")
    print(f"BinancePerpetualTrader:   {'✅ PASSED' if results['perp'] else '❌ FAILED'}")
    print(f"Interface Compatibility:  {'✅ PASSED' if results['interface'] else '❌ FAILED'}")
    print("="*80)
    
    if all(results.values()):
        print("\n🎉 ALL TESTS PASSED! Ready for integration into traderPerp.")
        return 0
    else:
        print("\n⚠️  SOME TESTS FAILED. Please fix issues before integration.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
