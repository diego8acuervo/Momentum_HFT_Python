# -*- coding: utf-8 -*-
"""
Trade Logger Module
Tracks all order and fill events, building a comprehensive trade log
"""

import pandas as pd
import datetime
import os


class TradeLogger:
    """
    Logs all order (EventoOrden) and fill (EventoCalce) events
    Maintains a DataFrame that progressively fills with trade data
    Exports to CSV when finished
    """
    
    def __init__(self, symbols, outputs_dir='outputs', bq_logger=None, account=None):
        """
        Initialize the trade logger

        Parameters:
            symbols: tuple/list of trading symbols (e.g., ('SOL', 'XRP'))
            outputs_dir: directory to save trade logs
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
        
        # Dictionary to track pending orders by (nemo, direction)
        # Key: (nemo, direction), Value: order dict
        self.pending_orders = {}
        
        # List to store completed trades (order + fill pairs)
        self.trades = []
        
        # DataFrame will be built from self.trades
        self.trades_df = None
        
        print(f"📊 TradeLogger initialized for {symbols[0]}/{symbols[1]}")
        
    def log_order(self, evento_orden):
        """
        Log an order event (EventoOrden)
        Stores the order data and waits for corresponding fill
        
        Parameters:
            evento_orden: EventoOrden object
        """
        order_key = (evento_orden.nemo, evento_orden.direccion.upper())  # Normalize to uppercase
        
        order_data = {
            'order_time': datetime.datetime.now(),
            'symbol': evento_orden.nemo,
            'direction': evento_orden.direccion.upper(),  # Store as uppercase
            'order_type': evento_orden.tipo_orden,
            'quantity': evento_orden.cantidad,
            'expected_price': evento_orden.precio,
        }
        
        # Store pending order
        self.pending_orders[order_key] = order_data

        if self.bq_logger:
            self.bq_logger.log("orders", {
                **order_data,
                "account": self.account,
                "pair": self.pair,
                "order_time": str(order_data["order_time"]),
            })

        print(f"📝 Order logged: {evento_orden.nemo} {evento_orden.direccion.upper()} {evento_orden.cantidad} @ {evento_orden.precio}")
        
    def log_fill(self, evento_calce):
        """
        Log a fill event (EventoCalce)
        Matches with pending order and creates completed trade record
        
        Parameters:
            evento_calce: EventoCalce object
        """
        fill_key = (evento_calce.nemo, evento_calce.direccion.upper())  # Normalize to uppercase
        
        # Try to find matching pending order
        if fill_key in self.pending_orders:
            order_data = self.pending_orders.pop(fill_key)
            
            # Calculate slippage
            expected_price = order_data['expected_price']
            actual_price = evento_calce.precioCalce
            
            if expected_price is not None and actual_price is not None:
                slippage = actual_price - expected_price
                slippage_pct = (slippage / expected_price) * 100 if expected_price != 0 else 0
            else:
                slippage = None
                slippage_pct = None
            
            # Create completed trade record
            trade_record = {
                'order_time': order_data['order_time'],
                'fill_time': evento_calce.iTiempo,
                'symbol': evento_calce.nemo,
                'direction': evento_calce.direccion,
                'order_type': order_data['order_type'],
                'quantity': evento_calce.cantidad,
                'expected_price': expected_price,
                'fill_price': actual_price,
                'slippage': slippage,
                'slippage_pct': slippage_pct,
                'commission': evento_calce.comision,
                'exchange': evento_calce.bolsa,
                'notional_value': evento_calce.cantidad * actual_price if actual_price else None,
            }
            
            self.trades.append(trade_record)

            if self.bq_logger:
                self.bq_logger.log("fills", {
                    **trade_record,
                    "account": self.account,
                    "pair": self.pair,
                    "order_time": str(trade_record.get("order_time", "")),
                    "fill_time": str(trade_record.get("fill_time", "")),
                })

            print(f"✅ Fill logged: {evento_calce.nemo} {evento_calce.direccion} {evento_calce.cantidad} @ {actual_price:.4f} (comm: {evento_calce.comision:.4f})")

        else:
            # Fill without matching order (shouldn't happen, but log it anyway)
            print(f"⚠️  Fill without matching order: {evento_calce.nemo} {evento_calce.direccion}")
            
            trade_record = {
                'order_time': None,
                'fill_time': evento_calce.iTiempo,
                'symbol': evento_calce.nemo,
                'direction': evento_calce.direccion,
                'order_type': None,
                'quantity': evento_calce.cantidad,
                'expected_price': None,
                'fill_price': evento_calce.precioCalce,
                'slippage': None,
                'slippage_pct': None,
                'commission': evento_calce.comision,
                'exchange': evento_calce.bolsa,
                'notional_value': evento_calce.cantidad * evento_calce.precioCalce if evento_calce.precioCalce else None,
            }
            
            self.trades.append(trade_record)
    
    def build_dataframe(self):
        """
        Build pandas DataFrame from trade records
        """
        if not self.trades:
            print("📊 No trades to log")
            self.trades_df = pd.DataFrame()
            return self.trades_df
        
        self.trades_df = pd.DataFrame(self.trades)
        
        # Sort by fill time
        self.trades_df = self.trades_df.sort_values('fill_time')
        
        # Calculate cumulative metrics
        self.trades_df['cumulative_commission'] = self.trades_df['commission'].cumsum()
        
        # Calculate P&L if we have pairs
        self._calculate_pnl()
        
        print(f"📊 DataFrame built with {len(self.trades_df)} trades")
        return self.trades_df
    
    def _calculate_pnl(self):
        """
        Calculate P&L for matched pairs of trades
        """
        # Group by symbol to track positions
        positions = {}
        pnl_list = []
        
        for idx, trade in self.trades_df.iterrows():
            symbol = trade['symbol']
            direction = trade['direction']
            quantity = trade['quantity']
            price = trade['fill_price']
            
            if symbol not in positions:
                positions[symbol] = {'quantity': 0, 'avg_price': 0}
            
            # Update position
            if direction.upper() in ['BUY', 'LONG']:
                positions[symbol]['quantity'] += quantity
            else:  # SELL, SHORT
                positions[symbol]['quantity'] -= quantity
            
            # Calculate realized P&L (simplified)
            if price is not None:
                if direction.upper() in ['SELL', 'SHORT'] and positions[symbol]['avg_price'] != 0:
                    pnl = quantity * (price - positions[symbol]['avg_price'])
                else:
                    pnl = 0
                    positions[symbol]['avg_price'] = price
            else:
                pnl = 0
            
            pnl_list.append(pnl)
        
        self.trades_df['realized_pnl'] = pnl_list
        self.trades_df['cumulative_pnl'] = self.trades_df['realized_pnl'].cumsum()
    
    def export_to_csv(self):
        """
        Export trade log to CSV file
        Filename format: trades_SYMBOL1_SYMBOL2_YYYYMMDD_HHMMSS.csv
        """
        if self.trades_df is None or len(self.trades_df) == 0:
            self.build_dataframe()
        
        if len(self.trades_df) == 0:
            print("⚠️  No trades to export")
            return None
        
        # Create filename with timestamp
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        symbol1, symbol2 = self.symbols[0], self.symbols[1]
        filename = f"trades_{symbol1}_{symbol2}_{timestamp}.csv"
        filepath = os.path.join(self.outputs_dir, filename)
        
        # Export to CSV
        self.trades_df.to_csv(filepath, index=False)
        
        print(f"💾 Trade log exported to: {filepath}")
        print(f"   Total trades: {len(self.trades_df)}")
        print(f"   Total commission: ${self.trades_df['commission'].sum():.2f}")
        
        if 'cumulative_pnl' in self.trades_df.columns:
            final_pnl = self.trades_df['cumulative_pnl'].iloc[-1]
            print(f"   Cumulative P&L: ${final_pnl:.2f}")
        
        return filepath
    
    def get_summary_stats(self):
        """
        Get summary statistics of trades
        """
        if self.trades_df is None or len(self.trades_df) == 0:
            return {}
        
        stats = {
            'total_trades': len(self.trades_df),
            'total_commission': self.trades_df['commission'].sum(),
            'total_notional': self.trades_df['notional_value'].sum(),
            'avg_slippage_pct': self.trades_df['slippage_pct'].mean() if 'slippage_pct' in self.trades_df.columns else 0,
            'trades_by_symbol': self.trades_df['symbol'].value_counts().to_dict(),
            'trades_by_direction': self.trades_df['direction'].value_counts().to_dict(),
        }
        
        if 'cumulative_pnl' in self.trades_df.columns:
            stats['final_pnl'] = self.trades_df['cumulative_pnl'].iloc[-1]
        
        return stats
    
    def print_summary(self):
        """
        Print a summary of trading activity
        """
        if self.trades_df is None or len(self.trades_df) == 0:
            self.build_dataframe()
        
        if len(self.trades_df) == 0:
            print("📊 No trades to summarize")
            return
        
        print("\n" + "="*60)
        print("📊 TRADE LOG SUMMARY")
        print("="*60)
        
        stats = self.get_summary_stats()
        
        print(f"\nTotal Trades: {stats['total_trades']}")
        print(f"Total Commission: ${stats['total_commission']:.2f}")
        print(f"Total Notional Value: ${stats['total_notional']:.2f}")
        
        if stats.get('avg_slippage_pct') is not None:
            print(f"Average Slippage: {stats['avg_slippage_pct']:.4f}%")
        
        print("\nTrades by Symbol:")
        for symbol, count in stats['trades_by_symbol'].items():
            print(f"  {symbol}: {count}")
        
        print("\nTrades by Direction:")
        for direction, count in stats['trades_by_direction'].items():
            print(f"  {direction}: {count}")
        
        if 'final_pnl' in stats:
            print(f"\nCumulative P&L: ${stats['final_pnl']:.2f}")
        
        print("="*60 + "\n")
