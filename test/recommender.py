import os, sys, time, datetime, threading
import pandas as pd
import numpy as np
import mibian  # Lib for Options Greeks
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker
from colorama import Fore, Back, Style, init
from tabulate import tabulate

init(autoreset=True)

# --- CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""

# --- GLOBAL DATA STORE ---
market_data = {
    "NIFTY": {"LTP": 0, "RSI": 0, "Trend": "NEUTRAL"},
    "SENSEX": {"LTP": 0, "RSI": 0, "Trend": "NEUTRAL"}
}
positions_data = {}  # Stores live LTP of your holding instruments
advice_log = {}      # Stores the last advice given

# --- 1. GREEKS & MATH ENGINE ---
class OptionsMath:
    @staticmethod
    def get_greeks(ltp, strike, spot, days_to_expiry, type='CE'):
        """Calculates Delta, Theta, and IV"""
        if days_to_expiry <= 0: days_to_expiry = 0.1 # Avoid divide by zero
        
        # Approximate Risk Free Rate = 10%
        # We solve for IV first based on market price (Reverse Engineering)
        try:
            type_code = 'C' if type == 'CE' else 'P'
            # mibian.BS([Underlying, Strike, Interest, Days], CallPrice=LTP)
            c = mibian.BS([spot, strike, 10, days_to_expiry], callPrice=ltp) if type_code == 'C' \
                else mibian.BS([spot, strike, 10, days_to_expiry], putPrice=ltp)
            
            iv = c.impliedVolatility
            
            # Now get Greeks using that IV
            g = mibian.BS([spot, strike, 10, days_to_expiry], volatility=iv)
            
            return {
                "IV": iv,
                "Delta": g.callDelta if type_code == 'C' else g.putDelta,
                "Theta": g.callTheta if type_code == 'C' else g.putTheta,
                "Gamma": g.gamma
            }
        except:
            return {"IV": 0, "Delta": 0, "Theta": 0, "Gamma": 0}

    @staticmethod
    def get_days_to_expiry(symbol):
        # Very basic parser. In production, parse real expiry dates.
        # Assuming current week expiry for demo simplicity
        today = datetime.datetime.now().date()
        # Logic to find next Thursday/Wednesday would go here
        # For now, returning a static '3 days' for math stability
        return 3 

# --- 2. AI ADVISOR ENGINE ---
class TradeAdvisor:
    @staticmethod
    def analyze(pos, index_stats):
        """
        The Brain: Decides Sell/Hold/Add
        Inputs: Position Dict, Index Technicals
        """
        advice = "WAIT"
        reason = "Analyzing..."
        color = Fore.WHITE

        pnl = pos['pnl']
        type = "CE" if "CE" in pos['symbol'] else "PE"
        
        # 1. STOP LOSS CHECK (Hard Rule)
        # If loss > 15% of capital invested in this trade
        invested = pos['avg'] * pos['qty']
        if pnl < -(invested * 0.15):
            return f"{Back.RED} 🛑 CRITICAL EXIT {Style.RESET_ALL}", "Stoploss Hit"

        # 2. THETA DECAY WARNING
        # If Theta is high (eating >10% of premium daily) and Index is flat
        if abs(pos['greeks']['Theta']) > (pos['ltp'] * 0.10) and index_stats['Trend'] == "SIDEWAYS":
            return f"{Fore.RED}REDUCE/EXIT", "High Theta Burn"

        # 3. DIRECTIONAL LOGIC
        rsi = index_stats['RSI']
        
        if type == "CE":
            if rsi > 60 and index_stats['Trend'] == "BULLISH":
                if pos['greeks']['Delta'] > 0.5:
                    advice = f"{Back.GREEN} 💰 ADD MORE {Style.RESET_ALL}"
                    reason = "Strong Mom + High Delta"
                else:
                    advice = f"{Fore.GREEN}HOLD"
                    reason = "Trend Up"
            elif rsi < 40:
                advice = f"{Fore.RED}SELL"
                reason = "Index Bearish"
            elif rsi > 80:
                advice = f"{Fore.YELLOW}BOOK PROFIT"
                reason = "Index Overbought"
                
        elif type == "PE":
            if rsi < 40 and index_stats['Trend'] == "BEARISH":
                if pos['greeks']['Delta'] < -0.5:
                    advice = f"{Back.GREEN} 💰 ADD MORE {Style.RESET_ALL}"
                    reason = "Collapse + High Delta"
                else:
                    advice = f"{Fore.GREEN}HOLD"
                    reason = "Trend Down"
            elif rsi > 60:
                advice = f"{Fore.RED}SELL"
                reason = "Index Bullish"
            elif rsi < 20:
                advice = f"{Fore.YELLOW}BOOK PROFIT"
                reason = "Index Oversold"

        return advice, reason

