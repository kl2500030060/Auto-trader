import os
import sys
import time
from kiteconnect import KiteConnect
from tabulate import tabulate

# ================= CONFIGURATION =================
API_KEY =""
ACCESS_TOKEN =""
# =================================================

class ZerodhaDashboard:
    def __init__(self):
        try:
            self.kite = KiteConnect(api_key=API_KEY)
            self.kite.set_access_token(ACCESS_TOKEN)
            # Verify connection immediately
            profile = self.kite.profile()
            self.user_name = profile.get('user_name', 'User')
        except Exception as e:
            print(f"❌ Connection Error: {e}")
            sys.exit()

    def clear(self):
        # Clears terminal for a clean look
        os.system('cls' if os.name == 'nt' else 'clear')

    def draw_table(self, title, data):
        self.clear()
        print(f"\n{'='*25} {title} {'='*25}\n")
        if not data:
            print("   [ No Data Found ]")
        else:
            # tablefmt="grid" provides a structured boxed look
            print(tabulate(data, headers="keys", tablefmt="grid", numalign="center"))
        input("\nPress ENTER to return to menu...")

    def run(self):
        while True:
            self.clear()
            print(f"--- 👤 LOGGED IN AS: {self.user_name.upper()} ---")
            print("1. View Profile")
            print("2. Check Funds/Margins")
            print("3. View Portfolio (Holdings)")
            print("4. View Day Positions (Active Trades)")
            print("5. View Order Book (Recent 10)")
            print("0. Exit")
            
            choice = input("\nEnter choice (0-5): ")

            if choice == '1':
                p = self.kite.profile()
                data = [{"Name": p.get('user_name'), "ID": p.get('user_id'), "Email": p.get('email')}]
                self.draw_table("USER PROFILE", data)

            elif choice == '2':
                m = self.kite.margins()
                eq = m.get('equity', {})
                data = [{
                    "Segment": "Equity",
                    "Available Cash": eq.get('available', {}).get('cash', 0),
                    "Used": eq.get('utilised', {}).get('debits', 0),
                    "Net Power": eq.get('net', 0)
                }]
                self.draw_table("ACCOUNT FUNDS", data)

            elif choice == '3':
                holdings = self.kite.holdings()
                clean_h = [{
                    "Symbol": h.get('tradingsymbol'),
                    "Qty": h.get('quantity'),
                    "Avg Price": h.get('average_price'),
                    "PnL": h.get('pnl')
                } for h in holdings]
                self.draw_table("PORTFOLIO HOLDINGS", clean_h)

            elif choice == '4':
                pos = self.kite.positions().get('day', [])
                clean_p = [{
                    "Symbol": p.get('tradingsymbol'),
                    "Qty": p.get('quantity'),
                    "LTP": p.get('last_price'),
                    "PnL": f"₹{p.get('pnl', 0):.2f}",
                    "Type": p.get('product')
                } for p in pos]
                self.draw_table("DAY POSITIONS", clean_p)

            elif choice == '5':
                orders = self.kite.orders()
                clean_o = []
                # FIX: Added 'or ""' to prevent NoneType slicing error
                for o in orders[-10:]:
                    raw_msg = o.get('status_message') or ""
                    clean_o.append({
                        "Time": o.get('order_timestamp'),
                        "Symbol": o.get('tradingsymbol'),
                        "Type": o.get('transaction_type'),
                        "Status": o.get('status'),
                        "Message": raw_msg[:30] 
                    })
                self.draw_table("RECENT ORDERS", clean_o)

            elif choice == '0':
                print("\nExiting Dashboard...")
                break
            else:
                print("\nInvalid choice. Please select 0-5.")
                time.sleep(1)

if __name__ == "__main__":
    app = ZerodhaDashboard()
    app.run()