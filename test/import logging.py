import logging
import datetime
import os
import csv
import sys
import time
import threading
import pandas as pd
from kiteconnect import KiteConnect, KiteTicker

# --- CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""
INDEX_TO_WATCH = "NIFTY" # Change to "SENSEX" on Fridays

# --- PATHS ---
DATA_DIR = "market_data_unified"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class UnifiedMiner:
    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
        
        self.option_tokens = {} # {token: symbol_name}
        self.spot_token = None
        self.vix_token = None
        
        # Live State (Thread Safe)
        self.latest_spot = 0.0
        self.latest_vix = 0.0
        
        self.center_atm = 0
        self.step = 50 if INDEX_TO_WATCH == "NIFTY" else 100 # Default init
        self.csv_writer = None
        self.file_handle = None
        self.running = False
        self.lock = threading.Lock()

    def get_spot_details(self):
        if INDEX_TO_WATCH == "NIFTY":
            return "NSE", "NIFTY 50", "NFO", 50
        else:
            return "BSE", "SENSEX", "BFO", 100

    def setup_static_tokens(self):
        """Finds Token IDs for Spot Index and India VIX"""
        spot_exch, spot_sym, _, self.step = self.get_spot_details() # <--- Capture Step Here
        
        spot_key = f"{spot_exch}:{spot_sym}"
        vix_key = "NSE:INDIA VIX"

        # 1. Find Spot Token
        try:
            spot_inst = self.kite.ltp(spot_key)
            if spot_key not in spot_inst:
                logging.error(f"❌ Key {spot_key} not found in response. Check Exchange/Symbol.")
                sys.exit()
                
            self.spot_token = spot_inst[spot_key]['instrument_token']
            self.latest_spot = spot_inst[spot_key]['last_price']
            logging.info(f"📍 Found Spot: {spot_sym} ({self.spot_token}) @ {self.latest_spot}")
        except Exception as e:
            logging.error(f"❌ Failed to find Spot Token: {e}")
            sys.exit()

        # 2. Find VIX Token (Always NSE)
        try:
            vix_inst = self.kite.ltp(vix_key)
            self.vix_token = vix_inst[vix_key]['instrument_token']
            self.latest_vix = vix_inst[vix_key]['last_price']
            logging.info(f"📉 Found VIX: INDIA VIX ({self.vix_token}) @ {self.latest_vix}")
        except Exception as e:
            logging.error(f"❌ Failed to find VIX Token: {e}")
            sys.exit()

        return [self.spot_token, self.vix_token]

    def get_strikes_for_atm(self, atm_price):
        _, _, opt_exch, step = self.get_spot_details()
        self.step = step
        
        # Fetch Instruments
        try:
            instruments = self.kite.instruments(opt_exch)
        except Exception as e:
            logging.error(f"Failed to fetch instruments: {e}")
            return {}

        df = pd.DataFrame(instruments)
        
        name_filter = "NIFTY" if INDEX_TO_WATCH == "NIFTY" else "SENSEX"
        df = df[(df['name'] == name_filter) & (df['segment'].str.contains("OPT"))]
        df['expiry'] = pd.to_datetime(df['expiry']).dt.date
        
        today = datetime.date.today()
        # Ensure we look for expiries >= today
        valid_expiries = sorted(df[df['expiry'] >= today]['expiry'].unique())
        if not valid_expiries: 
            logging.error("No valid expiry found.")
            return {}
        
        current_expiry = valid_expiries[0]
        logging.info(f"🗓️ Target Expiry: {current_expiry}")
        
        # Select Strikes (ATM +/- 5)
        strikes = [atm_price + (i * step) for i in range(-5, 6)]
        final_df = df[(df['expiry'] == current_expiry) & (df['strike'].isin(strikes))]
        
        new_tokens = {}
        for _, row in final_df.iterrows():
            new_tokens[row['instrument_token']] = row['tradingsymbol']
            
        return new_tokens

    def init_csv(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(DATA_DIR, f"{INDEX_TO_WATCH}_UNIFIED_{ts}.csv")
        self.file_handle = open(filename, 'w', newline='')
        self.csv_writer = csv.writer(self.file_handle)
        
        # THE GOLDEN HEADER
        self.csv_writer.writerow([
            "SystemTime", "ExchangeTime", "Token", "Symbol", 
            "LTP", "BidPrice", "AskPrice", "Vol", "OI", "VWAP", 
            "Spread", "Spot_LTP", "VIX_LTP"
        ])
        logging.info(f"📂 Saving Golden Data to: {filename}")

    def watchdog(self):
        """ Checks Spot Price every 60s and updates tokens if needed """
        logging.info("🐕 Watchdog Started...")
        while self.running:
            time.sleep(60) 
            try:
                if self.latest_spot == 0 or self.step == 0: continue

                current_atm = round(self.latest_spot / self.step) * self.step
                
                if abs(current_atm - self.center_atm) >= (2 * self.step):
                    logging.info(f"🔄 Market Moved! Spot: {self.latest_spot}. Re-centering ATM...")
                    new_token_dict = self.get_strikes_for_atm(current_atm)
                    
                    tokens_to_sub = []
                    with self.lock:
                        for t, s in new_token_dict.items():
                            if t not in self.option_tokens:
                                self.option_tokens[t] = s
                                tokens_to_sub.append(t)
                        self.center_atm = current_atm

                    if tokens_to_sub:
                        self.ticker.subscribe(tokens_to_sub)
                        self.ticker.set_mode(self.ticker.MODE_FULL, tokens_to_sub)
                        logging.info(f"➕ Added {len(tokens_to_sub)} new strikes.")
            except Exception as e:
                logging.error(f"Watchdog Error: {e}")

    def start(self):
        # 1. Setup Static Tokens (Spot/VIX)
        static_tokens = self.setup_static_tokens()
        
        # 2. Setup Initial Options
        # self.step is now guaranteed to be set by setup_static_tokens or __init__
        atm = round(self.latest_spot / self.step) * self.step
        self.center_atm = atm
        self.option_tokens = self.get_strikes_for_atm(atm)
        
        if not self.option_tokens:
            logging.error("No options found. Exiting.")
            sys.exit()

        all_tokens = static_tokens + list(self.option_tokens.keys())
        self.init_csv()
        
        # 3. Ticker Logic
        def on_ticks(ws, ticks):
            for t in ticks:
                token = t['instrument_token']
                ltp = t.get('last_price', 0)
                
                # CASE A: Update Spot/VIX State
                if token == self.spot_token:
                    self.latest_spot = ltp
                    continue # Don't record Spot ticks to CSV, just update state
                elif token == self.vix_token:
                    self.latest_vix = ltp
                    continue

                # CASE B: Record Option Data
                name = self.option_tokens.get(token)
                if not name: continue 

                # Extract Data
                sys_time = datetime.datetime.now().strftime("%H:%M:%S.%f")
                exch_time = t.get('exchange_timestamp')
                vol = t.get('volume_traded', 0)
                oi = t.get('oi', 0)
                vwap = t.get('average_price', 0)
                
                bid_p, ask_p = 0, 0
                if 'depth' in t:
                    if t['depth']['buy']: bid_p = t['depth']['buy'][0]['price']
                    if t['depth']['sell']: ask_p = t['depth']['sell'][0]['price']
                
                spread = round(ask_p - bid_p, 2)

                try:
                    self.csv_writer.writerow([
                        sys_time, exch_time, token, name, 
                        ltp, bid_p, ask_p, vol, oi, vwap, 
                        spread, self.latest_spot, self.latest_vix
                    ])
                except: pass

        def on_connect(ws, response):
            ws.subscribe(all_tokens)
            ws.set_mode(ws.MODE_FULL, all_tokens)
            logging.info(f"🚀 MINER LIVE. Watching {len(all_tokens)} Instruments.")

        def on_error(ws, code, reason):
             logging.error(f"Ticker Error: {reason}")

        self.ticker.on_ticks = on_ticks
        self.ticker.on_connect = on_connect
        self.ticker.on_error = on_error
        
        self.running = True
        t = threading.Thread(target=self.watchdog)
        t.daemon = True
        t.start()
        
        self.ticker.connect()

if __name__ == "__main__":
    miner = UnifiedMiner()
    try:
        miner.start()
    except KeyboardInterrupt:
        miner.running = False
        print("\n🛑 Stopped.")