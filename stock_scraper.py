import os
import json
import time
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 100_000_000
MAX_MARKET_CAP = 1_500_000_000

THEME_KEYWORDS = {
    "AI_DataCenter": ["data center", "liquid cooling", "hbm", "optical interconnect"],
    "Power_Infrastructure": ["electrical grid", "transformer", "substation", "power distribution"],
    "Semiconductor_Equipment": ["wafer", "lithography", "etching", "semiconductor packaging"],
    "Telecom": ["5g", "6g", "telecommunication", "fiber optic"],
    "Space": ["satellite", "low earth orbit", "aerospace", "payload"],
    "Defense": ["drone", "electronic warfare", "hypersonic", "missile", "defense contract"],
    "Energy_Security": ["lng", "natural gas", "grid resilience", "energy storage"],
    "SMR": ["small modular reactor", "nuclear", "reactor", "fission"],
    "Uranium": ["uranium", "u3o8", "yellowcake"],
    "Rare_Metal": ["rare earth", "critical mineral", "lithium", "neodymium"],
    "Quantum": ["quantum computing", "qubit", "quantum cryptography"],
    "BTC_System": ["bitcoin", "crypto mining", "hashrate", "asic"]
}

def get_or_create_sheet():
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    creds = Credentials.from_service_account_info(
        json.loads(secret_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        return sh.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows="2000", cols="6")
        # ご指定の縦型項目順にヘッダーを設定
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        return ws

def main():
    # 📝 本番ではここに全米上場ティッカーの配列を流し込みます
    # 現在はテスト稼働用に中小型株のサンプルで動かします
    tickers_to_scan = ["AEHR", "ATOM", "SKYT", "QUIK", "NVTS", "UUUU", "NXE", "RGTI", "OKLO", "SMR"]
    
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")
    
    for ticker in tickers_to_scan:
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 1. 時価総額フィルター
            market_cap = info.get("marketCap", 0)
            if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                continue
                
            summary = info.get("longBusinessSummary", "").lower()
            if not summary:
                continue
            
            # 2. テーマ判定
            matched_theme = None
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary for kw in keywords):
                    matched_theme = theme
                    break
            
            # 3. 縦型配置データ構造へ格納
            if matched_theme:
                discovered_gems.append([
                    matched_theme,                       # A: 分野
                    ticker.upper(),                      # B: ティッカー
                    info.get("longName", ticker),        # C: 企業名
                    round(market_cap / 1_000_000, 2),    # D: 時価総額($M)
                    info.get("longBusinessSummary", ""), # E: プロフィール(全文)
                    current_date                         # F: 同期日
                ])
            time.sleep(0.5)
        except Exception as e:
            print(f"Skipping {ticker} due to error: {e}")
            continue

    if discovered_gems:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"✨ 縦型構造で {len(discovered_gems)} 件の原石を完全にマッピングしました。")

if __name__ == "__main__":
    main()
