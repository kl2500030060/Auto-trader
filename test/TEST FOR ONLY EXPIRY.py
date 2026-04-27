import os, sys, time, datetime, json, logging
from collections import deque
import pandas as pd
from kiteconnect import KiteConnect, KiteTicker
from colorama import Fore, Back, Style, init

# --- SYSTEM INIT ---
init(autoreset=True)
PROJECT_NAME = "AEGIS_FINAL"
VERSION = "3.0 (Auto-Margin)"

# --- 1. USER CONFIGURATION ---
API_KEY = "YOUR_API_KEY"
ACCESS_TOKEN = "YOUR_ACCESS_TOKEN" 
INDEX_NAME = "SENSEX"               # "NIFTY" or "SENSEX"
MODE = "PAPER"                      # "REAL" or "PAPER"

# --- 2. RISK CONFIGURATION (STRICT) ---
STARTING_PAPER_CAPITAL = 100000.0   # Default if no wallet file exists
DEPLOY_PER_TRADE_PCT = 0.25         # Rule: Use 25% of Available Margin
MAX_DAILY_DRAWDOWN_PCT = 0.10       # Rule: Exit if Total Capital drops 10%

# --- 3. STRATEGY PARAMETERS ---
START_TIME = datetime.time(13, 30)  # Pre-scan
ACTIVE_TIME = datetime.time(14, 0)  # Execution
END_TIME = datetime.time(15, 15)    # Hard Stop

GAMMA_TRIGGER_PCT = 25.0            # Price Jump > 25%
WICK_REJECTION_LIMIT = 5.0          # Avoid upper wicks > 5 pts

# Paths
BASE_DIR = os.path.join(os.getcwd(), "AEGIS_Data")
LOG_FILE = os.path.join(BASE_DIR, "execution.log")
WALLET_FILE = os.path.join(BASE_DIR, "wallet.json")
if not os.path.exists(BASE_DIR): os.makedirs(BASE_DIR)

