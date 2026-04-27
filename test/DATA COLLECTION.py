import logging
import datetime
import os
import csv
import sys
import time
from kiteconnect import KiteConnect, KiteTicker

# --- CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""
INDEX_TO_WATCH = "NIFTY" # Change to SENSEX on Fridays

# --- PATHS ---
DATA_DIR = "market_data_blackbox"
if not os.path.exists(DATA_DIR): os.makedirs(DATA_DIR)

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class BlackBoxMiner:
    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
        self.tokens = {} # {token: symbol_name}
        self.csv_writer = None
        self.file_handle = None

    def get_expiry_tokens(self):
        logging.info(f"🔍 Scanning {INDEX_TO_WATCH} Option Chain...")
        
        exchange = "NFO" if INDEX_TO_WATCH == "NIFTY" else "BFO"
        symbol = "NIFTY 50" if INDEX_TO_WATCH == "NIFTY" else "SENSEX"
        
        # 1. Get Spot Price
        ltp = self.kite.ltp(f"{exchange}:{symbol}")[f"{exchange}:{symbol}"]['last_price']
        
        # 2. Round to ATM
        step = 50 if INDEX_TO_WATCH == "NIFTY" else 100
        atm = round(ltp / step) * step
        logging.info(f"📍 Spot: {ltp} | ATM: {atm}")
        
        # 3. Fetch Instruments
        instruments = self.kite.instruments(exchange)
        import pandas as pd
        df = pd.DataFrame(instruments)
        
        # Filter for Index Options
        df = df[(df['name'] == INDEX_TO_WATCH) & (df['segment'].str.contains("OPT"))]
        df['expiry'] = pd.to_datetime(df['expiry']).dt.date
        
        # Get Nearest Expiry
        today = datetime.date.today()
        # Filter expiries >= today
        valid_expiries = sorted(df[df['expiry'] >= today]['expiry'].unique())
        if not valid_expiries:
            logging.error("No valid expiry found!")
            sys.exit()
            
        current_expiry = valid_expiries[0]
        logging.info(f"🗓️ Target Expiry: {current_expiry}")
        
        # Select Strikes (ATM +/- 5)
        strikes = [atm + (i * step) for i in range(-5, 6)]
        
        final_df = df[(df['expiry'] == current_expiry) & (df['strike'].isin(strikes))]
        
        token_dict = {}
        for _, row in final_df.iterrows():
            token_dict[row['instrument_token']] = row['tradingsymbol']
            
        logging.info(f"🔥 Monitoring {len(token_dict)} Strikes")
        return token_dict

    def init_csv(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(DATA_DIR, f"{INDEX_TO_WATCH}_TICK_DATA_{ts}.csv")
        self.file_handle = open(filename, 'w', newline='')
        self.csv_writer = csv.writer(self.file_handle)
        
        # Header with Depth Info
        self.csv_writer.writerow([
            "SystemTime", "ExchangeTime", "Token", "Symbol", "LTP", 
            "BidPrice", "BidQty", "AskPrice", "AskQty", "Volume", "OI"
        ])
        logging.info(f"📂 Saving Data to: {filename}")

    def start(self):
        self.tokens = self.get_expiry_tokens()
        self.init_csv()
        
        token_list = list(self.tokens.keys())
        
        def on_ticks(ws, ticks):
            for t in ticks:
                token = t['instrument_token']
                name = self.tokens.get(token, "UNKNOWN")
                
                # Timestamps
                sys_time = datetime.datetime.now().strftime("%H:%M:%S.%f")
                exch_time = t.get('exchange_timestamp')
                
                # Price Data
                ltp = t.get('last_price', 0)
                vol = t.get('volume_traded', 0)
                oi = t.get('oi', 0)
                
                # Market Depth (Crucial for Slippage Analysis)
                bid_p = 0
                bid_q = 0
                ask_p = 0
                ask_q = 0
                
                if 'depth' in t:
                    buy_depth = t['depth'].get('buy', [])
                    sell_depth = t['depth'].get('sell', [])
                    
                    if buy_depth:
                        bid_p = buy_depth[0]['price']
                        bid_q = buy_depth[0]['quantity']
                    if sell_depth:
                        ask_p = sell_depth[0]['price']
                        ask_q = sell_depth[0]['quantity']

                # Write to CSV
                try:
                    self.csv_writer.writerow([
                        sys_time, exch_time, token, name, ltp,
                        bid_p, bid_q, ask_p, ask_q, vol, oi
                    ])
                except Exception as e:
                    logging.error(f"Write Error: {e}")

        def on_connect(ws, response):
            ws.subscribe(token_list)
            ws.set_mode(ws.MODE_FULL, token_list) # Full Mode for Depth
            logging.info("🚀 Data Miner Running...")

        self.ticker.on_ticks = on_ticks
        self.ticker.on_connect = on_connect
        self.ticker.connect()

if __name__ == "__main__":
    miner = BlackBoxMiner()
    miner.start()