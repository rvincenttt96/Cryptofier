import sqlite3
import pandas as pd
import plotly.graph_objects as go
import requests
import schedule
import time
import datetime
import os
import logging

# --- Configurations ---
TOKEN = "8980028033:AAHG1EAI0zzR-AwFm26vtGdE7tAEqMmNPWI"
CHAT_ID = "-1004494438997"
DB_NAME = "crypto_flow.db"

# --- Logging Setup ---
logging.basicConfig(
    filename='system_run.log',
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
    return f"${value:.2f}"

def send_startup_alert():
    """Send a quick test message to ensure Telegram API is reachable."""
    logging.info("Sending startup alert to Telegram...")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": "✅ <b>System Initialized</b>\nVPS Connected. Database and Telegram Reporter are active and running.",
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logging.info("Startup alert sent successfully.")
        else:
            logging.error(f"Failed to send startup alert: {response.text}")
    except Exception as e:
        logging.error(f"Connection error on startup: {e}")

def generate_and_send_report():
    logging.info("Generating hourly money flow report...")
    
    try:
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql_query("SELECT * FROM market_flow_5m ORDER BY id DESC LIMIT 12", conn)
        conn.close()

        if len(df) < 12:
            logging.warning("Not enough data for a 1-hour report yet. Skipping.")
            return

        df = df.iloc[::-1]

        delta_total = df['delta_total'].sum()
        delta_total2 = df['delta_total2'].sum()
        delta_btc_mcap = delta_total - delta_total2
        delta_btcd = df['delta_btc_d'].sum()
        delta_usdtd = df['delta_usdt_d'].sum()
        current_btc_price = df['btc_price'].iloc[-1]
        
        labels = ["Fiat / USDT Reserves", "Total Crypto Market", "Bitcoin (BTC)", "Altcoins"]
        sources, targets, values, colors = [], [], [], []

        if delta_total > 0:
            sources.append(0)
            targets.append(1)
            values.append(abs(delta_total))
            colors.append("rgba(39, 174, 96, 0.6)")
            if delta_btc_mcap > 0:
                sources.extend([1])
                targets.extend([2])
                values.extend([abs(delta_btc_mcap)])
                colors.extend(["rgba(243, 156, 18, 0.6)"])
            if delta_total2 > 0:
                sources.extend([1])
                targets.extend([3])
                values.extend([abs(delta_total2)])
                colors.extend(["rgba(41, 128, 185, 0.6)"])
        else:
            sources.append(1)
            targets.append(0)
            values.append(abs(delta_total))
            colors.append("rgba(192, 57, 43, 0.6)")
            if delta_btc_mcap < 0:
                sources.extend([2])
                targets.extend([1])
                values.extend([abs(delta_btc_mcap)])
                colors.extend(["rgba(243, 156, 18, 0.6)"])
            if delta_total2 < 0:
                sources.extend([3])
                targets.extend([1])
                values.extend([abs(delta_total2)])
                colors.extend(["rgba(41, 128, 185, 0.6)"])

        fig = go.Figure(data=[go.Sankey(
            node = dict(pad=25, thickness=30, line=dict(color="black", width=0.5), label=labels, color=["#95a5a6", "#34495e", "#f39c12", "#3498db"]),
            link = dict(source=sources, target=targets, value=values, color=colors)
        )])

        fig.update_layout(
            title_text=f"Hourly Money Flow Breakdown<br><sup>Total Volume Flow: {format_currency(abs(delta_total))}</sup>",
            font=dict(size=12, color="white"),
            paper_bgcolor="#1e1e1e",
            plot_bgcolor="#1e1e1e"
        )

        image_path = "sankey_flow.png"
        fig.write_image(image_path, width=800, height=500)

        flow_status = "🟢 INFLOW" if delta_total > 0 else "🔴 OUTFLOW"
        caption = (
            f"📊 <b>Hourly Money Flow Report</b>\n\n"
            f"💰 <b>BTC Price:</b> ${current_btc_price:,.2f}\n"
            f"🌊 <b>Market Status:</b> {flow_status}\n\n"
            f"<b>[ 1H Deltas ]</b>\n"
            f"• <b>TOTAL MCAP:</b> {format_currency(delta_total)}\n"
            f"• <b>BTC.D Change:</b> {delta_btcd:+.3f}%\n"
            f"• <b>USDT.D Change:</b> {delta_usdtd:+.3f}%\n\n"
            f"<b>[ Divergence Insights ]</b>\n"
        )

        if delta_btcd > 0 and delta_usdtd < 0:
            caption += "<i>Smart money is moving from Stablecoins directly into Bitcoin. Strong BTC Focus.</i>"
        elif delta_btcd < 0 and delta_usdtd < 0:
            caption += "<i>Stablecoins are being deployed, but flowing heavily into Altcoins (Altseason Setup).</i>"
        elif delta_btcd > 0 and delta_usdtd > 0:
            caption += "<i>Market is bleeding. Altcoins are being dumped for BTC and USDT faster than BTC is dumped.</i>"
        else:
            caption += "<i>General Market Sell-off. Assets converting to fiat/stablecoins.</i>"

        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            payload = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            response = requests.post(url, data=payload, files={"photo": photo})
        
        if response.status_code == 200:
            logging.info("Report successfully sent to Telegram channel.")
        else:
            logging.error(f"Failed to send to Telegram: {response.text}")

        if os.path.exists(image_path):
            os.remove(image_path)

    except Exception as e:
        logging.critical(f"Critical Error in generating report: {e}")

# --- Scheduler ---
logging.info("=== Telegram Money Flow Reporter Started ===")
send_startup_alert()

# Schedule to run at minute 00 of every hour
schedule.every().hour.at(":00").do(generate_and_send_report)

while True:
    schedule.run_pending()
    time.sleep(10)