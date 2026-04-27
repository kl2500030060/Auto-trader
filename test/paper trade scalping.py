import os, time, datetime, sys, requests, threading
import pandas as pd
import pandas_ta_classic as ta
from kiteconnect import KiteConnect, KiteTicker
from tabulate import tabulate
from colorama import Fore, Style, init

# Initialize Colors
init(autoreset=True)

# --- USER CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""

# --- TELEGRAM CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "8403730592:AAEm9PwpVjL33l_jHX9dNk4nlF3IIw6xsFU"   # <--- ENTER FROM BOTFATHER
TELEGRAM_CHAT_ID = "6651189484"       # <--- ENTER FROM USERINFOBOT

# SCALPING SETTINGS
INITIAL_CAPITAL = 100000.0   
TARGET_PCT = 0.015           
STOPLOSS_PCT = 0.005         
CHARGES_PER_ORDER = 30.0     
TIMEFRAME = "minute"         

# --- 1. THE COURIER (TELEGRAM ENGINE) ---
class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def send(self, message):
        """Sends message in a separate thread to prevent blocking the trade loop"""
        if self.chat_id == "your_chat_id": return # Skip if not configured
        
        def _req():
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                requests.post(self.base_url, json=payload, timeout=5)
            except Exception as e:
                print(f"{Fore.RED}⚠️ Telegram Failed: {e}{Style.RESET_ALL}")
        
        # Fire and forget thread
        threading.Thread(target=_req, daemon=True).start()

