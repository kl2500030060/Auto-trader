import time, os
import pandas as pd
import pandas_ta_classic as ta
from kiteconnect import KiteConnect
from tabulate import tabulate

# CONFIG
API_KEY =""
ACCESS_TOKEN =""
SYMBOL = "NSE:SBIN"  # Example Ticker

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

class AdvancedScalper:
    def __init__(self, symbol):
        self.symbol = symbol
        self.instrument_token = 779521 # Example token for SBIN

    def fetch_and_analyze(self):
        # Fetch 1-minute data for scalping (Requires Historical API)
        # If no Historical API, you'd collect 'ticks' manually into a list
        data = kite.historical_data(self.instrument_token, "2026-01-01", "2026-01-07", "minute")
        df = pd.DataFrame(data)

        # 1. EMA Crossover (9 vs 21)
        df['EMA_9'] = ta.ema(df['close'], length=9)
        df['EMA_21'] = ta.ema(df['close'], length=21)

        # 2. RSI (14)
        df['RSI'] = ta.rsi(df['close'], length=14)

        # 3. MACD
        macd = ta.macd(df['close'])
        df = pd.concat([df, macd], axis=1)

        # 4. Bollinger Bands
        bbands = ta.bbands(df['close'], length=20)
        df = pd.concat([df, bbands], axis=1)

        # 5. Heikin Ashi
        ha = ta.ha(df['open'], df['high'], df['low'], df['close'])
        df = pd.concat([df, ha], axis=1)

        return df.iloc[-1], df.iloc[-2] # Current and Previous candle

    def get_status(self, curr, prev):
        # Logic Definitions
        logics = [
            ["Indicator", "Value", "Condition", "Status"],
            ["EMA 9/21", f"{curr['EMA_9']:.1f}/{curr['EMA_21']:.1f}", "9 > 21", "✅ BULLISH" if curr['EMA_9'] > curr['EMA_21'] else "❌ BEARISH"],
            ["RSI (14)", f"{curr['RSI']:.1f}", "35 < RSI < 65", "✅ NEUTRAL" if 35 < curr['RSI'] < 65 else "⚠️ EXTREME"],
            ["MACD", f"{curr['MACDh_12_26_9']:.2f}", "Hist > 0", "✅ POSITIVE" if curr['MACDh_12_26_9'] > 0 else "❌ NEGATIVE"],
            ["Heikin Ashi", "Green" if curr['HA_close'] > curr['HA_open'] else "Red", "Color Change", "✅ REVERSAL" if (curr['HA_close'] > curr['HA_open'] and prev['HA_close'] < prev['HA_open']) else "⚪ NO CHANGE"],
            ["BBands", "Inside", "Price < Lower", "✅ OVERSOLD" if curr['close'] < curr['BBL_20_2.0'] else "⚪ STABLE"]
        ]
        return logics

    def live_dashboard(self):
        while True:
            try:
                os.system('cls' if os.name == 'nt' else 'clear')
                curr, prev = self.fetch_and_analyze()
                table = self.get_status(curr, prev)
                
                print(f"--- 🤖 ALGO STRATEGY MONITOR: {self.symbol} ---")
                print(f"LTP: ₹{curr['close']} | Time: {time.strftime('%H:%M:%S')}\n")
                print(tabulate(table, headers="firstrow", tablefmt="grid"))
                
                # TRADE SIGNAL TRIGGER
                bullish_count = sum(1 for row in table if "✅" in str(row[3]))
                print(f"\nSignal Strength: {bullish_count}/5 Indicators Matched")
                
                if bullish_count >= 4:
                    print("🚀 CRITICAL BUY SIGNAL DETECTED!")
                    # self.kite.place_order(...)

                print("\n(Press Ctrl+C to Exit)")
                time.sleep(3) # Polling interval
            except KeyboardInterrupt:
                break

if __name__ == "__main__":
    bot = AdvancedScalper(SYMBOL)
    bot.live_dashboard()