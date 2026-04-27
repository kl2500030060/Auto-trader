from kiteconnect import KiteConnect

# 1. Enter your details here
api_key =""
api_secret =""
request_token = ""

# 2. Generate Session
kite = KiteConnect(api_key=api_key)
data = kite.generate_session(request_token, api_secret=api_secret)
print("------------------------------------------")
print("YOUR ACCESS TOKEN IS:")
print(data["access_token"])
print("------------------------------------------")