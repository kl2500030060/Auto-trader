import logging
import datetime
import pandas as pd
import csv
import os
import time
from kiteconnect import KiteConnect, KiteTicker

# --- USER CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""

# --- EXPIRY SETTINGS (UPDATED FOR SENSEX) ---
OPTION_INDEX = "SENSEX"      # Target Index
EXCHANGE = "BFO"             # BSE Futures & Options
SEGMENT = "BFO-OPT"          # Segment Name
STEP_SIZE = 100              # Sensex Strike Difference
LOT_SIZE = 10                # Sensex Lot Size

# --- PATH CONFIGURATION ---
USER_DOCUMENTS = os.path.expanduser("~/Documents")
DATA_FOLDER = os.path.join(USER_DOCUMENTS, "Unified_Recorder_Data")
TIMESTAMP_STR = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
FILENAME = os.path.join(DATA_FOLDER, f"UNIFIED_{OPTION_INDEX}_{TIMESTAMP_STR}.csv")

if not os.path.exists(DATA_FOLDER): os.makedirs(DATA_FOLDER)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

class UnifiedSensexRecorder:
    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        
        self.token_map = {}
        self.latest_data = {
            "SENSEX_SPOT": 0.0,
            "NIFTY_SPOT": 0.0,
            "INDIA_VIX": 0.0
        }
        self.file_handle = None
        self.writer = None
        print(f"📂 SENSEX RECORDING STARTED: {FILENAME}")

    def get_instruments(self):
        logging.info("⏳ Fetching SENSEX Instruments...")
        tokens_to_sub = []
        
        try:
            # 1. Fetch Spot Indices (NSE & BSE)
            # We need NSE for VIX and BSE for Sensex Spot
            nse_inst = pd.DataFrame(self.kite.instruments("NSE"))
            bse_inst = pd.DataFrame(self.kite.instruments("BSE"))
            
            def add_index(df, name, key):
                row = df[df['tradingsymbol'] == name]
                if not row.empty:
                    tok = int(row.iloc[0]['instrument_token'])
                    self.token_map[tok] = {"Symbol": key, "Type": "INDEX", "Key": key}
                    tokens_to_sub.append(tok)
                    logging.info(f"✅ Added Index: {key} ({tok})")
                    return tok
            
            add_index(bse_inst, "SENSEX", "SENSEX_SPOT")
            add_index(nse_inst, "NIFTY 50", "NIFTY_SPOT")
            add_index(nse_inst, "INDIA VIX", "INDIA_VIX")

            # 2. Fetch Sensex Options (BFO)
            # Get Current Spot Price
            spot_ltp = self.kite.ltp("BSE:SENSEX")["BSE:SENSEX"]["last_price"]
            atm_strike = round(spot_ltp / STEP_SIZE) * STEP_SIZE
            logging.info(f"📍 Sensex Spot: {spot_ltp} | ATM: {atm_strike}")
            
            # Fetch BFO Instruments
            opt_inst = pd.DataFrame(self.kite.instruments(EXCHANGE))
            
            # Filter for SENSEX Options
            df = opt_inst[(opt_inst['name'] == OPTION_INDEX) & (opt_inst['segment'] == SEGMENT)].copy()
            
            # Get Nearest Expiry
            df['expiry'] = pd.to_datetime(df['expiry']).dt.date
            today = datetime.date.today()
            valid_expiries = sorted(df[df['expiry'] >= today]['expiry'].unique())
            
            if not valid_expiries:
                logging.error("❌ No Future Expiries Found!")
                return tokens_to_sub
                
            target_expiry = valid_expiries[0]
            logging.info(f"📅 Target Expiry: {target_expiry}")
            
            # Select Strikes (ATM +/- 5 Strikes i.e., +/- 500 points)
            strikes = [atm_strike + (i * STEP_SIZE) for i in range(-5, 6)]
            
            target_df = df[(df['expiry'] == target_expiry) & (df['strike'].isin(strikes))]
            
            for _, row in target_df.iterrows():
                tok = int(row['instrument_token'])
                sym = row['tradingsymbol']
                self.token_map[tok] = {"Symbol": sym, "Type": "OPTION"}
                tokens_to_sub.append(tok)
            
            logging.info(f"🔥 Tracking {len(target_df)} Sensex Options")
            return tokens_to_sub

        except Exception as e:
            logging.error(f"Error fetching tokens: {e}")
            return []

    def init_csv(self):
        self.file_handle = open(FILENAME, 'w', newline='')
        self.writer = csv.writer(self.file_handle)
        header = [
            "SystemTime", "ExchangeTime", "Token", "Symbol", 
            "LTP", "BidPrice", "AskPrice", "Vol", "OI", 
            "Spread", "Spot_LTP", "VIX_LTP"
        ]
        self.writer.writerow(header)

    def on_ticks(self, ws, ticks):
        sys_time = datetime.datetime.now().strftime('%H:%M:%S.%f')
        
        for tick in ticks:
            token = tick['instrument_token']
            
            # Update Cache
            if token in self.token_map:
                info = self.token_map[token]
                ltp = tick.get('last_price', 0)
                
                if info["Type"] == "INDEX":
                    self.latest_data[info["Key"]] = ltp
            
            # Record Option Data
            if token in self.token_map and self.token_map[token]["Type"] == "OPTION":
                info = self.token_map[token]
                symbol = info["Symbol"]
                
                exch_time = tick.get('exchange_timestamp')
                if exch_time: exch_time = exch_time.strftime('%Y-%m-%d %H:%M:%S')
                else: exch_time = sys_time
                
                ltp = tick.get('last_price', 0)
                vol = tick.get('volume_traded', 0)
                oi = tick.get('oi', 0)
                
                bid = tick['depth']['buy'][0]['price'] if 'depth' in tick and tick['depth']['buy'] else 0
                ask = tick['depth']['sell'][0]['price'] if 'depth' in tick and tick['depth']['sell'] else 0
                spread = round(ask - bid, 2) if bid > 0 else 0
                
                # Context
                spot = self.latest_data["SENSEX_SPOT"]
                vix = self.latest_data["INDIA_VIX"]
                
                try:
                    self.writer.writerow([
                        sys_time, exch_time, token, symbol, 
                        ltp, bid, ask, vol, oi, 
                        spread, spot, vix
                    ])
                except: pass
        
        self.file_handle.flush()

    def on_connect(self, ws, response):
        tokens = self.get_instruments()
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)

    def start(self):
        self.init_csv()
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.connect()

if __name__ == "__main__":
    recorder = UnifiedSensexRecorder()
    recorder.start()