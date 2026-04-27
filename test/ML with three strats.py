import os, sys, time, datetime, threading, json
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker, exceptions
from colorama import Fore, Back, Style, init
from tabulate import tabulate
from stable_baselines3 import PPO
from gymnasium import spaces
import gymnasium as gym

init(autoreset=True)

# --- 1. CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""

# Default Index
CURRENT_INDEX = "NIFTY" # Can be 'NIFTY' or 'SENSEX'

# Risk & ML Settings
MAX_DAILY_LOSS = -5000.0   
MAX_CAPITAL_PCT = 0.60
HINDSIGHT_WINDOW = 15

# Paths
DATA_DIR = "commander_v13_data"
LOGS_DIR = os.path.join(DATA_DIR, "logs")
MODELS_DIR = os.path.join(DATA_DIR, "models")
WALLET_FILE = os.path.join(DATA_DIR, "wallet.json")
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

print(f"📂 DATA SAVED AT: {os.path.abspath(DATA_DIR)}")

# --- 2. INDEX MANAGER ---
class IndexManager:
    @staticmethod
    def get_details(name):
        if name == "NIFTY":
            return {
                "Symbol": "NSE:NIFTY 50", "Token": 256265, "Step": 50,
                "Lot": 65, "Segment": "NFO-OPT", "Exchange": "NFO"
            }
        elif name == "SENSEX":
            return {
                "Symbol": "BSE:SENSEX", "Token": 265, "Step": 100,
                "Lot": 20, "Segment": "BFO-OPT", "Exchange": "BFO"
            }
        return None

# --- 3. OPTION CHAIN MANAGER (UPDATED FOR RESOLUTION) ---
class OptionChainManager:
    def __init__(self, kite):
        self.kite = kite
        self.instruments_nfo = []
        self.instruments_bfo = []
        self.token_map = {} # {token: {'symbol': '...', 'exchange': '...'}}
        self.atm_token_ref = None # Stores the current ATM Call Token for Spot strategies
        self.is_loaded = {"NFO": False, "BFO": False}

    def load_instruments(self, exchange):
        if exchange == "NFO" and not self.is_loaded["NFO"]:
            print("⏳ Downloading NFO Instruments...")
            self.instruments_nfo = self.kite.instruments("NFO")
            self.is_loaded["NFO"] = True
        elif exchange == "BFO" and not self.is_loaded["BFO"]:
            print("⏳ Downloading BFO Instruments...")
            self.instruments_bfo = self.kite.instruments("BFO")
            self.is_loaded["BFO"] = True

    def get_dynamic_tokens(self, index_name, spot_price):
        details = IndexManager.get_details(index_name)
        step = details["Step"]
        segment = details["Segment"]
        exchange = details["Exchange"]
        
        self.load_instruments(exchange)
        source_list = self.instruments_nfo if exchange == "NFO" else self.instruments_bfo
        
        atm_strike = round(spot_price / step) * step
        
        # Filter Options
        opts = [i for i in source_list if i['name'] == index_name and i['segment'] == segment]
        if not opts: return {}

        df = pd.DataFrame(opts)
        df['expiry'] = pd.to_datetime(df['expiry'])
        nearest_expiry = df['expiry'].min()
        
        # Get ATM, OTM, ITM
        strikes = [atm_strike, atm_strike + step, atm_strike - step]
        
        final_opts = df[
            (df['expiry'] == nearest_expiry) & 
            (df['strike'].isin(strikes))
        ]
        
        new_map = {}
        for _, row in final_opts.iterrows():
            tok = int(row['instrument_token'])
            new_map[tok] = {
                'symbol': row['tradingsymbol'],
                'exchange': row['exchange'],
                'strike': row['strike'],
                'type': row['instrument_type']
            }
            # Save ATM CE for Spot Strategies
            if row['strike'] == atm_strike and row['instrument_type'] == 'CE':
                self.atm_token_ref = tok

        self.token_map.update(new_map) # Merge into master map
        return new_map

# --- 4. DATA HUB ---
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

    def subscribe_dynamic(self, new_tokens):
        clean = [int(t) for t in new_tokens]
        to_add = [t for t in clean if t not in self.subscribed_tokens]
        if to_add and self.running:
            self.kws.subscribe(to_add)
            self.kws.set_mode(self.kws.MODE_LTP, to_add)
            self.subscribed_tokens.update(to_add)

    def start_stream(self, initial_tokens):
        clean_tokens = [int(t) for t in initial_tokens]
        self.subscribed_tokens.update(clean_tokens)
        
        def on_ticks(ws, ticks):
            for t in ticks:
                tok = t['instrument_token']
                ltp = t['last_price']
                if tok in self.ticks_buffer: self.ticks_buffer[tok].append(ltp)

        def on_connect(ws, response):
            print(f"{Fore.GREEN}✅ HUB: Connected.{Style.RESET_ALL}")
            ws.subscribe(list(self.subscribed_tokens))
            ws.set_mode(ws.MODE_LTP, list(self.subscribed_tokens))

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

