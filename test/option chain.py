import os, sys, time, datetime, threading
from collections import deque
import pandas as pd
import numpy as np
from kiteconnect import KiteConnect, KiteTicker
from colorama import Fore, Back, Style, init
from tabulate import tabulate

init(autoreset=True)

# --- CONFIGURATION ---
API_KEY =""
ACCESS_TOKEN =""

# Settings
VWMA_PERIOD = 20  # Rolling window
REFRESH_RATE = 0.2 # Fast refresh to catch the 'Flash'
RANGE = 10        # 10 Strikes Up / 10 Down

# Global Data Store
live_data = {}    
token_map = {}    
display_cache = {} # MEMORY: Stores last printed LTP to determine flash color

# --- HELPER CLASSES ---

class InstrumentLoader:
    def __init__(self, kite):
        self.kite = kite
        self.nfo_df = None
        self.bfo_df = None
        
    def load(self, exchange):
        print(f"⏳ Downloading {exchange} Instruments...", end="\r")
        if exchange == 'NFO' and self.nfo_df is None:
            self.nfo_df = pd.DataFrame(self.kite.instruments('NFO'))
        elif exchange == 'BFO' and self.bfo_df is None:
            self.bfo_df = pd.DataFrame(self.kite.instruments('BFO'))
        return self.nfo_df if exchange == 'NFO' else self.bfo_df

class OptionChainLogic:
    def __init__(self, index_name, spot_price, loader_obj):
        self.index = index_name
        self.spot = spot_price
        
        if index_name == "NIFTY":
            self.step = 50
            self.symbol = "NIFTY"
            self.exchange = "NFO"
        elif index_name == "SENSEX":
            self.step = 100
            self.symbol = "SENSEX"
            self.exchange = "BFO"
            
        self.df = loader_obj.load(self.exchange)
        self.atm = round(self.spot / self.step) * self.step
        
    def get_strikes(self):
        strikes = []
        for i in range(-RANGE, RANGE + 1):
            strikes.append(self.atm + (i * self.step))
        return strikes

    def get_tokens(self, strikes):
        df = self.df
        # Filter for Options (OPT) matching Index Name & Strikes
        relevant = df[
            (df['name'] == self.symbol) & 
            (df['segment'].str.contains('OPT')) &
            (df['strike'].isin(strikes))
        ]
        
        # Get Nearest Expiry
        relevant['expiry'] = pd.to_datetime(relevant['expiry'])
        min_expiry = relevant['expiry'].min()
        final_batch = relevant[relevant['expiry'] == min_expiry]
        
        new_tokens = []
        global token_map
        token_map = {} 
        
        for _, row in final_batch.iterrows():
            tok = row['instrument_token']
            new_tokens.append(tok)
            token_map[tok] = {
                'strike': row['strike'],
                'type': row['instrument_type'],
                'symbol': row['tradingsymbol']
            }
            
            # Initialize Data Store if new
            if tok not in live_data:
                live_data[tok] = {
                    'ltp': 0, 'change': 0, 
                    'vwap': 0, 'last_vol': 0,
                    'vwma_deque': deque(maxlen=VWMA_PERIOD), 
                    'vwma': 0
                }
        return new_tokens

# --- MAIN APP ---

