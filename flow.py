import datetime
import sqlite3
import time
from tvDatafeed import Interval, TvDatafeed

DB_NAME = "crypto_flow.db"

# Extracted weights from analyzing 2204 recent candles
W_TOTAL = 0.3521
W_TOTAL2 = 0.1245
W_BTCD = 0.1299
W_USDTD = -0.3935

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_flow_5m (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            btc_price REAL NOT NULL,
            total_mcap REAL NOT NULL,
            total2_mcap REAL NOT NULL,
            btc_d REAL NOT NULL,
            usdt_d REAL NOT NULL,
            delta_total REAL DEFAULT 0,
            delta_total2 REAL DEFAULT 0,
            delta_btc_d REAL DEFAULT 0,
            delta_usdt_d REAL DEFAULT 0,
            flow_destination TEXT,
            flow_score REAL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

class CryptoFlowCollector:
    def __init__(self):
        init_db()

    def fetch_market_data(self, max_retries=3):
        for attempt in range(1, max_retries + 1):
            try:
                tv = TvDatafeed()
                btc_df = tv.get_hist("BTCUSDT", "BINANCE", Interval.in_5_minute, n_bars=1)
                total_df = tv.get_hist("TOTAL", "CRYPTOCAP", Interval.in_5_minute, n_bars=1)
                total2_df = tv.get_hist("TOTAL2", "CRYPTOCAP", Interval.in_5_minute, n_bars=1)
                btcd_df = tv.get_hist("BTC.D", "CRYPTOCAP", Interval.in_5_minute, n_bars=1)
                usdtd_df = tv.get_hist("USDT.D", "CRYPTOCAP", Interval.in_5_minute, n_bars=1)

                if any(df is None for df in [btc_df, total_df, total2_df, btcd_df, usdtd_df]):
                    time.sleep(2)
                    continue

                return {
                    "btc_price": float(btc_df["close"].iloc[-1]),
                    "total": float(total_df["close"].iloc[-1]),
                    "total2": float(total2_df["close"].iloc[-1]),
                    "btc_d": float(btcd_df["close"].iloc[-1]),
                    "usdt_d": float(usdtd_df["close"].iloc[-1]),
                }
            except Exception:
                time.sleep(2)
        return None

    def get_last_record(self):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_mcap, total2_mcap, btc_d, usdt_d FROM market_flow_5m ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"total": row[0], "total2": row[1], "btc_d": row[2], "usdt_d": row[3]}
        return None

    def calculate_flow_score(self, deltas):
        """Calculate liquidity score based on extracted mathematical weights"""
        # Normalize deltas for scale coordination
        norm_total = deltas["delta_total"] / 1e9
        norm_total2 = deltas["delta_total2"] / 1e9
        norm_btcd = deltas["delta_btc_d"]
        norm_usdtd = deltas["delta_usdt_d"]

        raw_score = (
            (norm_total * W_TOTAL)
            + (norm_total2 * W_TOTAL2)
            + (norm_btcd * W_BTCD)
            + (norm_usdtd * W_USDTD)
        )
        return round(raw_score * 10, 4)

    def analyze_flow(self, current, prev):
        if not prev:
            return "INITIALIZING_BASE_CANDLE"
        delta_total = current["total"] - prev["total"]
        delta_btc_d = current["btc_d"] - prev["btc_d"]
        delta_usdt_d = current["usdt_d"] - prev["usdt_d"]

        if delta_usdt_d < 0 and delta_total > 0:
            return "INFLOW -> BTC FOCUS" if delta_btc_d > 0 else "INFLOW -> ALTSEASON FLOW"
        elif delta_usdt_d > 0 and delta_total < 0:
            return "OUTFLOW -> ALT BLEEDING (FLIGHT TO BTC/USDT)" if delta_btc_d > 0 else "OUTFLOW -> MARKET SELL-OFF (FLIGHT TO USDT)"
        return "INTERNAL ROTATION / RANGING"

    def process_candle(self):
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        current_data = self.fetch_market_data()

        if not current_data:
            print(f"[{now_str}] Connection failed. Skipping candle.")
            return

        prev_data = self.get_last_record()
        if prev_data:
            deltas = {
                "delta_total": current_data["total"] - prev_data["total"],
                "delta_total2": current_data["total2"] - prev_data["total2"],
                "delta_btc_d": current_data["btc_d"] - prev_data["btc_d"],
                "delta_usdt_d": current_data["usdt_d"] - prev_data["usdt_d"],
            }
        else:
            deltas = {"delta_total": 0.0, "delta_total2": 0.0, "delta_btc_d": 0.0, "delta_usdt_d": 0.0}

        flow_dest = self.analyze_flow(current_data, prev_data)
        flow_score = self.calculate_flow_score(deltas)

        # Save to SQLite
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO market_flow_5m (
                timestamp, btc_price, total_mcap, total2_mcap, btc_d, usdt_d,
                delta_total, delta_total2, delta_btc_d, delta_usdt_d, flow_destination, flow_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_str, current_data["btc_price"], current_data["total"], current_data["total2"],
            current_data["btc_d"], current_data["usdt_d"], deltas["delta_total"],
            deltas["delta_total2"], deltas["delta_btc_d"], deltas["delta_usdt_d"],
            flow_dest, flow_score
        ))
        conn.commit()
        conn.close()

        print(f"\n==================== [{now_str}] ====================")
        print(f"BTC Price:       ${current_data['btc_price']:,.2f}")
        print(f"Total MCAP:      ${current_data['total']/1e9:.3f} B  (Delta: {deltas['delta_total']/1e6:+.2f} M)")
        print(f"Total2 MCAP:     ${current_data['total2']/1e9:.3f} B  (Delta: {deltas['delta_total2']/1e6:+.2f} M)")
        print(f"BTC Dominance:   {current_data['btc_d']:.2f}%       (Delta: {deltas['delta_btc_d']:+.3f}%)")
        print(f"USDT Dominance:  {current_data['usdt_d']:.2f}%       (Delta: {deltas['delta_usdt_d']:+.3f}%)")
        print(f"Flow Status:     {flow_dest}")
        print(f"Flow Score:      {flow_score:+.4f}")
        print("==========================================================")

    def run(self, interval_seconds=300):
        print("=== Crypto Flow Tracker with Formula Integration ===")
        while True:
            self.process_candle()
            time.sleep(interval_seconds)

if __name__ == "__main__":
    collector = CryptoFlowCollector()
    collector.run()