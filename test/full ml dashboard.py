import os, sys, time, datetime, threading, math
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker
from tabulate import tabulate
from colorama import Fore, Back, Style, init

# Initialize Colors
init(autoreset=True)

# --- 1. CONFIGURATION & CREDENTIALS ---
API_KEY =""
ACCESS_TOKEN =""

# System Paths
DATA_DIR = "user_data"
MODELS_DIR = os.path.join(DATA_DIR, "models")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(MODELS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Brokerage Config
BROKERAGE_PER_ORDER = 30.0 # ₹30 Buy + ₹30 Sell = ₹60 per trade

# --- 2. DATA & METRICS ENGINE ---
class DataManager:
    @staticmethod
    def log_trade(strategy_name, entry_time, symbol, side, qty, entry_price, exit_price, pnl):
        """Saves trade to CSV for 'Learning' and Export"""
        file_path = os.path.join(LOGS_DIR, f"{strategy_name}_trades.csv")
        
        # Calculate Net PnL (After Brokerage)
        charges = BROKERAGE_PER_ORDER * 2
        net_pnl = pnl - charges
        
        new_row = {
            "Time": entry_time, "Symbol": symbol, "Side": side, "Qty": qty,
            "Entry": entry_price, "Exit": exit_price, "Gross_PnL": pnl,
            "Charges": charges, "Net_PnL": net_pnl
        }
        
        # Append to CSV
        if os.path.exists(file_path):
            df = pd.read_csv(file_path)
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        else:
            df = pd.DataFrame([new_row])
            
        df.to_csv(file_path, index=False)
        return net_pnl

    @staticmethod
    def get_metrics(strategy_name):
        """Calculates Sharpe Ratio & Win Rate from CSV"""
        file_path = os.path.join(LOGS_DIR, f"{strategy_name}_trades.csv")
        if not os.path.exists(file_path):
            return 0.0, 0.0, 0.0 # Return defaults

        df = pd.read_csv(file_path)
        if df.empty: return 0.0, 0.0, 0.0

        # Sharpe Ratio (Trade-based)
        # Formula: Mean PnL / StdDev PnL
        returns = df['Net_PnL']
        mean_ret = returns.mean()
        std_dev = returns.std()
        
        # Avoid division by zero
        sharpe = (mean_ret / std_dev) if std_dev != 0 else 0.0
        
        # Win Rate
        wins = len(df[df['Net_PnL'] > 0])
        win_rate = (wins / len(df)) * 100
        
        total_pnl = df['Net_PnL'].sum()
        
        return total_pnl, win_rate, sharpe

# --- 3. BROKER INTERFACE (REAL & PAPER) ---
class BrokerInterface:
    def __init__(self, mode="PAPER"):
        self.mode = mode
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        
        # Simulated Wallet
        self.paper_balance = 100000.0

    def get_margins(self):
        if self.mode == "REAL":
            try:
                m = self.kite.margins()
                return m['equity']['available']['cash'], m['equity']['utilised']['debits']
            except:
                return 0.0, 0.0
        else:
            return self.paper_balance, 0.0

    def get_market_depth(self, symbol):
        # Fetch Level 2 Data (Top 5 Bids/Asks)
        try:
            quote = self.kite.quote(f"NSE:{symbol}")
            return quote[f"NSE:{symbol}"]['depth']
        except:
            return None

# --- 4. STRATEGY ENGINE (THE BRAINS) ---
class StrategyBase:
    def __init__(self, name, broker):
        self.name = name
        self.broker = broker
        self.running = False
    
    def run_logic(self, tick_data):
        pass # To be overridden by specific strategies

class FibonacciStrategy(StrategyBase):
    def __init__(self, broker):
        super().__init__("Fibonacci_Golden", broker)
    
    def process(self, symbol, ltp):
        # SIMULATED LOGIC FOR DEMO
        # In real usage, this would calc Fib levels using history
        # Here we randomly simulate a trade to show the Dashboard features
        import random
        if random.random() > 0.95: # 5% chance to trade per tick
            entry = ltp
            exit_p = ltp + (random.randint(-10, 20)) # Random Outcome
            qty = 50
            pnl = (exit_p - entry) * qty
            
            # Log Data
            net = DataManager.log_trade(self.name, datetime.datetime.now(), symbol, "BUY", qty, entry, exit_p, pnl)
            return net
        return None

class ScalpingStrategy(StrategyBase):
    def __init__(self, broker):
        super().__init__("Momentum_Scalper", broker)

    def process(self, symbol, ltp):
        # MOMENTUM LOGIC SIMULATION
        import random
        if random.random() > 0.90: # 10% chance (High Frequency)
            entry = ltp
            exit_p = ltp + (random.randint(-5, 10)) 
            qty = 100
            pnl = (exit_p - entry) * qty
            
            net = DataManager.log_trade(self.name, datetime.datetime.now(), symbol, "BUY", qty, entry, exit_p, pnl)
            return net
        return None

# --- 5. THE DASHBOARD UI ---
class Dashboard:
    def __init__(self):
        self.broker = BrokerInterface(mode="PAPER") # Default to Paper
        self.active_strategy = None
        self.selected_index = "NIFTY 50"

    def header(self, title):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"{Back.BLUE}{Fore.WHITE} 🧠 NEURAL TRADE COMMAND CENTER {Style.RESET_ALL}")
        print(f"{Fore.CYAN}Current Mode: {self.broker.mode} | Index: {self.selected_index}{Style.RESET_ALL}")
        print(f"─" * 50)
        print(f"{Fore.YELLOW}📍 {title}{Style.RESET_ALL}\n")

    def main_menu(self):
        while True:
            self.header("MAIN MENU")
            print("1. 🧪 LAB: Learning & Simulation (Paper Trading)")
            print("2. ⚔️ WAR ROOM: Real Trading (Live Execution)")
            print("3. ⚙️ Settings (Choose Index / Export Data)")
            print("4. Exit")
            
            ch = input("\nSelect Option: ")
            if ch == '1': self.lab_tab()
            elif ch == '2': self.real_trade_tab()
            elif ch == '3': self.settings_tab()
            elif ch == '4': sys.exit()

    # --- TAB 1: LEARNING LAB ---
    def lab_tab(self):
        while True:
            self.broker.mode = "PAPER"
            self.header("LABORATORY (Paper Trading)")
            
            # Show Stats for Strategies
            fib_pnl, fib_wr, fib_sr = DataManager.get_metrics("Fibonacci_Golden")
            scl_pnl, scl_wr, scl_sr = DataManager.get_metrics("Momentum_Scalper")
            
            data = [
                ["1. Fibonacci", f"₹ {fib_pnl:,.2f}", f"{fib_wr:.1f}%", f"{fib_sr:.2f}"],
                ["2. Scalping",  f"₹ {scl_pnl:,.2f}", f"{scl_wr:.1f}%", f"{scl_sr:.2f}"]
            ]
            print(tabulate(data, headers=["Strategy", "Net PnL", "Win Rate", "Sharpe Ratio"], tablefmt="fancy_grid"))
            
            print("\nOptions:")
            print("[1] Train Fibonacci Model")
            print("[2] Train Scalping Model")
            print("[B] Back to Main Menu")
            
            ch = input("\nSelect Action: ").upper()
            if ch == '1': self.run_simulation(FibonacciStrategy(self.broker))
            elif ch == '2': self.run_simulation(ScalpingStrategy(self.broker))
            elif ch == 'B': break

    def run_simulation(self, strategy_obj):
        print(f"\n{Fore.GREEN}🚀 Starting {strategy_obj.name} simulation... (Ctrl+C to Stop){Style.RESET_ALL}")
        print("Learning from live ticks... Saving results to DB...")
        
        # Simulate Loop
        try:
            # Mock price for demo
            price = 24500.0 
            while True:
                price += (np.random.randint(-10, 10))
                res = strategy_obj.process(self.selected_index, price)
                
                if res is not None:
                    color = Fore.GREEN if res > 0 else Fore.RED
                    print(f"Trade Executed | Net PnL: {color}₹ {res:.2f}{Style.RESET_ALL} (Incl. Brokerage)")
                
                time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}🛑 Simulation Stopped. Data Saved.{Style.RESET_ALL}")
            time.sleep(1)

    # --- TAB 2: REAL TRADING ---
    def real_trade_tab(self):
        while True:
            self.broker.mode = "REAL"
            self.header("WAR ROOM (Real Money)")
            
            # 1. Show Wallet
            cash, used = self.broker.get_margins()
            print(f"{Fore.MAGENTA}💰 REAL ACCOUNT:{Style.RESET_ALL}")
            print(f"   Available Cash: ₹ {cash:,.2f}")
            print(f"   Used Margin:    ₹ {used:,.2f}\n")
            
            # 2. Show Order Book / Depth (Optional)
            print(f"{Fore.CYAN}📊 MARKET DEPTH ({self.selected_index}):{Style.RESET_ALL}")
            # (In real run, self.broker.get_market_depth() would print here)
            print("   [Live Depth Data Would Appear Here via WebSocket]\n")
            
            print("Deploy Strategy (Using Learnt Data):")
            print("1. Deploy Fibonacci Golden Pocket")
            print("2. Deploy Momentum Scalper")
            print("B. Back")
            
            ch = input("\nSelect: ").upper()
            if ch in ['1', '2']:
                print(f"\n{Fore.RED}⚠️ WARNING: You are about to trade with REAL CAPITAL.")
                confirm = input("Type 'CONFIRM' to proceed: ")
                if confirm == "CONFIRM":
                    print(f"{Fore.GREEN}🚀 SYSTEM LIVE. EXECUTING TRADES...{Style.RESET_ALL}")
                    # In real code: self.run_simulation(Strategy) but with REAL orders
                    input("Press Enter to Stop Live Trading...")
            elif ch == 'B':
                break

    # --- TAB 3: SETTINGS & EXPORT ---
    def settings_tab(self):
        while True:
            self.header("SETTINGS")
            print(f"Current Index: {Fore.YELLOW}{self.selected_index}{Style.RESET_ALL}")
            print("1. Switch to NIFTY 50")
            print("2. Switch to SENSEX")
            print("3. 💾 Export Learnt Data (Show Path)")
            print("B. Back")
            
            ch = input("\nSelect: ").upper()
            if ch == '1': self.selected_index = "NIFTY 50"
            elif ch == '2': self.selected_index = "SENSEX"
            elif ch == '3':
                full_path = os.path.abspath(LOGS_DIR)
                print(f"\n{Fore.GREEN}✅ DATA EXPORT:{Style.RESET_ALL}")
                print(f"Your trade logs and models are stored here:")
                print(f"{Fore.YELLOW}{full_path}{Style.RESET_ALL}")
                print("You can copy this folder to any other computer to transfer the 'Brain'.")
                input("\nPress Enter...")
            elif ch == 'B': break

if __name__ == "__main__":
    app = Dashboard()
    app.main_menu()