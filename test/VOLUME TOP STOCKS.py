import os, sys, time, datetime, threading
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker
from colorama import Fore, Back, Style, init
from tabulate import tabulate

init(autoreset=True)

# --- 1. CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""
MAIN_TOKEN = 256265  # NIFTY 50

# Scanner Settings
SCAN_UNIVERSE = "NFO-OPT" # Can be 'NIFTY50', 'FNO'
SCAN_INTERVAL_MIN = 15    # Re-scan market every 15 mins
TOP_N_STOCKS = 5          # Watch top 5 active stocks

# Risk Settings
MAX_DAILY_LOSS = -5000.0   
MAX_CAPITAL_PCT = 0.60     

# Paths
DATA_DIR = "commander_v6_data"
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# --- 2. DYNAMIC MARKET SCANNER (NEW) ---
class MarketScanner:
    def __init__(self, kite):
        self.kite = kite
        self.active_tokens = [] # List of tokens currently being watched
        self.token_map = {}     # Token -> Symbol Name

    def scan_market(self):
        """Finds Top 5 Stocks by 'Turnover' & 'Volatility'"""
        print(f"\n{Fore.YELLOW}🔭 SCANNING MARKET (Liquidity & Volatility Check)...{Style.RESET_ALL}")
        
        try:
            # 1. Get Universe (e.g., NIFTY 50 stocks)
            # For speed, we hardcode a few liquid stocks, or you can fetch a CSV from NSE
            # Here we simulate fetching the 'F&O' list. In production, utilize kite.instruments('NFO')
            universe_symbols = [
                "NSE:RELIANCE", "NSE:HDFCBANK", "NSE:INFY", "NSE:TCS", "NSE:ICICIBANK",
                "NSE:SBIN", "NSE:TATAMOTORS", "NSE:AXISBANK", "NSE:BAJFINANCE", "NSE:MARUTI",
                "NSE:ADANIENT", "NSE:KOTAKBANK", "NSE:WIPRO", "NSE:LT", "NSE:ITC"
            ]
            
            # 2. Fetch Live Quotes (Snapshot)
            quotes = self.kite.quote(universe_symbols)
            
            scored_stocks = []
            for sym, data in quotes.items():
                tok = data['instrument_token']
                ltp = data['last_price']
                vol = data['volume']
                ohlc = data['ohlc']
                
                # METRIC 1: Turnover (Liquidity) -> Price * Volume
                turnover = ltp * vol
                
                # METRIC 2: Volatility % -> (High - Low) / Prev_Close
                volatility = ((ohlc['high'] - ohlc['low']) / ohlc['close']) * 100
                
                scored_stocks.append({
                    'symbol': sym,
                    'token': int(tok),
                    'ltp': ltp,
                    'turnover': turnover,
                    'volatility': volatility
                })
            
            # 3. Sort & Select (Weightage: 70% Turnover, 30% Volatility)
            # Simple sorting by Turnover for now as it implies activity
            scored_stocks.sort(key=lambda x: x['turnover'], reverse=True)
            
            top_picks = scored_stocks[:TOP_N_STOCKS]
            
            # 4. Update Watchlist
            new_tokens = []
            print(f"{Back.BLUE}{Fore.WHITE} 🎯 TOP MOVERS IDENTIFIED {Back.RESET}")
            headers = ["Symbol", "LTP", "Turnover (Cr)", "Volat %"]
            table = [[s['symbol'], s['ltp'], f"{s['turnover']/10000000:.1f} Cr", f"{s['volatility']:.2f}%"] for s in top_picks]
            print(tabulate(table, headers=headers, tablefmt="simple"))
            
            self.active_tokens = [s['token'] for s in top_picks]
            self.token_map = {s['token']: s['symbol'].split(":")[1] for s in top_picks}
            
            return self.active_tokens, self.token_map

        except Exception as e:
            print(f"{Fore.RED}⚠️ Scan Failed: {e}{Style.RESET_ALL}")
            return [], {}

# --- 3. CENTRAL DATA HUB (Dynamic) ---
class MarketDataHub:
    def __init__(self, kite):
        self.kite = kite
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        self.listeners = {} 
        self.ticks_buffer = {}
        self.subscribed_tokens = set()
        self.running = False

    def register(self, token, listener_method):
        token = int(token)
        if token not in self.listeners:
            self.listeners[token] = []
            self.ticks_buffer[token] = []
        if listener_method not in self.listeners[token]:
            self.listeners[token].append(listener_method)

    def update_subscriptions(self, new_tokens):
        """Updates WebSocket subscription list dynamically"""
        # Convert to set
        new_set = set(new_tokens)
        
        # Calc difference
        to_sub = list(new_set - self.subscribed_tokens)
        to_unsub = list(self.subscribed_tokens - new_set)
        
        if self.kws.is_connected():
            if to_sub: 
                print(f"📡 Subscribing to: {to_sub}")
                self.kws.subscribe(to_sub)
                self.kws.set_mode(self.kws.MODE_LTP, to_sub)
            if to_unsub:
                # Optional: Unsubscribe to save bandwidth
                # self.kws.unsubscribe(to_unsub)
                pass
        
        self.subscribed_tokens.update(new_set)

    def start_stream(self):
        def on_ticks(ws, ticks):
            for t in ticks:
                tok = t['instrument_token']
                ltp = t['last_price']
                if tok in self.ticks_buffer:
                    self.ticks_buffer[tok].append(ltp)

        def on_connect(ws, response):
            print(f"{Fore.GREEN}✅ HUB: WebSocket Connected.{Style.RESET_ALL}")
            if self.subscribed_tokens:
                tokens_list = list(self.subscribed_tokens)
                ws.subscribe(tokens_list)
                ws.set_mode(ws.MODE_LTP, tokens_list)

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.connect(threaded=True)
        self.running = True
        
        last_min = datetime.datetime.now().minute
        while self.running:
            now_min = datetime.datetime.now().minute
            if last_min != now_min:
                last_min = now_min
                for tok, ticks in self.ticks_buffer.items():
                    if ticks:
                        c_open, c_high, c_low, c_close = ticks[0], max(ticks), min(ticks), ticks[-1]
                        df = pd.DataFrame([[c_open, c_high, c_low, c_close]], columns=['Open', 'High', 'Low', 'Close'])
                        
                        if tok in self.listeners:
                            for listener in self.listeners[tok]:
                                threading.Thread(target=listener, args=(df, tok), daemon=True).start()
                        
                        self.ticks_buffer[tok] = [] 
            time.sleep(1)

