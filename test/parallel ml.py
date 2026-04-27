import os, sys, time, datetime, threading
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker, exceptions
from colorama import Fore, Back, Style, init
from tabulate import tabulate
import os
print(f"📂 YOUR DATA IS SAVED HERE: {os.path.abspath('commander_v7_data')}")

init(autoreset=True)

# --- 1. CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""
MAIN_SYMBOL = "NSE:NIFTY 50"
MAIN_TOKEN = 256265  # NIFTY 50

# Stocks to Scan
SCAN_LIST = ["NSE:RELIANCE", "NSE:INFY", "NSE:HDFCBANK", "NSE:SBIN", "NSE:TCS"]
LOT_SIZE = 50   

# Risk Settings
MAX_DAILY_LOSS = -5000.0   
MAX_CAPITAL_PCT = 0.60     

# Paths
DATA_DIR = "commander_v5_data"
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# --- 2. RISK MANAGER ---
class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.kill_switch = False

    def update_pnl(self, pnl):
        self.daily_pnl += pnl
        if self.daily_pnl <= MAX_DAILY_LOSS:
            self.kill_switch = True
            print(f"\n{Back.RED}{Fore.WHITE} 🛑 DAILY LOSS LIMIT HIT! SYSTEM LOCKED. {Style.RESET_ALL}")

    def can_trade(self):
        return not self.kill_switch

# --- 3. CENTRAL DATA HUB (FIXED) ---
class MarketDataHub:
    def __init__(self, kite):
        self.kite = kite
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        self.listeners = {} 
        self.ticks_buffer = {} 
        self.running = False

    def register(self, token, listener_method):
        # FIX: Ensure token is standard Python int
        token = int(token)
        if token not in self.listeners:
            self.listeners[token] = []
            self.ticks_buffer[token] = []
        self.listeners[token].append(listener_method)

    def start_stream(self, tokens):
        # FIX: Force convert all tokens to int to prevent 1006 Error
        clean_tokens = [int(t) for t in tokens]
        
        def on_ticks(ws, ticks):
            for t in ticks:
                tok = t['instrument_token']
                ltp = t['last_price']
                if tok in self.ticks_buffer:
                    self.ticks_buffer[tok].append(ltp)

        def on_connect(ws, response):
            print(f"{Fore.GREEN}✅ HUB: Connected! Subscribing to {len(clean_tokens)} instruments...{Style.RESET_ALL}")
            ws.subscribe(clean_tokens)
            ws.set_mode(ws.MODE_LTP, clean_tokens)

        def on_error(ws, code, reason):
            print(f"{Fore.RED}❌ WS ERROR {code}: {reason}{Style.RESET_ALL}")

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.on_error = on_error
        self.kws.connect(threaded=True)
        self.running = True
        
        # Candle Builder Loop
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

# --- 4. THE HUNTER (SCANNER ENGINE) ---
class HunterScanner:
    def __init__(self):
        self.history = {} 
        self.symbols = {} 
        self.alerts = []  

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

        if rsi < 30:
            self.log_alert(sym, price, f"OVERSOLD (RSI {rsi:.1f})", Fore.GREEN)
        elif rsi > 70:
            self.log_alert(sym, price, f"OVERBOUGHT (RSI {rsi:.1f})", Fore.RED)

    def log_alert(self, sym, price, msg, color):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.alerts.append([ts, sym, price, msg])
        print(f"\n{color}⚡ HUNTER ALERT: {sym} {msg} @ {price}{Style.RESET_ALL}")

