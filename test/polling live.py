import time
import os
from kiteconnect import KiteConnect
from tabulate import tabulate

# CONFIG
API_KEY =""
ACCESS_TOKEN =""

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

def get_live_positions():
    while True:
        try:
            # 1. Fetch data from Zerodha
            pos = kite.positions().get('day', [])
            
            # 2. Clear terminal for the 'Live' effect
            os.system('cls' if os.name == 'nt' else 'clear')
            
            print(f"--- 🕒 LIVE DASHBOARD (Refreshing every 1s) ---")
            print(f"Last Update: {time.strftime('%H:%M:%S')}\n")

            if not pos:
                print("   [ No active trades today ]")
            else:
                # 3. Filter and Format
                clean_p = []
                total_pnl = 0
                for p in pos:
                    pnl = p.get('pnl', 0)
                    total_pnl += pnl
                    clean_p.append({
                        "Symbol": p['tradingsymbol'],
                        "Qty": p['quantity'],
                        "Avg": p['average_price'],
                        "LTP": p['last_price'],
                        "PnL": f"₹{pnl:,.2f}"
                    })
                
                # 4. Draw Table
                print(tabulate(clean_p, headers="keys", tablefmt="grid"))
                print(f"\nTOTAL DAY PnL: ₹{total_pnl:,.2f}")
                print("\n(Press Ctrl+C to stop)")

            # 5. Wait for 5 seconds before next request
            time.sleep(1) 

        except KeyboardInterrupt:
            print("\nExiting Live View...")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10) # Wait longer if there's an error

if __name__ == "__main__":
    get_live_positions()