# --- 2. THE ACCOUNTANT ---
class AccountManager:
    def __init__(self, capital, telegram_bot):
        self.balance = capital
        self.start_cap = capital
        self.total_charges = 0.0
        self.ledger = [] 
        self.active_position = None 
        self.tg = telegram_bot # Link the courier

    def calculate_max_qty(self, ltp):
        usable_cash = self.balance * 0.98
        qty = int(usable_cash / ltp)
        lot_size = 50 # NIFTY Lot
        return (qty // lot_size) * lot_size

    def execute_buy(self, symbol, token, ltp, timestamp):
        qty = self.calculate_max_qty(ltp)
        if qty < 50:
            print(f"{Fore.RED}❌ Insufficient funds!{Style.RESET_ALL}")
            return False

        cost = qty * ltp
        self.balance -= cost
        
        self.active_position = {
            'symbol': symbol, 'token': token, 'type': 'BUY',
            'qty': qty, 'buy_price': ltp, 'time': timestamp
        }
        
        log_msg = f"🟢 *BUY EXECUTED*\n\nSymbol: `{symbol}`\nQty: {qty}\nPrice: {ltp}\nInvested: ₹{cost:,.2f}"
        print(f"{Fore.GREEN}>>> {log_msg.replace('*', '').replace('`', '')}{Style.RESET_ALL}")
        self.tg.send(log_msg) # <--- SEND ALERT
        return True

    def execute_sell(self, ltp, timestamp, reason):
        pos = self.active_position
        if not pos: return

        revenue = pos['qty'] * ltp
        gross_pnl = revenue - (pos['qty'] * pos['buy_price'])
        trade_charges = CHARGES_PER_ORDER * 2 
        net_pnl = gross_pnl - trade_charges
        
        self.balance += revenue 
        self.balance -= trade_charges
        self.total_charges += trade_charges
        
        # Format for Ledger
        self.ledger.append([
            timestamp, pos['symbol'], pos['qty'], pos['buy_price'], 
            ltp, f"{gross_pnl:.2f}", f"{trade_charges:.2f}",
            f"{net_pnl:.2f}", f"{self.balance:.2f}", reason
        ])
        
        # Telegram Message
        emoji = "✅" if net_pnl > 0 else "🛑"
        log_msg = (f"{emoji} *SELL EXECUTED ({reason})*\n\n"
                   f"Price: {ltp}\n"
                   f"PnL: ₹{net_pnl:,.2f}\n"
                   f"New Balance: ₹{self.balance:,.2f}")
        
        self.active_position = None
        print(f"{Fore.YELLOW}>>> {log_msg.replace('*', '')}{Style.RESET_ALL}\n")
        self.print_ledger()
        self.tg.send(log_msg) # <--- SEND ALERT

    def print_ledger(self):
        headers = ["Time", "Symbol", "Qty", "Buy", "Sell", "Gross PnL", "Fees", "Net PnL", "Cap", "Reason"]
        print("\n" + tabulate(self.ledger, headers=headers, tablefmt="simple"))

# --- 3. STRATEGY ENGINE ---
class ScalpBot:
    def __init__(self):
        print(f"{Fore.YELLOW}🚀 Initializing Telegram Scalper...{Style.RESET_ALL}")
        try:
            self.kite = KiteConnect(api_key=API_KEY)
            self.kite.set_access_token(ACCESS_TOKEN)
            self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        except:
            print("❌ Connection Failed. Check Credentials.")
            sys.exit()

        # Initialize Telegram
        self.tg_bot = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        self.tg_bot.send(f"🤖 *Algo Started*\nCapital: ₹{INITIAL_CAPITAL:,.0f}")

        self.acct = AccountManager(INITIAL_CAPITAL, self.tg_bot)
        self.target_token = None
        
    def get_itm_contract(self):
        print("🔍 Scanning for Targets...")
        spot_sym = "NSE:NIFTY 50"
        ltp = self.kite.ltp(spot_sym)[spot_sym]['last_price']
        
        strike = round(ltp / 50) * 50
        itm_strike = strike - 50 # ITM Call
        
        nfo = pd.DataFrame(self.kite.instruments("NFO"))
        expiry = nfo[nfo['name'] == "NIFTY"]['expiry'].min()
        
        contract = nfo[(nfo['strike'] == itm_strike) & 
                       (nfo['expiry'] == expiry) & 
                       (nfo['instrument_type'] == "CE")].iloc[0]
        
        # FIX: Convert numpy.int64 to native python int
        self.target_token = int(contract['instrument_token'])
        self.target_symbol = contract['tradingsymbol']
        
        msg = f"🎯 *Target Locked*\nSymbol: `{self.target_symbol}`\nStrike: {itm_strike} CE"
        print(msg.replace("*", "").replace("`", ""))
        self.tg_bot.send(msg)

    def run(self):
        self.get_itm_contract()
        self.kws.on_ticks = self.on_ticks
        self.kws.connect(threaded=True)
        time.sleep(2) 
        self.kws.subscribe([self.target_token])
        self.kws.set_mode(self.kws.MODE_FULL, [self.target_token])
        print(f"{Fore.GREEN}✅ Listening for Signals...{Style.RESET_ALL}")
        while True: time.sleep(1)

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            if tick['instrument_token'] == self.target_token:
                self.process_logic(tick['last_price'])

    def process_logic(self, ltp):
        # EXIT LOGIC
        if self.acct.active_position:
            buy_price = self.acct.active_position['buy_price']
            pct_change = (ltp - buy_price) / buy_price
            
            if pct_change >= TARGET_PCT:
                self.acct.execute_sell(ltp, datetime.datetime.now().strftime("%H:%M:%S"), "TARGET HIT")
            elif pct_change <= -STOPLOSS_PCT:
                self.acct.execute_sell(ltp, datetime.datetime.now().strftime("%H:%M:%S"), "STOPLOSS")
            return

        # ENTRY LOGIC
        now = datetime.datetime.now()
        if now.second % 5 != 0: return 

        try:
            data = self.kite.historical_data(self.target_token, 
                                           now - datetime.timedelta(minutes=30), 
                                           now, TIMEFRAME)
            df = pd.DataFrame(data)
            if df.empty: return

            df.ta.rsi(length=14, append=True)
            df.ta.ema(length=9, append=True)
            
            current = df.iloc[-1]
            rsi = current['RSI_14']
            ema = current['EMA_9']
            
            if rsi > 55 and ltp > ema:
                timestamp = now.strftime("%H:%M:%S")
                self.acct.execute_buy(self.target_symbol, self.target_token, ltp, timestamp)
                
        except Exception as e:
            print(f"Data Error: {e}")

if __name__ == "__main__":
    bot = ScalpBot()
    bot.run()