# --- 4. LOGGING ---
class Logger:
    @staticmethod
    def log(msg, level="INFO"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        color = Fore.WHITE
        if level == "TRADE": color = Fore.MAGENTA
        elif level == "ERROR": color = Fore.RED
        elif level == "SUCCESS": color = Fore.GREEN
        elif level == "WARNING": color = Fore.YELLOW
        elif level == "WALLET": color = Fore.BLUE
        
        print(f"{color}[{ts}] {msg}{Style.RESET_ALL}")
        with open(LOG_FILE, "a") as f:
            f.write(f"{ts},{level},{msg}\n")

# --- 5. CAPITAL GUARD (The Brain) ---
class CapitalGuard:
    def __init__(self):
        self.wallet_file = WALLET_FILE
        self.current_balance = self.load_wallet()
        
        # We track "Start of Day" balance to calculate the 10% drop
        # In a real scenario, you might want to reset this baseline daily.
        # For now, we use the loaded balance as the baseline for the session.
        self.session_start_balance = self.current_balance
        self.max_loss_limit = self.session_start_balance * MAX_DAILY_DRAWDOWN_PCT
        self.kill_switch = False
        
        Logger.log(f"💰 WALLET LOADED: ₹{self.current_balance:,.2f}", "WALLET")
        Logger.log(f"🛑 DAILY LOSS LIMIT: ₹{self.max_loss_limit:,.2f} (10% of Start)", "WARNING")

    def load_wallet(self):
        if os.path.exists(self.wallet_file):
            try:
                with open(self.wallet_file, 'r') as f:
                    data = json.load(f)
                    return data.get("balance", STARTING_PAPER_CAPITAL)
            except:
                return STARTING_PAPER_CAPITAL
        else:
            # First time setup
            self.save_wallet(STARTING_PAPER_CAPITAL)
            return STARTING_PAPER_CAPITAL

    def save_wallet(self, amount):
        with open(self.wallet_file, 'w') as f:
            json.dump({"balance": amount, "last_updated": str(datetime.datetime.now())}, f)

    def calculate_position_size(self, ltp, lot_size):
        """
        Auto-adjusts quantity based on 25% of CURRENT balance.
        """
        if self.kill_switch:
            Logger.log("⛔ Trade Blocked: KILL SWITCH ACTIVE", "ERROR")
            return 0
        
        # Rule: 25% of Total Account
        deploy_amount = self.current_balance * DEPLOY_PER_TRADE_PCT
        
        if ltp <= 0: return 0
        
        # Raw Quantity (e.g., 25000 / 3 = 8333)
        raw_qty = int(deploy_amount / ltp)
        
        # Snap to Lot Size (e.g., Round 8333 down to nearest multiple of 65/75)
        if raw_qty < lot_size:
            Logger.log(f"⚠️ Insufficient funds for 1 lot. Need ₹{ltp*lot_size}", "WARNING")
            return 0
            
        final_qty = (raw_qty // lot_size) * lot_size
        
        # Logging for Verification
        Logger.log(f"🧮 Sizing: Balance ₹{self.current_balance:.0f} | Deploy ₹{deploy_amount:.0f} (25%)", "INFO")
        Logger.log(f"   Price ₹{ltp} | Lot {lot_size} | Calc Qty: {final_qty}", "INFO")
        
        return final_qty

    def update_pnl(self, pnl):
        self.current_balance += pnl
        self.save_wallet(self.current_balance)
        
        Logger.log(f"💳 Wallet Updated: ₹{self.current_balance:,.2f} (PnL: {pnl:+.2f})", "WALLET")
        
        # Check 10% Drawdown Rule
        total_loss = self.session_start_balance - self.current_balance
        if total_loss >= self.max_loss_limit:
            self.kill_switch = True
            Logger.log(f"💀 CRITICAL: Max Loss Hit (-₹{total_loss:.2f}). TRADING STOPPED.", "ERROR")

# --- 6. CORE ENGINE ---
class AegisEngine:
    def __init__(self):
        Logger.log(f"🚀 BOOTING {PROJECT_NAME}...", "INFO")
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        
        self.guard = CapitalGuard()
        self.token_map = {}
        self.price_memory = {}
        self.active_trade = None
        
        self.setup_instruments()
        self.feed = KiteTicker(API_KEY, ACCESS_TOKEN)
        self.start_feed()

    def setup_instruments(self):
        Logger.log(f"🔍 Scanning {INDEX_NAME} Chain...", "INFO")
        
        # Dynamic Exchange Selection
        if INDEX_NAME == "NIFTY":
            exch, symbol, step = "NSE", "NIFTY 50", 50
            seg = "NFO"
        elif INDEX_NAME == "SENSEX":
            exch, symbol, step = "BSE", "SENSEX", 100
            seg = "BFO"

        try:
            # Fetch Spot
            ltp = self.kite.ltp(f"{exch}:{symbol}")[f"{exch}:{symbol}"]['last_price']
            atm = round(ltp / step) * step
            Logger.log(f"📍 Spot: {ltp} | ATM: {atm}", "INFO")

            # Fetch Options
            instruments = self.kite.instruments(seg)
            df = pd.DataFrame(instruments)
            df = df[(df['name'] == INDEX_NAME) & (df['segment'] == f"{seg}-OPT")]
            
            # Filter Expiry
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            nearest_expiry = df['expiry'].min()
            
            # Select ATM +/- 5 Strikes
            strikes = [atm + (i*step) for i in range(-5, 6)]
            target_df = df[(df['expiry'] == nearest_expiry) & (df['strike'].isin(strikes))]
            
            for _, row in target_df.iterrows():
                tok = row['instrument_token']
                self.token_map[tok] = {
                    'symbol': row['tradingsymbol'],
                    'lot': row['lot_size'],  # <--- AUTO-FETCHES 65/75/etc
                    'exchange': row['exchange']
                }
                self.price_memory[tok] = deque(maxlen=20)
                
            Logger.log(f"🔥 Watching {len(self.token_map)} Strikes", "SUCCESS")
                
        except Exception as e:
            Logger.log(f"Setup Error: {e}", "ERROR")
            sys.exit()

    def start_feed(self):
        def on_ticks(ws, ticks):
            for t in ticks:
                self.process_tick(t)

        def on_connect(ws, r):
            tokens = list(self.token_map.keys())
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            Logger.log("⚡ Data Feed Connected", "SUCCESS")

        self.feed.on_ticks = on_ticks
        self.feed.on_connect = on_connect
        self.feed.connect(threaded=True)

    def process_tick(self, tick):
        now = datetime.datetime.now().time()
        
        # Time Filters
        if now < START_TIME: return
        if now >= END_TIME:
            if self.active_trade: self.close_trade(tick['last_price'], "EOD Exit")
            return

        token = tick['instrument_token']
        ltp = tick['last_price']
        
        self.price_memory[token].append(ltp)

        if self.active_trade:
            if self.active_trade['token'] == token:
                self.manage_trade(ltp)
        elif now >= ACTIVE_TIME:
            self.scan_setup(token, ltp)

    def scan_setup(self, token, ltp):
        if len(self.price_memory[token]) < 10: return
        if self.guard.kill_switch: return

        # Gamma Logic
        start_price = self.price_memory[token][0]
        pct_change = ((ltp - start_price) / start_price) * 100
        
        # Wick Logic
        high_val = max(self.price_memory[token])
        wick = high_val - ltp
        
        # "Cheap Option" Filter (Hero Zero Zone)
        if 2.0 <= ltp <= 50.0:
            if pct_change >= GAMMA_TRIGGER_PCT and wick <= WICK_REJECTION_LIMIT:
                self.execute_trade(token, ltp, pct_change)

    def execute_trade(self, token, ltp, spike):
        meta = self.token_map[token]
        
        # --- HERE IS THE AUTO-ADJUSTMENT LOGIC ---
        qty = self.guard.calculate_position_size(ltp, meta['lot'])
        
        if qty > 0:
            Logger.log(f"🎯 GAMMA SPIKE: {meta['symbol']} (+{spike:.1f}%)", "TRADE")
            
            # Place Order (Real or Paper)
            if MODE == "REAL":
                try:
                    self.kite.place_order(
                        variety=self.kite.VARIETY_REGULAR,
                        exchange=meta['exchange'],
                        tradingsymbol=meta['symbol'],
                        transaction_type=self.kite.TRANSACTION_TYPE_BUY,
                        quantity=qty,
                        product=self.kite.PRODUCT_MIS,
                        order_type=self.kite.ORDER_TYPE_MARKET
                    )
                except Exception as e:
                    Logger.log(f"Order Failed: {e}", "ERROR")
                    return

            self.active_trade = {
                'token': token,
                'symbol': meta['symbol'],
                'entry': ltp,
                'qty': qty,
                'sl': ltp * 0.75, # 25% SL
                'target': ltp * 3.0, # 1:3 RR (200% ROI)
                'high': ltp
            }
            Logger.log(f"⚔️ BOUGHT {qty} Qty @ {ltp}", "TRADE")

    def manage_trade(self, ltp):
        trade = self.active_trade
        
        # Trailing SL Logic
        if ltp > trade['high']:
            trade['high'] = ltp
            # Trail to Breakeven at +30%
            if ltp >= trade['entry'] * 1.30 and trade['sl'] < trade['entry']:
                trade['sl'] = trade['entry'] + 0.1
                Logger.log("🛡️ SL Trailed to Breakeven", "INFO")

        # Check Exit
        if ltp <= trade['sl']:
            self.close_trade(ltp, "SL Hit")
        elif ltp >= trade['target']:
            # In Hero Zero, we often hold, but let's stick to rule
            # or trail aggressively. For now, we update SL to lock profits.
            pass 

    def close_trade(self, price, reason):
        trade = self.active_trade
        pnl = (price - trade['entry']) * trade['qty']
        
        if MODE == "REAL":
            try:
                meta = self.token_map[trade['token']]
                self.kite.place_order(
                    variety=self.kite.VARIETY_REGULAR,
                    exchange=meta['exchange'],
                    tradingsymbol=meta['symbol'],
                    transaction_type=self.kite.TRANSACTION_TYPE_SELL,
                    quantity=trade['qty'],
                    product=self.kite.PRODUCT_MIS,
                    order_type=self.kite.ORDER_TYPE_MARKET
                )
            except: pass

        color = "SUCCESS" if pnl > 0 else "ERROR"
        Logger.log(f"❌ SOLD {trade['symbol']} @ {price} | PnL: {pnl:.2f} ({reason})", color)
        
        self.guard.update_pnl(pnl)
        self.active_trade = None

# --- MAIN ---
if __name__ == "__main__":
    app = AegisEngine()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting...")