# --- 4. THE HUNTER (Now uses Dynamic Tokens) ---
class HunterScanner:
    def __init__(self):
        self.history = {} 
        self.symbols = {} 
        self.alerts = []  

    def update_watchlist(self, token_map):
        self.symbols.update(token_map)

    def process_candle(self, df, token):
        if token not in self.history: self.history[token] = pd.DataFrame()
        self.history[token] = pd.concat([self.history[token], df], ignore_index=True)
        
        hist = self.history[token]
        if len(hist) < 15: return 

        calc_df = hist.copy()
        calc_df['RSI'] = ta.rsi(calc_df['Close'], length=14)
        rsi = calc_df.iloc[-1]['RSI']
        price = calc_df.iloc[-1]['Close']
        sym = self.symbols.get(token, str(token))

        if rsi < 30: self.log_alert(sym, price, f"OVERSOLD (RSI {rsi:.1f})", Fore.GREEN)
        elif rsi > 70: self.log_alert(sym, price, f"OVERBOUGHT (RSI {rsi:.1f})", Fore.RED)

    def log_alert(self, sym, price, msg, color):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.alerts.append([ts, sym, price, msg])
        print(f"\n{color}⚡ ALERT: {sym} {msg} @ {price}{Style.RESET_ALL}")

# --- 5. RISK & STRATEGY (SAME AS V5) ---
class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.kill_switch = False
    def update_pnl(self, pnl):
        self.daily_pnl += pnl
        if self.daily_pnl <= MAX_DAILY_LOSS: self.kill_switch = True
    def can_trade(self): return not self.kill_switch

class StrategyWorker:
    def __init__(self, name, kite_obj, risk_manager):
        self.name = name
        self.kite = kite_obj
        self.risk = risk_manager
        self.mode = "PAPER"
        self.is_active = False
        self.paper_balance = 100000.0
        self.log_path = os.path.join(LOGS_DIR, f"{name}_ledger.csv")

    def get_funds(self): return self.paper_balance # Simplified for Scanner Demo
    
    def process_candle(self, df, token):
        # NIFTY LOGIC REMOVED FOR BREVITY - FOCUS IS ON SCANNER
        pass

# --- 6. COMMANDER V6.0 (AUTO-PILOT) ---
class CommanderV6:
    def __init__(self):
        print(f"{Fore.YELLOW}🚀 SYSTEM BOOT... Initializing Auto-Scanner...{Style.RESET_ALL}")
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        
        self.risk = RiskManager()
        self.hub = MarketDataHub(self.kite)
        self.scanner = MarketScanner(self.kite)
        self.hunter = HunterScanner()
        
        # 1. Start Hub
        threading.Thread(target=self.hub.start_stream, daemon=True).start()
        
        # 2. Start Auto-Scan Loop (Background Thread)
        threading.Thread(target=self.auto_scan_loop, daemon=True).start()

    def auto_scan_loop(self):
        while True:
            # A. Run Scan
            tokens, token_map = self.scanner.scan_market()
            
            # B. Update Hunter
            self.hunter.update_watchlist(token_map)
            
            # C. Subscribe Hub to new Tokens
            self.hub.update_subscriptions(tokens)
            
            # D. Register Hunter Logic to these tokens
            for tok in tokens:
                self.hub.register(tok, self.hunter.process_candle)
            
            # E. Sleep for 15 mins
            time.sleep(SCAN_INTERVAL_MIN * 60)

    def menu(self):
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"{Back.BLUE}{Fore.WHITE} 🧠 NEURAL COMMANDER v6.0 (Auto-Pilot) {Style.RESET_ALL}")
            print(f"📡 Active Watchlist: {len(self.hub.subscribed_tokens)} Stocks")
            print(f"⏱️  Next Scan In: {SCAN_INTERVAL_MIN} mins")
            
            if self.hunter.alerts:
                print(f"\n📢 {Fore.CYAN}LATEST ALERTS:{Style.RESET_ALL}")
                for a in self.hunter.alerts[-5:]:
                    print(f"   [{a[0]}] {a[1]}: {a[3]}")
            
            print("\n[Q] Exit")
            if input().upper() == 'Q': sys.exit()

if __name__ == "__main__":
    app = CommanderV6()
    app.menu()