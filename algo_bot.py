import sqlite3
import pandas as pd
import plotly.graph_objects as go
import requests
import time
import datetime
import os
import logging
from tvDatafeed import TvDatafeed, Interval

# --- Configurations ---
TOKEN = "8980028033:AAHG1EAI0zzR-AwFm26vtGdE7tAEqMmNPWI"
CHAT_ID = "-1004494438997"
DB_NAME = "crypto_flow.db"
STARTING_BALANCE = 500.0
LAG_THRESHOLD = 300.0   # Updated: Covers 0.26% Round-trip Fee + Profit Margin
FEE_RATE = 0.0013       # 0.13% per trade side

# VIP API Keys
WALLEX_API_KEY = "20263|s3qszKCNr6q24RSoE5t6SKGwB2ixG5gF5ZFcq9l9"

# --- Logging Setup ---
logging.basicConfig(
    filename='algo_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

def format_currency(value):
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    elif abs(value) >= 1e6:
        return f"${value / 1e6:.2f}M"
    return f"${value:,.2f}"

def get_market_prices(tv_client):
    prices = {'global': 0.0, 'binance': 0.0, 'nobitex': 0.0, 'wallex': 0.0, 'nobi_failed': False, 'wall_failed': False}
    
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=5).json()
        prices['binance'] = float(res['price'])
    except: pass

    try:
        wall_headers = {'User-Agent': 'TraderBot/AlgoV4', 'X-API-Key': WALLEX_API_KEY}
        w_res = requests.get("https://api.wallex.ir/v1/markets", headers=wall_headers, timeout=8)
        if w_res.status_code == 200:
            data = w_res.json()
            btc_tmn = float(data['result']['symbols']['BTCTMN']['stats']['lastPrice'])
            usdt_tmn = float(data['result']['symbols']['USDTTMN']['stats']['lastPrice'])
            if usdt_tmn > 0: prices['wallex'] = btc_tmn / usdt_tmn
        else:
            prices['wall_failed'] = True
    except:
        prices['wall_failed'] = True

    try:
        nobi_headers = {'User-Agent': 'TraderBot/AlgoV4'}
        n_res = requests.get("https://apiv2.nobitex.ir/market/stats?dstCurrency=rls", headers=nobi_headers, timeout=8)
        if n_res.status_code == 200:
            data = n_res.json()
            if data.get("status") == "ok":
                btc_rls = float(data["stats"]["btc-rls"]["latest"])
                usdt_rls = float(data["stats"]["usdt-rls"]["latest"])
                if usdt_rls > 0: prices['nobitex'] = btc_rls / usdt_rls
            else:
                prices['nobi_failed'] = True
        else:
            prices['nobi_failed'] = True
    except:
        prices['nobi_failed'] = True

    try:
        df = tv_client.get_hist("BTCUSD", "INDEX", Interval.in_1_minute, n_bars=1)
        if df is not None and not df.empty:
            prices['global'] = float(df['close'].iloc[-1])
        else:
            prices['global'] = prices['binance'] 
    except:
        prices['global'] = prices['binance']

    if prices['nobitex'] == 0: prices['nobitex'] = prices['global']
    if prices['wallex'] == 0: prices['wallex'] = prices['global']

    return prices