# --- 3. DATA MANAGER ---
class GuardianCore:
    def __init__(self):
        print(f"{Fore.YELLOW}🛡️  INITIALIZING PORTFOLIO GUARDIAN...{Style.RESET_ALL}")
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        
        # Init Tokens
        self.nifty_token = 256265
        self.sensex_token = 265
        self.active_tokens = [self.nifty_token, self.sensex_token]
        
        # Start Ticker
        threading.Thread(target=self.start_ticker, daemon=True).start()
        # Start Analysis Loop
        self.run_dashboard()

    def start_ticker(self):
        def on_ticks(ws, ticks):
            for t in ticks:
                tok = t['instrument_token']
                ltp = t['last_price']
                
                # Update Market Data
                if tok == self.nifty_token: market_data["NIFTY"]["LTP"] = ltp
                elif tok == self.sensex_token: market_data["SENSEX"]["LTP"] = ltp
                
                # Update Position Data
                if tok in positions_data:
                    positions_data[tok]['ltp'] = ltp

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = lambda ws, r: ws.subscribe(self.active_tokens)
        self.kws.connect(threaded=True)

    def fetch_market_technicals(self):
        """Calculates RSI and Trend for NIFTY"""
        # Fetch 30 min candles for trend
        try:
            hist = self.kite.historical_data(self.nifty_token, 
                                           datetime.date.today() - datetime.timedelta(days=5), 
                                           datetime.date.today(), "15minute")
            df = pd.DataFrame(hist)
            if not df.empty:
                df['RSI'] = ta.rsi(df['close'], length=14)
                df['EMA'] = ta.ema(df['close'], length=20)
                
                last = df.iloc[-1]
                rsi = last['RSI']
                price = last['close']
                ema = last['EMA']
                
                trend = "SIDEWAYS"
                if price > ema and rsi > 55: trend = "BULLISH"
                elif price < ema and rsi < 45: trend = "BEARISH"
                
                market_data["NIFTY"]["RSI"] = rsi
                market_data["NIFTY"]["Trend"] = trend
        except:
            pass

    def run_dashboard(self):
        while True:
            # 1. Fetch Active Positions from Zerodha
            try:
                positions = self.kite.positions()['net']
                # Filter open positions (Qty != 0)
                active_pos = [p for p in positions if p['quantity'] != 0]
            except:
                active_pos = []

            # 2. Update Technicals
            self.fetch_market_technicals()
            
            # 3. Update Tokens for WebSocket
            new_tokens = [p['instrument_token'] for p in active_pos]
            for t in new_tokens:
                if t not in self.active_tokens:
                    self.active_tokens.append(t)
                    if self.kws.is_connected(): self.kws.subscribe([t])
            
            # 4. Build Display Data
            table_rows = []
            
            for p in active_pos:
                tok = p['instrument_token']
                sym = p['tradingsymbol']
                qty = p['quantity']
                avg = p['average_price']
                
                # Get Live LTP from Websocket (or API fallback)
                ltp = positions_data.get(tok, {}).get('ltp', p['last_price'])
                positions_data[tok] = {'ltp': ltp} # Ensure dict exists
                
                # Calculate P&L
                cur_val = qty * ltp
                buy_val = qty * avg
                pnl = cur_val - buy_val
                pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
                
                # Extract Strike/Details for Greeks
                # (Simple parsing: NIFTY24JAN21500CE)
                strike = 0
                import re
                nums = re.findall(r'\d+', sym)
                if len(nums) > 0: strike = int(nums[-1])
                
                # Calculate Greeks
                idx_ltp = market_data["NIFTY"]["LTP"]
                days = OptionsMath.get_days_to_expiry(sym)
                greeks = OptionsMath.get_greeks(ltp, strike, idx_ltp, days, 'CE' if 'CE' in sym else 'PE')
                
                # Prepare Position Dict for Advisor
                pos_summary = {
                    'symbol': sym, 'qty': qty, 'avg': avg, 'ltp': ltp, 
                    'pnl': pnl, 'greeks': greeks
                }
                
                # GET RECOMMENDATION
                rec, reason = TradeAdvisor.analyze(pos_summary, market_data["NIFTY"])
                
                # Format Row
                table_rows.append([
                    f"{Fore.CYAN}{sym}{Style.RESET_ALL}",
                    qty,
                    f"{avg:.2f}",
                    f"{Style.BRIGHT}{ltp:.2f}",
                    f"{pnl_color}{pnl:.2f}{Style.RESET_ALL}",
                    f"Δ:{greeks['Delta']:.2f} | θ:{greeks['Theta']:.2f}",
                    rec,
                    f"{Fore.WHITE}{reason}"
                ])

            # 5. RENDER
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"{Back.BLUE}{Fore.WHITE} 🛡️  PORTFOLIO GUARDIAN (Live Monitor) {Style.RESET_ALL}")
            
            # Market Health Bar
            n_trend = market_data["NIFTY"]["Trend"]
            n_col = Fore.GREEN if "BULL" in n_trend else (Fore.RED if "BEAR" in n_trend else Fore.YELLOW)
            print(f"NIFTY: {market_data['NIFTY']['LTP']} | RSI: {market_data['NIFTY']['RSI']:.1f} | Trend: {n_col}{n_trend}{Style.RESET_ALL}")
            print("-" * 100)
            
            if not table_rows:
                print("\nNo Active Positions found in Zerodha Account.")
            else:
                headers = ["Symbol", "Qty", "Avg", "LTP", "P&L", "Greeks", "🤖 RECOMMENDATION", "Reason"]
                print(tabulate(table_rows, headers=headers, tablefmt="simple"))
                
            time.sleep(1)

if __name__ == "__main__":
    app = GuardianCore()