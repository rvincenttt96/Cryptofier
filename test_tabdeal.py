import requests

def test_tabdeal_public_api():
    print("🚀 Testing Tabdeal Public API (Binance Architecture)...")
    
    # اندپوینتِ لیست معاملات که در مستندات با دسترسی NONE معرفی شده است
    base_url = "https://api1.tabdeal.org/r/api/v1/trades"
    
    headers = {
        'User-Agent': 'TraderBot/AlgoV4_Test'
    }
    
    # بازارهایی که می‌خواهیم آخرین قیمتشان را چک کنیم
    symbols = ['BTC_IRT', 'USDT_IRT', 'BTC_USDT']
    prices = {}
    
    for sym in symbols:
        try:
            # استفاده از پارامتر tabdealSymbol و دریافت فقط 1 معامله آخر
            url = f"{base_url}?tabdealSymbol={sym}&limit=1"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    # قیمت آخرین معامله انجام شده را برمی‌داریم
                    prices[sym] = float(data[0]['price'])
                    print(f"✅ Fetched {sym} successfully.")
                else:
                    prices[sym] = 0.0
            else:
                print(f"❌ Blocked or Failed for {sym}. Status: {response.status_code}")
                prices[sym] = 0.0
                
        except Exception as e:
            print(f"❌ Connection Error on {sym}: {e}")
            prices[sym] = 0.0

    # چاپ و تحلیل دیتا
    print("-" * 40)
    print("📊 [ Extracted Data from Tabdeal ]")
    
    btc_irt = prices.get('BTC_IRT', 0)
    usdt_irt = prices.get('USDT_IRT', 0)
    btc_usdt_direct = prices.get('BTC_USDT', 0)
    
    print(f"BTC / IRT (Toman): {btc_irt:,.0f}")
    print(f"USDT / IRT (Toman): {usdt_irt:,.0f}")
    print(f"BTC / USDT (Direct Market): ${btc_usdt_direct:,.2f}")
    
    # محاسبه قیمت مصنوعی از روی تومان
    if usdt_irt > 0:
        synthetic_price = btc_irt / usdt_irt
        print(f"BTC / USDT (Calculated Synthetic): ${synthetic_price:,.2f}")
        
        diff = btc_usdt_direct - synthetic_price
        print(f"Internal Arbitrage Diff: ${abs(diff):.2f}")
        
    print("-" * 40)

if __name__ == "__main__":
    test_tabdeal_public_api()