def evaluate_paper_trade(prices, flow_status, latest_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # --- DATA SCIENCE RECORDING ---
    wall_diff = prices['global'] - prices['wallex'] if not prices['wall_failed'] else 0.0
    nobi_diff = prices['global'] - prices['nobitex'] if not prices['nobi_failed'] else 0.0
    
    cursor.execute("""
        UPDATE market_flow_5m 
        SET binance_price=?, nobitex_price=?, wallex_price=?, nobi_lag=?, wall_lag=? 
        WHERE id=?
    """, (prices['binance'], prices['nobitex'], prices['wallex'], nobi_diff, wall_diff, latest_id))
    # ------------------------------

    cursor.execute("SELECT balance FROM paper_wallet WHERE id=1")
    balance = cursor.fetchone()[0]
    
    cursor.execute("SELECT id, exchange, entry_price, amount_btc, entry_fee, invested_usd FROM paper_trades WHERE status='OPEN'")
    open_trade = cursor.fetchone()
    
    event_alert = None
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not open_trade:
        if "INFLOW" in flow_status:
            best_diff = 0.0
            best_exchange = None
            best_price = 0.0
            
            if wall_diff >= LAG_THRESHOLD and wall_diff >= nobi_diff and prices['wallex'] != prices['global']:
                best_diff = wall_diff
                best_exchange = "Wallex"
                best_price = prices['wallex']
            elif nobi_diff >= LAG_THRESHOLD and nobi_diff > wall_diff and prices['nobitex'] != prices['global']:
                best_diff = nobi_diff
                best_exchange = "Nobitex"
                best_price = prices['nobitex']

            if best_exchange and balance > 0:
                invested_usd = balance
                entry_fee = invested_usd * FEE_RATE
                amount_btc = (invested_usd - entry_fee) / best_price
                
                cursor.execute("""
                    INSERT INTO paper_trades (status, entry_time, exchange, entry_price, amount_btc, entry_fee, invested_usd)
                    VALUES ('OPEN', ?, ?, ?, ?, ?, ?)
                """, (now_str, best_exchange, best_price, amount_btc, entry_fee, invested_usd))
                cursor.execute("UPDATE paper_wallet SET balance=0 WHERE id=1")
                event_alert = f"<b>[ TRADE OPENED (LONG) ]</b>\nExchange: {best_exchange}\nEntry Price: ${best_price:,.2f}\nInvested: ${invested_usd:,.2f}\nEntry Fee: ${entry_fee:,.2f} ({FEE_RATE*100}%)\nReason: Positive Netflow + ${best_diff:.0f} Lag"
                logging.info(f"OPENED TRADE on {best_exchange} at {best_price}")

    else:
        trade_id, exchange, entry_price, amount_btc, entry_fee, invested_usd = open_trade
        current_price = prices['wallex'] if exchange == "Wallex" else prices['nobitex']
        is_blocked = prices['wall_failed'] if exchange == "Wallex" else prices['nobi_failed']
        
        if not is_blocked:
            current_lag = prices['global'] - current_price
            
            # --- The Patient Sniper: 2-Candle Confirmation Rule ---
            cursor.execute("SELECT flow_destination FROM market_flow_5m ORDER BY id DESC LIMIT 2")
            last_2_flows = [r[0] for r in cursor.fetchall()]
            confirmed_outflow = len(last_2_flows) == 2 and all("OUTFLOW" in str(f) for f in last_2_flows)
            
            if confirmed_outflow or current_lag <= 0:
                exit_gross = amount_btc * current_price
                exit_fee = exit_gross * FEE_RATE
                exit_net = exit_gross - exit_fee
                
                net_pnl = exit_net - invested_usd
                gross_pnl = exit_gross - (amount_btc * entry_price)
                
                cursor.execute("""
                    UPDATE paper_trades 
                    SET status='CLOSED', exit_time=?, exit_price=?, pnl_usd=?, exit_fee=?, net_pnl_usd=? 
                    WHERE id=?
                """, (now_str, current_price, gross_pnl, exit_fee, net_pnl, trade_id))
                cursor.execute("UPDATE paper_wallet SET balance=? WHERE id=1", (exit_net,))
                
                reason = "Confirmed Negative Netflow (2 Candles)" if confirmed_outflow else "Lag Neutralized (Hit Fair Value)"
                event_alert = (
                    f"<b>[ TRADE CLOSED ]</b>\n"
                    f"Exchange: {exchange}\n"
                    f"Exit Price: ${current_price:,.2f}\n"
                    f"Gross PnL: ${gross_pnl:+.2f}\n"
                    f"Total Fees: -${(entry_fee + exit_fee):.2f}\n"
                    f"<b>Net PnL: ${net_pnl:+.2f}</b>\n"
                    f"Reason: {reason}"
                )
                logging.info(f"CLOSED TRADE on {exchange}: Net PnL {net_pnl}")

    conn.commit()
    conn.close()
    return event_alert

def generate_and_send_report(row, prices, event_alert=None):
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT * FROM market_flow_5m ORDER BY id DESC LIMIT 12", conn)
        
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM paper_wallet WHERE id=1")
        wallet_balance = cursor.fetchone()[0]
        
        cursor.execute("SELECT exchange, entry_price, amount_btc, entry_fee, invested_usd FROM paper_trades WHERE status='OPEN'")
        open_trade = cursor.fetchone()
        
        # Calculate Historic Net PnL (Fallback to gross if old trades didn't have net_pnl)
        cursor.execute("SELECT SUM(COALESCE(NULLIF(net_pnl_usd, 0), pnl_usd)) FROM paper_trades WHERE status='CLOSED'")
        total_pnl = cursor.fetchone()[0] or 0.0
        conn.close()

        if len(df) < 12: return
        df = df.iloc[::-1]

        delta_total = df['delta_total'].sum()
        delta_total2 = df['delta_total2'].sum()
        delta_btc_mcap = delta_total - delta_total2
        delta_btcd = df['delta_btc_d'].sum()
        delta_usdtd = df['delta_usdt_d'].sum()
        
        latest_row = df.iloc[-1]
        current_btc_price = latest_row['btc_price']
        current_total = latest_row['total_mcap'] / 1e9
        current_total2 = latest_row['total2_mcap'] / 1e9
        current_btcd = latest_row['btc_d']
        current_usdtd = latest_row['usdt_d']
        
        labels = ["Fiat / USDT", "Total Crypto Market", "Bitcoin (BTC)", "Altcoins"]
        sources, targets, values, colors = [], [], [], []

        plot_delta_total = abs(delta_total) if abs(delta_total) > 1 else 1 
        plot_delta_btc = abs(delta_btc_mcap) if abs(delta_btc_mcap) > 1 else 1
        plot_delta_total2 = abs(delta_total2) if abs(delta_total2) > 1 else 1

        if delta_total > 0:
            sources.extend([0]); targets.extend([1]); values.extend([plot_delta_total]); colors.extend(["rgba(39, 174, 96, 0.6)"])
            if delta_btc_mcap > 0:
                sources.append(1); targets.append(2); values.append(plot_delta_btc); colors.append("rgba(243, 156, 18, 0.6)")
            if delta_total2 > 0:
                sources.append(1); targets.append(3); values.append(plot_delta_total2); colors.append("rgba(41, 128, 185, 0.6)")
        else:
            sources.extend([1]); targets.extend([0]); values.extend([plot_delta_total]); colors.extend(["rgba(192, 57, 43, 0.6)"])
            if delta_btc_mcap < 0:
                sources.append(2); targets.append(1); values.append(plot_delta_btc); colors.append("rgba(243, 156, 18, 0.6)")
            if delta_total2 < 0:
                sources.append(3); targets.append(1); values.append(plot_delta_total2); colors.append("rgba(41, 128, 185, 0.6)")

        if not sources: sources, targets, values, colors = [0], [1], [1], ["rgba(149, 165, 166, 0.2)"]

        fig = go.Figure(data=[go.Sankey(node=dict(pad=25, thickness=30, label=labels, color=["#95a5a6", "#34495e", "#f39c12", "#3498db"]), link=dict(source=sources, target=targets, value=values, color=colors))])
        fig.update_layout(title_text=f"Money Flow Breakdown | Vol: {format_currency(abs(delta_total))}", font=dict(size=12, color="white"), paper_bgcolor="#1e1e1e", plot_bgcolor="#1e1e1e")
        
        image_path = "dashboard.png"
        fig.write_image(image_path, width=800, height=450)

        flow_status_display = "Positive Netflow" if delta_total > 0 else "Negative Netflow"
        
        caption = ""
        if event_alert: caption += f"{event_alert}\n\n"

        caption += (
            f"<b>--- Hourly Money Flow Report ---</b>\n\n"
            f"<b>BTC Price:</b> ${current_btc_price:,.2f}\n"
            f"<b>Market Status:</b> {flow_status_display}\n\n"
            f"<code>[ Market Metrics & 1H Deltas ]\n"
            f"TOTAL MCAP:  ${current_total:,.2f}B ({delta_total/1e6:+.2f} M)\n"
            f"TOTAL2 MCAP: ${current_total2:,.2f}B ({delta_total2/1e6:+.2f} M)\n"
            f"BTC.D:       {current_btcd:.2f}% ({delta_btcd:+.3f}%)\n"
            f"USDT.D:      {current_usdtd:.2f}% ({delta_usdtd:+.3f}%)</code>\n\n"
            f"<b>[ Divergence Insights ]</b>\n"
        )

        if delta_btcd > 0 and delta_usdtd < 0:
            caption += "<i>Smart money is moving from Stablecoins directly into Bitcoin. Strong BTC Focus.</i>\n\n"
        elif delta_btcd < 0 and delta_usdtd < 0:
            caption += "<i>Stablecoins are being deployed, but flowing heavily into Altcoins (Altseason Setup).</i>\n\n"
        elif delta_btcd > 0 and delta_usdtd > 0:
            caption += "<i>Market is bleeding. Altcoins are being dumped for BTC and USDT faster than BTC is dumped.</i>\n\n"
        else:
            caption += "<i>General Market Sell-off. Assets converting to fiat/stablecoins.</i>\n\n"

        def get_diff_str(g_price, e_price, is_blocked):
            if is_blocked: return "<code>[ API Blocked ]</code>"
            diff = g_price - e_price
            if diff >= LAG_THRESHOLD: return f"<code>[ Lag: -${diff:.0f} ]</code> <b>[ Buy Zone ]</b>"
            elif diff > 0: return f"<code>[ Lag: -${diff:.0f} ]</code>"
            else: return f"<code>[ Prem: +${abs(diff):.0f} ]</code> <b>[ Sell Zone ]</b>"

        caption += (
            f"<b>[ Exchange Premium Board ]</b>\n"
            f"Global Index: ${prices['global']:,.2f}\n"
            f"Binance: ${prices['binance']:,.2f} {get_diff_str(prices['global'], prices['binance'], False)}\n"
            f"Nobitex: ${prices['nobitex']:,.2f} {get_diff_str(prices['global'], prices['nobitex'], prices.get('nobi_failed', False))}\n"
            f"Wallex: ${prices['wallex']:,.2f} {get_diff_str(prices['global'], prices['wallex'], prices.get('wall_failed', False))}\n\n"
        )
        
        caption += f"<b>[ Paper Trading Status ]</b>\n"
        if open_trade:
            exchange_name, entry_price, amount_btc, entry_fee, invested_usd = open_trade
            live_price = prices['wallex'] if exchange_name == "Wallex" else prices['nobitex']
            is_trade_blocked = prices.get('wall_failed', False) if exchange_name == "Wallex" else prices.get('nobi_failed', False)
            
            if is_trade_blocked:
                caption += f"Open Position: LONG on {exchange_name}\nLive Net PnL: <b>[ Awaiting API ]</b>\n"
            else:
                live_gross = amount_btc * live_price
                live_exit_fee = live_gross * FEE_RATE
                live_net = live_gross - live_exit_fee
                live_net_pnl = live_net - invested_usd
                caption += f"Open Position: LONG on {exchange_name}\nLive Net PnL: <b>${live_net_pnl:+.2f}</b>\n"
        else:
            caption += f"Open Position: None\nAvailable Balance: ${wallet_balance:,.2f}\n"
        caption += f"Total Historic Net PnL: ${total_pnl:+.2f}\n"

        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            requests.post(url, data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": photo})
        
        if os.path.exists(image_path): os.remove(image_path)

    except Exception as e:
        logging.critical(f"Error generating report: {e}")

# --- Main Polling Loop ---
if __name__ == "__main__":
    tv_global_client = TvDatafeed()
    
    logging.info("=== Algo Bot Started ===")
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data={"chat_id": CHAT_ID, "text": "<b>[ Algo Bot Initialized ]</b>\nEngine: V4 Patient Sniper\nUI Mode: Transparent Fee Accounting", "parse_mode": "HTML"})

    last_processed_id = 0
    last_report_time = time.time()

    conn = sqlite3.connect(DB_NAME)
    res = conn.execute("SELECT MAX(id) FROM market_flow_5m").fetchone()[0]
    if res: last_processed_id = res
    conn.close()

    while True:
        try:
            conn = sqlite3.connect(DB_NAME)
            df = pd.read_sql_query("SELECT * FROM market_flow_5m ORDER BY id DESC LIMIT 1", conn)
            conn.close()
            
            if not df.empty:
                latest_row = df.iloc[0]
                latest_id = latest_row['id']
                
                if latest_id > last_processed_id:
                    flow_status = latest_row['flow_destination']
                    prices = get_market_prices(tv_global_client)
                    
                    event_msg = evaluate_paper_trade(prices, flow_status, latest_id)
                    
                    if event_msg:
                        generate_and_send_report(latest_row, prices, event_msg)
                        last_report_time = time.time() 
                    
                    last_processed_id = latest_id
            
            if time.time() - last_report_time > 1200:
                prices = get_market_prices(tv_global_client)
                generate_and_send_report(latest_row, prices)
                last_report_time = time.time()
                    
        except Exception as e:
            logging.error(f"Main loop error: {e}")
            
        time.sleep(10)
