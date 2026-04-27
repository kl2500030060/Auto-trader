import os, time, datetime, threading, sys
import pandas as pd
import numpy as np
import pandas_ta_classic as ta
import gymnasium as gym
from gym_anytrading.envs import StocksEnv
from stable_baselines3 import PPO
from kiteconnect import KiteConnect, KiteTicker
from colorama import Fore, Style, init

init(autoreset=True)

API_KEY =""
ACCESS_TOKEN =""
SYMBOL = "NSE:NIFTY 50"
TOKEN = 256265 # NIFTY 50 Token (Example)
TIMEFRAME_MINUTES = 1
TRAIN_INTERVAL = 30 # Retrain model every 30 candles

# --- 1. REAL-TIME CANDLE MANAGER ---
class CandleManager:
    def __init__(self):
        self.ticks = []
        self.candles = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])
        self.last_candle_time = None

    def add_tick(self, ltp, volume=0):
        # Simple tick accumulator
        self.ticks.append(ltp)

    def close_candle(self):
        if not self.ticks: return None
        
        # Build candle from accumulated ticks
        c_open = self.ticks[0]
        c_high = max(self.ticks)
        c_low = min(self.ticks)
        c_close = self.ticks[-1]
        
        # Create DataFrame Row
        ts = datetime.datetime.now()
        new_row = pd.DataFrame([[c_open, c_high, c_low, c_close, 0]], 
                               columns=['Open', 'High', 'Low', 'Close', 'Volume'], 
                               index=[ts])
        
        # Append to main dataframe
        self.candles = pd.concat([self.candles, new_row])
        self.ticks = [] # Reset buffer
        
        # Add Indicators (The "Eyes" of the AI)
        if len(self.candles) > 15:
            self.candles['RSI'] = ta.rsi(self.candles['Close'], length=14)
            self.candles['SMA'] = ta.sma(self.candles['Close'], length=20)
            self.candles['Pct_Change'] = self.candles['Close'].pct_change()
            
        return self.candles.iloc[-1] # Return latest candle

# --- 2. THE LEARNING ENVIRONMENT ---
# We wrap the data in a Gym environment so PPO can learn from it
def feature_process(env):
    start = env.frame_bound[0] - env.window_size
    end = env.frame_bound[1]
    prices = env.df.loc[:, 'Low'].to_numpy()[start:end]
    # AI sees Close price, RSI, and SMA
    signal_features = env.df.loc[:, ['Close', 'RSI', 'SMA']].to_numpy()[start:end]
    return prices, signal_features

class LiveLearningEnv(StocksEnv):
    _process_data = feature_process

# --- 3. THE SELF-LEARNING AGENT ---
class OnlineLearner:
    def __init__(self):
        self.model_path = "live_ppo_model.zip"
        self.candle_manager = CandleManager()
        
        # Load or Create Model
        if os.path.exists(self.model_path):
            print(f"{Fore.GREEN}🧠 Loaded Existing Brain.{Style.RESET_ALL}")
            self.model = PPO.load(self.model_path)
        else:
            print(f"{Fore.YELLOW}⚠️ Creating New Brain...{Style.RESET_ALL}")
            # Initialize with dummy data structure
            dummy_df = pd.DataFrame(np.random.rand(100, 5), columns=['Open', 'High', 'Low', 'Close', 'Volume'])
            dummy_df['RSI'] = 50
            dummy_df['SMA'] = 100
            env = LiveLearningEnv(df=dummy_df, window_size=10, frame_bound=(10, 50))
            self.model = PPO("MlpPolicy", env, verbose=0)

        # Connect to Zerodha
        self.kite = KiteConnect(api_key=API_KEY)
        self.kite.set_access_token(ACCESS_TOKEN)
        self.kws = KiteTicker(API_KEY, ACCESS_TOKEN)

    def retrain_brain(self):
        """The Magic: Takes recent history and updates the Neural Network"""
        df = self.candle_manager.candles.copy()
        
        # We need at least 50 candles to learn anything useful
        if len(df) < 50: return 

        print(f"\n{Fore.MAGENTA}🧠 RE-TRAINING MODEL on last {len(df)} candles...{Style.RESET_ALL}")
        
        # Create a fresh environment with the NEW data
        # We only train on valid data (drop NaNs from indicators)
        clean_df = df.dropna()
        if clean_df.empty: return

        env = LiveLearningEnv(df=clean_df, window_size=10, frame_bound=(10, len(clean_df)))
        
        # Link model to new env and learn
        self.model.set_env(env)
        # Train for a short burst (e.g., 2000 steps) to adapt quickly
        self.model.learn(total_timesteps=2000) 
        
        self.model.save(self.model_path)
        print(f"{Fore.GREEN}✅ Model Updated & Saved!{Style.RESET_ALL}")

    def on_ticks(self, ws, ticks):
        for tick in ticks:
            if tick['instrument_token'] == TOKEN:
                self.candle_manager.add_tick(tick['last_price'])

# --- ADD THIS NEW METHOD ---
    def on_connect(self, ws, response):
        """Called automatically when the handshake is complete"""
        print(f"{Fore.GREEN}✅ WebSocket Connected! Subscribing to {TOKEN}...{Style.RESET_ALL}")
        self.kws.subscribe([TOKEN])
        self.kws.set_mode(self.kws.MODE_LTP, [TOKEN])

    # --- REPLACE YOUR RUN METHOD WITH THIS ---
    def run(self):
        # Assign the callbacks
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect  # <--- Link the new method here
        
        # Start the background thread
        self.kws.connect(threaded=True)
        
        print(f"{Fore.CYAN}🚀 Online Learning Bot Started on {SYMBOL}...{Style.RESET_ALL}")
        
        # We removed the manual subscribe() call from here because 
        # on_connect() handles it now.
        
        last_min = datetime.datetime.now().minute
        candles_since_train = 0

        # ... (The rest of your while loop remains exactly the same) ...
        while True:
            now_min = datetime.datetime.now().minute
            
            if now_min != last_min:
                last_min = now_min
                candle = self.candle_manager.close_candle()
                
                if candle is not None and len(self.candle_manager.candles) > 15:
                    # [Predict/Trade Logic remains here]
                    # ... (Keep your existing logic) ...
                    pass 
            
            time.sleep(1)

if __name__ == "__main__":
    bot = OnlineLearner()
    bot.run()