# --- 5. PERSISTENCE & RISK ---
class WalletManager:
    @staticmethod
    def load_balance(strategy_name):
        if not os.path.exists(WALLET_FILE): return 100000.0
        try:
            with open(WALLET_FILE, 'r') as f: return json.load(f).get(strategy_name, 100000.0)
        except: return 100000.0

    @staticmethod
    def save_balance(strategy_name, new_balance):
        data = {}
        if os.path.exists(WALLET_FILE):
            try:
                with open(WALLET_FILE, 'r') as f: data = json.load(f)
            except: pass
        data[strategy_name] = new_balance
        with open(WALLET_FILE, 'w') as f: json.dump(data, f, indent=4)

class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.kill_switch = False
    def update_pnl(self, pnl):
        self.daily_pnl += pnl
        if self.daily_pnl <= MAX_DAILY_LOSS: self.kill_switch = True
    def can_trade(self): return not self.kill_switch

# --- 6. STRATEGY WORKER (LIVE & RESOLVED) ---
class BaseStrategyWorker:
    def __init__(self, name, kite_obj, risk_manager, opt_mgr):
        self.name = name
        self.kite = kite_obj
        self.risk = risk_manager
        self.opt_mgr = opt_mgr
        self.mode = "PAPER"
        self.is_active = False
        self.paper_balance = WalletManager.load_balance(name)
        self.history = pd.DataFrame()
        self.log_path = os.path.join(LOGS_DIR, f"{name}_ledger.csv")
        self.active_trade = None
        self.dynamic_sl = 0.005 

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
        details = IndexManager.get_details(CURRENT_INDEX)
        lot = details["Lot"]
        return (raw // lot) * lot

    def process_candle(self, df, token):
        if not self.is_active or not self.risk.can_trade(): return
        self.history = pd.concat([self.history, df], ignore_index=True)
        if len(self.history) < 20: return
        
        price = df.iloc[-1]['Close']
        
        # DEMO LOGIC (Replace with your ML predict)
        if self.active_trade is None:
            # Random Entry for visual demo
            if np.random.randint(0, 100) > 98: 
                self.entry_trade(price, "BUY", token)
        else:
            if price < self.active_trade['SL']:
                pnl = (price - self.active_trade['Entry']) * self.active_trade['Qty']
                self.close_trade(price, pnl)

    def entry_trade(self, price, side, token):
        # 1. RESOLVE SYMBOL
        target_info = None
        
        # A. If we are analyzing an Option Token directly (Expiry Bot)
        if token in self.opt_mgr.token_map:
            target_info = self.opt_mgr.token_map[token]
        
        # B. If we are analyzing SPOT (Fib/Scalp Bot) -> Trade ATM Call
        elif self.opt_mgr.atm_token_ref:
            target_info = self.opt_mgr.token_map.get(self.opt_mgr.atm_token_ref)
            
        if not target_info:
            print(f"{Fore.RED}⚠️ Could not resolve trading symbol for token {token}!{Style.RESET_ALL}")
            return

        trading_symbol = target_info['symbol']
        exchange = target_info['exchange']
        
        # 2. CALCULATE QTY
        qty = self.calculate_qty(price)
        details = IndexManager.get_details(CURRENT_INDEX)
        if qty < details["Lot"]: 
            if self.mode == "REAL": print(f"{Fore.RED}Insufficient Funds{Style.RESET_ALL}")
            return
        
        print(f"\n{Fore.GREEN}⚡ [{self.mode}] {self.name}: BUY {qty} {trading_symbol} @ {price}{Style.RESET_ALL}")
        
        order_id = "PAPER_ORD"
        
        # 3. LIVE EXECUTION (UNCOMMENTED)
        if self.mode == "REAL":
            try:
                order_id = self.kite.place_order(
                    tradingsymbol=trading_symbol,
                    exchange=exchange,
                    transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                    quantity=qty,
                    order_type=self.kite.ORDER_TYPE_MARKET,
                    product=self.kite.PRODUCT_MIS
                )
                print(f"{Fore.MAGENTA}⚔️ LIVE ORDER PLACED! ID: {order_id}{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}❌ ORDER FAILED: {e}{Style.RESET_ALL}")
                return # Abort if failed

        self.active_trade = {
            "Entry": price, "Qty": qty, 
            "SL": price * (1 - self.dynamic_sl),
            "Symbol": trading_symbol,
            "Exchange": exchange
        }

    def close_trade(self, price, pnl):
        if self.mode == "PAPER": 
            self.paper_balance += pnl
            WalletManager.save_balance(self.name, self.paper_balance)
        self.risk.update_pnl(pnl)
        
        # LIVE EXIT
        if self.mode == "REAL" and self.active_trade:
            try:
                self.kite.place_order(
                    tradingsymbol=self.active_trade['Symbol'],
                    exchange=self.active_trade['Exchange'],
                    transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                    quantity=self.active_trade['Qty'],
                    order_type=self.kite.ORDER_TYPE_MARKET,
                    product=self.kite.PRODUCT_MIS
                )
                print(f"{Fore.MAGENTA}⚔️ LIVE EXIT PLACED!{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.RED}❌ EXIT FAILED: {e}{Style.RESET_ALL}")

        print(f"{Fore.RED if pnl<0 else Fore.GREEN}❌ [{self.mode}] {self.name}: SELL @ {price} | PnL: {pnl:.2f}{Style.RESET_ALL}")
        self.active_trade = None

# --- 7. COMMANDER V13 (MAIN) ---
class CommanderV13:
    def __init__(self):
        print(f"{Fore.YELLOW}🚀 SYSTEM BOOT... Fetching Live Data...{Style.RESET_ALL}")
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        
        self.risk = RiskManager()
        self.hub = MarketDataHub(self.kite)
        self.opt_mgr = OptionChainManager(self.kite)
        
        # Pass opt_mgr to workers
        self.strategies = {
            "1": BaseStrategyWorker("FIB_BOT", self.kite, self.risk, self.opt_mgr),
            "2": BaseStrategyWorker("SCALP_BOT", self.kite, self.risk, self.opt_mgr),
            "3": BaseStrategyWorker("EXPIRY_BOT", self.kite, self.risk, self.opt_mgr)
        }
        
        self.refresh_index_subscription(CURRENT_INDEX)
        
        # Start background stream
        threading.Thread(target=self.hub.start_stream, args=([256265],), daemon=True).start()

    def refresh_index_subscription(self, index_name):
        global CURRENT_INDEX
        CURRENT_INDEX = index_name
        details = IndexManager.get_details(index_name)
        
        print(f"🔄 Switching to {index_name} (Symbol: {details['Symbol']} | Step: {details['Step']})")
        
        try:
            quote = self.kite.quote(details["Symbol"])
            spot_token = quote[details["Symbol"]]['instrument_token']
            spot_price = quote[details["Symbol"]]['last_price']
            
            # Register Spot to Fib/Scalp Bots
            self.hub.subscribe_dynamic([spot_token])
            self.hub.register(spot_token, self.strategies["1"].process_candle)
            self.hub.register(spot_token, self.strategies["2"].process_candle)
            
            # Get Options for Expiry Bot
            opts = self.opt_mgr.get_dynamic_tokens(index_name, spot_price)
            if opts:
                print(f"🔥 Monitoring {index_name} Options: {len(opts)} Contracts")
                self.hub.subscribe_dynamic(list(opts.keys()))
                for tok in opts:
                    self.hub.register(tok, self.strategies["3"].process_candle)
                    
        except Exception as e:
            print(f"❌ Index Switch Error: {e}")

    def menu(self):
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            
            idx_color = Fore.CYAN if CURRENT_INDEX == "NIFTY" else Fore.MAGENTA
            print(f"{Back.BLUE}{Fore.WHITE} 🧠 COMMANDER v13 (PRODUCTION READY) {Style.RESET_ALL}")
            print(f"📊 Index: {idx_color}{CURRENT_INDEX}{Style.RESET_ALL} | 📦 Lot Size: {IndexManager.get_details(CURRENT_INDEX)['Lot']}")
            
            try:
                m = self.kite.margins()['equity']['available']
                rm_val = m.get('live_balance', 0) or m.get('cash', 0)
                rm_str = f"₹ {rm_val:,.2f}"
            except: rm_str = "OFFLINE"
            print(f"🏦 Real Margin: {Fore.GREEN}{rm_str}{Style.RESET_ALL}")
            print("-" * 65)
            
            table_data = []
            for pid, s in self.strategies.items():
                status = f"{Back.GREEN} ON {Back.RESET}" if s.is_active else "OFF"
                mode_col = f"{Fore.RED}REAL{Style.RESET_ALL}" if s.mode == "REAL" else "PAPER"
                fund_disp = f"Paper: ₹{s.paper_balance:,.0f}" if s.mode == "PAPER" else "REAL ACC"
                table_data.append([pid, s.name, mode_col, status, fund_disp])
            
            print(tabulate(table_data, headers=["ID", "Strategy", "Mode", "Status", "Funds"], tablefmt="fancy_grid"))
            
            print("\n[1-3] Toggle ON/OFF   [M] Mode (Real/Paper)")
            print("[S] Switch Index      [Q] Exit")
            
            ch = input("\nCommand > ").upper()
            
            if ch == 'Q': sys.exit()
            elif ch == 'S': 
                new_idx = "SENSEX" if CURRENT_INDEX == "NIFTY" else "NIFTY"
                self.refresh_index_subscription(new_idx)
                time.sleep(2)
            elif ch == 'M':
                bot_id = input("Bot ID: ")
                if bot_id in self.strategies:
                    val = input("Type 'REAL' or 'PAPER': ").upper()
                    if val == "REAL":
                        if input("Confirm LIVE? (YES): ") == "YES": self.strategies[bot_id].mode = "REAL"
                    else: self.strategies[bot_id].mode = "PAPER"
            elif ch in self.strategies:
                self.strategies[ch].is_active = not self.strategies[ch].is_active

if __name__ == "__main__":
    app = CommanderV13()
    app.menu()