class CommanderChain:
    def __init__(self):
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)
        self.loader = InstrumentLoader(self.kite)
        
        self.current_tokens = []
        self.current_atm = 0
        self.spot_token = 0
        
        # User Selection
        print(f"{Fore.YELLOW}1. NIFTY\n2. SENSEX{Style.RESET_ALL}")
        choice = input("Select Index: ")
        self.index_name = "SENSEX" if choice == '2' else "NIFTY"
        self.idx_details = self.get_index_details(self.index_name)
        
        # Init Chain
        self.initialize_chain()
        
        # Websocket Setup
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.connect(threaded=True)
        
        self.run_display_loop()

    def get_index_details(self, name):
        # Fetch Spot Token
        q = self.kite.quote(f"NSE:{name} 50" if name == "NIFTY" else "BSE:SENSEX")
        key = list(q.keys())[0]
        return {'token': q[key]['instrument_token'], 'ltp': q[key]['last_price']}

    def initialize_chain(self):
        spot_ltp = self.idx_details['ltp']
        logic = OptionChainLogic(self.index_name, spot_ltp, self.loader)
        self.current_atm = logic.atm
        strikes = logic.get_strikes()
        self.current_tokens = logic.get_tokens(strikes)
        
        # Add Spot token to list so we keep getting spot updates
        self.current_tokens.append(self.idx_details['token'])
        self.spot_token = self.idx_details['token']

    def on_connect(self, ws, response):
        ws.subscribe(self.current_tokens)
        ws.set_mode(ws.MODE_FULL, self.current_tokens)

    def update_subscriptions(self, new_tokens):
        # Only subscribe to new, unsubscribe from old to save bandwidth
        old_set = set(self.current_tokens)
        new_set = set(new_tokens)
        to_sub = list(new_set - old_set)
        to_unsub = list(old_set - new_set)
        
        if to_sub:
            self.kws.subscribe(to_sub)
            self.kws.set_mode(self.kws.MODE_FULL, to_sub)
        if to_unsub:
            self.kws.unsubscribe(to_unsub)
        self.current_tokens = list(new_set)

    def on_ticks(self, ws, ticks):
        for t in ticks:
            tok = t['instrument_token']
            
            # Handle Spot Price Update
            if tok == self.spot_token:
                self.idx_details['ltp'] = t['last_price']
                continue
            
            # Handle Option Update
            if tok in live_data:
                d = live_data[tok]
                ltp = t['last_price']
                vol = t['volume_traded']
                
                d['ltp'] = ltp
                d['change'] = t.get('change', 0)
                d['vwap'] = t.get('average_price', 0)
                
                # VWMA Logic
                tick_vol = vol - d['last_vol']
                if tick_vol < 0: tick_vol = 0 
                d['last_vol'] = vol
                
                if tick_vol > 0:
                    d['vwma_deque'].append((ltp, tick_vol))
                
                if len(d['vwma_deque']) > 0:
                    sum_pv = sum(p * v for p, v in d['vwma_deque'])
                    sum_v = sum(v for _, v in d['vwma_deque'])
                    d['vwma'] = sum_pv / sum_v if sum_v > 0 else ltp
                else:
                    d['vwma'] = ltp

    def run_display_loop(self):
        while True:
            # 1. Dynamic ATM Check
            spot = self.idx_details['ltp']
            step = 50 if self.index_name == "NIFTY" else 100
            new_atm = round(spot / step) * step
            
            # If ATM shifted, regenerate the whole chain
            if new_atm != self.current_atm:
                logic = OptionChainLogic(self.index_name, spot, self.loader)
                strikes = logic.get_strikes()
                new_tokens = logic.get_tokens(strikes)
                new_tokens.append(self.spot_token)
                self.update_subscriptions(new_tokens)
                self.current_atm = new_atm

            # 2. Render Screen
            self.render()
            time.sleep(REFRESH_RATE)

    def render(self):
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"{Back.BLUE}{Fore.WHITE} LIVE CHAIN: {self.index_name} ({self.idx_details['ltp']}) | ATM: {self.current_atm} {Style.RESET_ALL}\n")
        
        # Organize Data
        chain_rows = {}
        for tok, info in token_map.items():
            strike = info['strike']
            typ = info['type']
            if strike not in chain_rows: chain_rows[strike] = {'CE': None, 'PE': None}
            chain_rows[strike][typ] = {'data': live_data.get(tok, {}), 'token': tok}

        table_data = []
        sorted_strikes = sorted(chain_rows.keys())
        
        for k in sorted_strikes:
            row = chain_rows[k]
            
            def fmt(info):
                if not info or not info['data'] or info['data']['ltp'] == 0: 
                    return ["-", "-", "-", "-"]
                
                data = info['data']
                token = info['token']
                ltp = data['ltp']
                
                # --- VISUAL FLASH LOGIC ---
                # Check what we printed last time for this specific token
                prev_disp = display_cache.get(token, ltp)
                
                if ltp > prev_disp:
                    # PRICE UP: Green Background
                    ltp_str = f"{Back.GREEN}{Fore.BLACK} {ltp:.2f} {Style.RESET_ALL}"
                elif ltp < prev_disp:
                    # PRICE DOWN: Red Background
                    ltp_str = f"{Back.RED}{Fore.WHITE} {ltp:.2f} {Style.RESET_ALL}"
                else:
                    # NO CHANGE: Normal Text
                    ltp_str = f"{Fore.WHITE} {ltp:.2f} {Style.RESET_ALL}"
                
                # Update memory for next loop
                display_cache[token] = ltp
                
                # Other columns text color based on Day Change (+/-)
                c = Fore.GREEN if data['change'] >= 0 else Fore.RED
                
                return [
                    f"{c}{data['vwma']:.2f}{Style.RESET_ALL}",
                    f"{c}{data['vwap']:.2f}{Style.RESET_ALL}",
                    f"{c}{data['change']:.2f}%{Style.RESET_ALL}",
                    ltp_str # <--- Only this cell flashes background
                ]

            ce_cols = fmt(row['CE'])
            pe_cols = fmt(row['PE'])
            
            # Highlight ATM Strike Row
            strike_str = f"{Fore.CYAN}{k}{Style.RESET_ALL}"
            if k == self.current_atm:
                strike_str = f"{Back.YELLOW}{Fore.BLACK} {k} {Style.RESET_ALL}"
            
            # Build Row: CE Data | Strike | PE Data
            full_row = ce_cols + [strike_str] + pe_cols[::-1]
            table_data.append(full_row)

        headers = ["C-VWMA", "C-VWAP", "C-CHG", "C-LTP", "STRIKE", "P-LTP", "P-CHG", "P-VWAP", "P-VWMA"]
        print(tabulate(table_data, headers=headers, tablefmt="simple", stralign="center"))

if __name__ == "__main__":
    try:
        app = CommanderChain()
    except KeyboardInterrupt:
        sys.exit()