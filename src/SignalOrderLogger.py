# -*- coding: utf-8 -*-
"""
Signal and Order Logger Module
Tracks signal generation and order creation to debug quantity calculations
"""

import pandas as pd
import datetime
import os


class SignalOrderLogger:
    """
    Logs all signal (EventoSenal) and order (EventoOrden) events
    Helps debug the signal → quantity → order pipeline
    Tracks where large quantities are coming from
    """
    
    def __init__(self, symbols, outputs_dir='outputs', bq_logger=None, account=None):
        """
        Initialize the signal/order logger

        Parameters:
            symbols: tuple/list of trading symbols (e.g., ('ETH', 'XRP'))
            outputs_dir: directory to save logs
            bq_logger: optional BQLogger instance for BigQuery dual-write
            account: account name for BQ row tagging (e.g. 'binance_live')
        """
        self.symbols = symbols
        self.outputs_dir = outputs_dir
        self.bq_logger = bq_logger
        self.account = account
        self.pair = f"{symbols[0]}_{symbols[1]}" if len(symbols) >= 2 else str(symbols)
        
        # Create outputs directory if it doesn't exist
        os.makedirs(self.outputs_dir, exist_ok=True)
        
        # Lists to store signals and orders
        self.signals = []
        self.orders = []
        
        # DataFrames
        self.signals_df = None
        self.orders_df = None
        
        print(f"📊 SignalOrderLogger initialized for {symbols[0]}/{symbols[1]}")
        
    def log_signal(self, evento_senal, z_score=None, spread=None, hedge_ratio=None):
        """
        Log a signal event (EventoSenal)
        
        Parameters:
            evento_senal: EventoSenal object
            z_score: Z-score that triggered the signal (optional)
            spread: Spread value (optional)
            hedge_ratio: Hedge ratio used (optional)
        """
        signal_data = {
            'timestamp': evento_senal.datetime if evento_senal.datetime else datetime.datetime.now(),
            'symbol': evento_senal.nemo,
            'signal_type': evento_senal.tipo_senal,
            'signal_strength': evento_senal.fuerza,  # Signal strength/intensity (not quantity - that's calculated by portfolio)
            'z_score': z_score,
            'spread': spread,
            'hedge_ratio': hedge_ratio,
        }
        
        self.signals.append(signal_data)

        if self.bq_logger:
            self.bq_logger.log("signals", {
                **signal_data,
                "account": self.account,
                "pair": self.pair,
                "timestamp": str(signal_data["timestamp"]),
            })

        z_str = f"{z_score:.4f}" if z_score is not None else 'N/A'
        hr_str = f"{hedge_ratio:.4f}" if hedge_ratio is not None else 'N/A'
        strength_str = f"{evento_senal.fuerza:.4f}" if evento_senal.fuerza else 'N/A'
        print(f"📡 Signal logged: {evento_senal.nemo} {evento_senal.tipo_senal} strength={strength_str} | z={z_str} | hr={hr_str}")
        
    def log_order(self, evento_orden, signal_type=None, cash_available=None, 
                  price_used=None, atr_value=None, risk_pct=None, 
                  raw_quantity=None, final_quantity=None):
        """
        Log an order event (EventoOrden) with detailed quantity calculation info
        
        Parameters:
            evento_orden: EventoOrden object
            signal_type: Original signal type that triggered this order
            cash_available: Cash available for the trade
            price_used: Price used in quantity calculation
            atr_value: ATR value used for position sizing (if applicable)
            risk_pct: Risk percentage used (if applicable)
            raw_quantity: Quantity before rounding/flooring
            final_quantity: Final quantity in the order
        """
        order_data = {
            'timestamp': evento_orden.timestamp if evento_orden.timestamp else datetime.datetime.now(),
            'symbol': evento_orden.nemo,
            'direction': evento_orden.direccion,
            'order_type': evento_orden.tipo_orden,
            'order_quantity': evento_orden.cantidad,
            'order_price': evento_orden.precio,
            'signal_type': signal_type,
            'cash_available': cash_available,
            'price_used': price_used,
            'atr_value': atr_value,
            'risk_pct': risk_pct,
            'raw_quantity': raw_quantity,
            'final_quantity': final_quantity,
            'notional_value': evento_orden.cantidad * evento_orden.precio if evento_orden.precio else None,
        }
        
        self.orders.append(order_data)

        if self.bq_logger:
            self.bq_logger.log("orders", {
                **order_data,
                "account": self.account,
                "pair": self.pair,
                "timestamp": str(order_data["timestamp"]),
            })

        notional = order_data['notional_value']
        precio_str = f"{evento_orden.precio:.4f}" if evento_orden.precio else 'N/A'
        notional_str = f"${notional:.2f}" if notional else 'N/A'
        cash_str = f"${cash_available:.2f}" if cash_available else 'N/A'
        print(f"📝 Order logged: {evento_orden.nemo} {evento_orden.direccion} qty={evento_orden.cantidad:.4f} @ {precio_str} | notional={notional_str} | cash={cash_str}")
        
    def log_quantity_calculation(self, symbol, step_name, value, description=""):
        """
        Log intermediate steps in quantity calculation for debugging
        
        Parameters:
            symbol: Trading symbol
            step_name: Name of the calculation step
            value: Calculated value
            description: Additional description
        """
        calc_data = {
            'timestamp': datetime.datetime.now(),
            'symbol': symbol,
            'step': step_name,
            'value': value,
            'description': description,
        }
        
        print(f"  🔢 {symbol} | {step_name}: {value} {description}")
        
    def build_dataframes(self):
        """
        Build pandas DataFrames from signal and order records
        """
        # Build signals DataFrame
        if self.signals:
            self.signals_df = pd.DataFrame(self.signals)
            self.signals_df = self.signals_df.sort_values('timestamp')
            print(f"📊 Signals DataFrame built with {len(self.signals_df)} signals")
        else:
            print("📊 No signals to log")
            self.signals_df = pd.DataFrame()
        
        # Build orders DataFrame
        if self.orders:
            self.orders_df = pd.DataFrame(self.orders)
            self.orders_df = self.orders_df.sort_values('timestamp')
            
            # Calculate quantity-to-cash ratio
            self.orders_df['qty_to_cash_ratio'] = self.orders_df.apply(
                lambda row: (row['order_quantity'] * row['price_used'] / row['cash_available']) 
                if row['cash_available'] and row['price_used'] else None, 
                axis=1
            )
            
            print(f"📊 Orders DataFrame built with {len(self.orders_df)} orders")
        else:
            print("📊 No orders to log")
            self.orders_df = pd.DataFrame()
        
        return self.signals_df, self.orders_df
    
    def export_to_csv(self):
        """
        Export signal and order logs to CSV files
        """
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        symbol1, symbol2 = self.symbols[0], self.symbols[1]
        
        # Export signals
        if self.signals_df is not None and len(self.signals_df) > 0:
            signals_filename = f"signals_{symbol1}_{symbol2}_{timestamp}.csv"
            signals_filepath = os.path.join(self.outputs_dir, signals_filename)
            self.signals_df.to_csv(signals_filepath, index=False)
            print(f"💾 Signals log exported to: {signals_filepath}")
            print(f"   Total signals: {len(self.signals_df)}")
        else:
            print("⚠️  No signals to export")
            signals_filepath = None
        
        # Export orders
        if self.orders_df is not None and len(self.orders_df) > 0:
            orders_filename = f"orders_{symbol1}_{symbol2}_{timestamp}.csv"
            orders_filepath = os.path.join(self.outputs_dir, orders_filename)
            self.orders_df.to_csv(orders_filepath, index=False)
            print(f"💾 Orders log exported to: {orders_filepath}")
            print(f"   Total orders: {len(self.orders_df)}")
            
            # Show statistics about oversized orders
            if 'qty_to_cash_ratio' in self.orders_df.columns:
                oversized = self.orders_df[self.orders_df['qty_to_cash_ratio'] > 1.0]
                if len(oversized) > 0:
                    print(f"   ⚠️  WARNING: {len(oversized)} orders exceed available cash!")
                    print(f"   Max ratio: {self.orders_df['qty_to_cash_ratio'].max():.2f}x")
        else:
            print("⚠️  No orders to export")
            orders_filepath = None
        
        return signals_filepath, orders_filepath
    
    def print_summary(self):
        """
        Print a summary of signal and order activity
        """
        if self.signals_df is None or self.orders_df is None:
            self.build_dataframes()
        
        print("\n" + "="*80)
        print("📊 SIGNAL & ORDER SUMMARY")
        print("="*80)
        
        # Signals summary
        if len(self.signals_df) > 0:
            print("\n🎯 SIGNALS:")
            print(f"  Total signals: {len(self.signals_df)}")
            print(f"  Signals by type:")
            for signal_type, count in self.signals_df['signal_type'].value_counts().items():
                print(f"    {signal_type}: {count}")
            print(f"  Signals by symbol:")
            for symbol, count in self.signals_df['symbol'].value_counts().items():
                print(f"    {symbol}: {count}")
            
            if 'hedge_ratio' in self.signals_df.columns:
                avg_hr = self.signals_df['hedge_ratio'].mean()
                print(f"  Average hedge ratio: {avg_hr:.4f}")
        else:
            print("\n🎯 SIGNALS: None generated")
        
        # Orders summary
        if len(self.orders_df) > 0:
            print("\n📋 ORDERS:")
            print(f"  Total orders: {len(self.orders_df)}")
            print(f"  Orders by direction:")
            for direction, count in self.orders_df['direction'].value_counts().items():
                print(f"    {direction}: {count}")
            print(f"  Orders by symbol:")
            for symbol, count in self.orders_df['symbol'].value_counts().items():
                print(f"    {symbol}: {count}")
            
            # Quantity analysis
            print(f"\n💰 QUANTITY ANALYSIS:")
            print(f"  Average order quantity: {self.orders_df['order_quantity'].mean():.4f}")
            print(f"  Max order quantity: {self.orders_df['order_quantity'].max():.4f}")
            print(f"  Min order quantity: {self.orders_df['order_quantity'].min():.4f}")
            
            if 'notional_value' in self.orders_df.columns:
                print(f"  Average notional value: ${self.orders_df['notional_value'].mean():.2f}")
                print(f"  Max notional value: ${self.orders_df['notional_value'].max():.2f}")
            
            if 'cash_available' in self.orders_df.columns:
                avg_cash = self.orders_df['cash_available'].mean()
                print(f"  Average cash available: ${avg_cash:.2f}")
            
            # Check for oversized orders
            if 'qty_to_cash_ratio' in self.orders_df.columns:
                print(f"\n⚠️  OVERSIZED ORDER CHECK:")
                oversized = self.orders_df[self.orders_df['qty_to_cash_ratio'] > 1.0]
                if len(oversized) > 0:
                    print(f"  Orders exceeding cash: {len(oversized)}/{len(self.orders_df)}")
                    print(f"  Max overage: {self.orders_df['qty_to_cash_ratio'].max():.2f}x available cash")
                    print(f"  Affected symbols: {oversized['symbol'].unique().tolist()}")
                else:
                    print(f"  ✅ All orders within cash limits")
            
            # ATR analysis if available
            if 'atr_value' in self.orders_df.columns and self.orders_df['atr_value'].notna().any():
                print(f"\n📈 ATR-BASED SIZING:")
                print(f"  Average ATR: {self.orders_df['atr_value'].mean():.4f}")
                print(f"  Average risk %: {self.orders_df['risk_pct'].mean():.2f}%" if 'risk_pct' in self.orders_df.columns else "  Risk %: N/A")
        else:
            print("\n📋 ORDERS: None created")
        
        print("="*80 + "\n")
    
    def get_diagnostic_info(self):
        """
        Get diagnostic information about quantity calculations
        Returns a dictionary with key metrics
        """
        if self.orders_df is None or len(self.orders_df) == 0:
            return {"error": "No orders logged"}
        
        diagnostics = {
            'total_orders': len(self.orders_df),
            'total_signals': len(self.signals_df) if self.signals_df is not None else 0,
            'avg_order_quantity': self.orders_df['order_quantity'].mean(),
            'max_order_quantity': self.orders_df['order_quantity'].max(),
            'avg_notional': self.orders_df['notional_value'].mean() if 'notional_value' in self.orders_df.columns else None,
            'max_notional': self.orders_df['notional_value'].max() if 'notional_value' in self.orders_df.columns else None,
            'avg_cash_available': self.orders_df['cash_available'].mean() if 'cash_available' in self.orders_df.columns else None,
            'oversized_orders': len(self.orders_df[self.orders_df['qty_to_cash_ratio'] > 1.0]) if 'qty_to_cash_ratio' in self.orders_df.columns else None,
            'max_qty_to_cash_ratio': self.orders_df['qty_to_cash_ratio'].max() if 'qty_to_cash_ratio' in self.orders_df.columns else None,
        }
        
        return diagnostics