# --- 5. STRATEGY WORKER ---
class StrategyWorker:
    def __init__(self, name, kite_obj, risk_manager):
        self.name = name
        self.kite = kite_obj
        self.risk = risk_manager
        self.mode = "PAPER"
        self.is_active = False
        self.paper_balance = 100000.0
        self.history = pd.DataFrame()
        self.log_path = os.path.join(LOGS_DIR, f"{name}_ledger.csv")

    def get_funds(self):
        if self.mode == "REAL":
            try:
                m = self.kite.margins()
                eq = m['equity']['available']
                return eq.get('live_balance', 0) if eq.get('cash', 0) == 0 else eq['cash']
            except: return 0.0
        return self.paper_balance

    def calculate_qty(self, price):
        funds = self.get_funds() * MAX_CAPITAL_PCT
        if price == 0: return 0
        raw = int(funds / price)
        return (raw // LOT_SIZE) * LOT_SIZE

    def process_candle(self, df, token):
        if not self.is_active or not self.risk.can_trade(): return
        self.history = pd.concat([self.history, df], ignore_index=True)
        if len(self.history) < 20: return
        price = df.iloc[-1]['Close']

        import random
        dice = random.randint(1, 100)
        signal = None
        if self.name == "FIB_BOT" and dice > 95: signal = "BUY"
        elif self.name == "SCALP_BOT" and dice > 90: signal = "SELL"

        if signal: self.execute_trade(signal, price)

    def execute_trade(self, signal, price):
        qty = self.calculate_qty(price)
        if qty < LOT_SIZE: return

        import random
        pnl = random.randint(-500, 1000) 
        
        if self.mode == "PAPER": self.paper_balance += pnl
        self.risk.update_pnl(pnl)

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_trade(timestamp, self.mode, signal, qty, price, pnl)
        
        color = Fore.GREEN if pnl > 0 else Fore.RED
        print(f"\n{color}⚡ [{self.mode}] {self.name}: {signal} {qty} @ {price} | PnL: ₹{pnl}{Style.RESET_ALL}")

    def log_trade(self, time, mode, side, qty, price, pnl):
        rec = pd.DataFrame([[time, mode, side, qty, price, pnl]], 
                           columns=['Time', 'Mode', 'Side', 'Qty', 'Price', 'PnL'])
        hdr = not os.path.exists(self.log_path)
        rec.to_csv(self.log_path, mode='a', header=hdr, index=False)

    def view_ledger(self):
        if not os.path.exists(self.log_path):
            print(f"{Fore.RED}No logs found.{Style.RESET_ALL}")
            return
        df = pd.read_csv(self.log_path)
        print(tabulate(df.tail(5), headers='keys', tablefmt='fancy_grid'))

# --- 6. COMMANDER V5.0 ---
class CommanderV5:
    def __init__(self):
        print(f"{Fore.YELLOW}🚀 SYSTEM BOOT... Fetching Live Data...{Style.RESET_ALL}")
        
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        
        self.real_margin = self.fetch_real_margin()
        self.risk = RiskManager()
        self.hub = MarketDataHub(self.kite)
        self.hunter = HunterScanner()
        
        self.strategies = {
            "1": StrategyWorker("FIB_BOT", self.kite, self.risk),
            "2": StrategyWorker("SCALP_BOT", self.kite, self.risk)
        }

        # SETUP LISTENERS (NIFTY)
        self.hub.register(MAIN_TOKEN, self.strategies["1"].process_candle)
        self.hub.register(MAIN_TOKEN, self.strategies["2"].process_candle)
        
        # SETUP LISTENERS (SCANNER STOCKS)
        self.scanner_tokens = []
        try:
            print("⏳ Resolving Scanner Tokens...")
            all_inst = pd.DataFrame(self.kite.instruments("NSE"))
            for sym in SCAN_LIST:
                clean_sym = sym.split(":")[1]
                row = all_inst[all_inst['tradingsymbol'] == clean_sym]
                if not row.empty:
                    # FIX IS HERE: FORCE CONVERT TO INT
                    tok = int(row.iloc[0]['instrument_token'])
                    self.scanner_tokens.append(tok)
                    self.hunter.symbols[tok] = clean_sym
                    self.hub.register(tok, self.hunter.process_candle)
                    print(f"   found {clean_sym} -> {tok}")
        except Exception as e:
            print(f"⚠️ Scanner Setup Error: {e}")

        # START STREAM
        all_tokens = [int(MAIN_TOKEN)] + self.scanner_tokens
        threading.Thread(target=self.hub.start_stream, args=(all_tokens,), daemon=True).start()

    def fetch_real_margin(self):
        try:
            m = self.kite.margins()
            eq = m['equity']['available']
            val = eq.get('live_balance', 0)
            if val == 0: val = eq.get('cash', 0)
            return val
        except: return "ERROR"

    def refresh_screen(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"{Back.BLUE}{Fore.WHITE} 🧠 NEURAL COMMANDER v5.0 (Stable) {Style.RESET_ALL}")
        
        rm_val = f"₹ {self.real_margin:,.2f}" if isinstance(self.real_margin, float) else "ERROR"
        print(f"🏦 Real Margin: {Fore.GREEN}{rm_val}{Style.RESET_ALL}")
        print(f"🎯 Scanner:    Watching {len(self.scanner_tokens)} Stocks")
        print("-" * 60)

        table_data = []
        for pid, s in self.strategies.items():
            status = f"{Back.GREEN} ON {Back.RESET}" if s.is_active else "OFF"
            mode = f"{Fore.RED}REAL{Style.RESET_ALL}" if s.mode == "REAL" else "PAPER"
            table_data.append([pid, s.name, mode, status, f"₹ {s.get_funds():,.0f}"])
        print(tabulate(table_data, headers=["ID", "Strategy", "Mode", "Status", "Funds"], tablefmt="fancy_grid"))
        
        if self.hunter.alerts:
            print(f"\n📢 {Fore.CYAN}RECENT ALERTS:{Style.RESET_ALL}")
            for a in self.hunter.alerts[-3:]:
                print(f"   [{a[0]}] {a[1]}: {a[3]} @ {a[2]}")

    def menu(self):
        while True:
            self.refresh_screen()
            print("\n[1] Config FIB_BOT    [2] Config SCALP_BOT")
            print("[L] View Logs         [S] Scanner Details")
            print("[Q] Exit")
            ch = input("\nCommand > ").upper()
            
            if ch == 'Q': sys.exit()
            elif ch == 'L': 
                if input("View FIB(1) or SCALP(2)? ") == '1': self.strategies['1'].view_ledger()
                else: self.strategies['2'].view_ledger()
                input("Press Enter...")
            elif ch == 'S':
                print(f"\n🔭 Watching: {list(self.hunter.symbols.values())}")
                input("Press Enter...")
            elif ch in self.strategies: self.config_strategy(self.strategies[ch])

    def config_strategy(self, s):
        while True:
            self.refresh_screen()
            print(f"\n⚙️  CONFIG: {s.name}")
            print("[1] Set PAPER Mode  [2] Set REAL Mode")
            print("[3] Toggle ON/OFF   [B] Back")
            sel = input("Selection: ").upper()
            if sel == '1': s.mode = "PAPER"
            elif sel == '2': 
                if input("Type 'LIVE': ") == "LIVE": s.mode = "REAL"
            elif sel == '3': s.is_active = not s.is_active
            elif sel == 'B': break

if __name__ == "__main__":
    app = CommanderV5()
    app.menu()