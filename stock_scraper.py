import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# =========================================================================
# ⚙️ 安全第一・低速巡回設定
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 300_000_000        # $300M (足切りライン)
MAX_MARKET_CAP = 10_000_000_000   　# $10B

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

def get_sec_all_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        return [item["ticker"].upper() for item in data.values() if "." not in item["ticker"] and "-" not in item["ticker"]]
    except Exception as e:
        print(f"❌ SECからのティッカーリスト取得に失敗: {e}")
        return []

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
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        return ws

def main():
    tickers = get_sec_all_tickers()
    if not tickers:
        print("📭 SECからのデータが空です。")
        return
        
    # 🌟 テスト用配列：検証用として、確実にQuantumテーマにヒットする「RGTI」を先頭に固定
    test_tickers = ["RGTI"] + tickers[:500]
    
    print(f"🐢 【手動テストモード】先頭の {len(test_tickers)} 社のみを安全に走査します...")
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")

    for count, ticker in enumerate(test_tickers, 1):
        try:
            # 🛠️ 修正：存在しなかった session 引数を完全に排除してエラーを解決
            stock = yf.Ticker(ticker)
            info = stock.info
            
            market_cap = info.get("marketCap", 0)
            summary = info.get("longBusinessSummary", "").lower()
            if not summary:
                time.sleep(0.5)
                continue
            
            # 🛠️ 修正：テスト時は時価総額フィルターを緩め、書き込みロジックの開通確認を最優先する
            if ticker != "RGTI":
                if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                    time.sleep(0.5)
                    continue
            
            # 2. 12テーマの走査
            matched_theme = None
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary for kw in keywords):
                    matched_theme = theme
                    break
            
            if matched_theme:
                print(f" ✨ 【原石発見】[{matched_theme}] {ticker} - ${market_cap/1e6:.1f}M")
                discovered_gems.append([
                    matched_theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2),
                    info.get("longBusinessSummary", ""),
                    current_date
                ])
            
            time.sleep(1.0)
            
        except Exception as e:
            # 🛠️ 修正：予期せぬエラーが起きた場合は、ログに原因を出力するように変更
            print(f"⚠️ {ticker} の走査中にエラーが発生: {e}")
            time.sleep(1.0)
            continue

    # 3. スプレッドシートへの書き込み
    if len(discovered_gems) > 0:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 成功！テストをクリアし、{len(discovered_gems)} 件の原石をスプレッドシートへ格納しました。")
    else:
        print("📭 テスト対象の銘柄からテーマに合致するものが検出されませんでした。")

if __name__ == "__main__":
    main()
