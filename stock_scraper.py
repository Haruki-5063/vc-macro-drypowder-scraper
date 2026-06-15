import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================================
# ⚙️ 最適化・偽装設定
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 100_000_000      # $100M
MAX_MARKET_CAP = 1_500_000_000    # $1.5B

# Yahooのブロックをマイルドに回避するため、スレッド数を10に調整
MAX_WORKERS = 10

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

def process_single_ticker(ticker, session):
    """【修正】Yahooのブロックを回避するために、偽装セッションを使い回す"""
    try:
        # セッションをインジェクションして、ブラウザからのアクセスに見せかける
        stock = yf.Ticker(ticker, session=session)
        info = stock.info
        
        market_cap = info.get("marketCap", 0)
        if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
            return None
            
        summary = info.get("longBusinessSummary", "").lower()
        if not summary:
            return None
        
        for theme, keywords in THEME_KEYWORDS.items():
            if any(kw in summary for kw in keywords):
                return [
                    theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2),
                    info.get("longBusinessSummary", ""),
                    time.strftime("%Y-%m-%d")
                ]
    except Exception as e:
        # 401などの重大なエラーが発生しているか確認するため、怪しいエラーだけログに出すように変更
        if "401" in str(e) or "Unauthorized" in str(e):
            print(f"⚠️ {ticker} がYahooに拒否されました: {e}")
        return None
    return None

def main():
    tickers = get_sec_all_tickers()
    if not tickers:
        print("📭 スキャン対象のティッカーが空です。")
        return
        
    print(f"🕵️ 偽装セッションを有効化し、全米スクリーニングを開始します (並列数: {MAX_WORKERS})...")
    discovered_gems = []
    
    # 🌟 【最重要の対策】Yahoo Financeを騙すためのクリーンなブラウザセッションを生成
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    })

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # セッションオブジェクトを各スレッドに引き渡す
        future_to_ticker = {executor.submit(process_single_ticker, ticker, session): ticker for ticker in tickers}
        
        for count, future in enumerate(as_completed(future_to_ticker), 1):
            result = future.result()
            if result:
                print(f" ✨ 【原石発見】[{result[0]}] {result[1]} - ${result[3]:.1f}M")
                discovered_gems.append(result)
                
            if count % 1000 == 0:
                print(f" 🟩 全米全企業の走査進捗: {count} / {len(tickers)} 社完了...")

    # 3. シートへの一ッ括上書き処理
    ws = get_or_create_sheet()
    
    # 【安全設計】もし途中でブロックされて0件だった場合は、既存のシートを破壊（クリア）しないようにガードをかける
    if len(discovered_gems) > 0:
        print(f"🎉 データの安全を確認。{len(discovered_gems)}件の原石をスプレッドシートへ射出します。")
        ws.clear() 
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print("✨ スプレッドシートの更新が完全に完了しました！")
    else:
        print("📭 警告：今回の走査で取得できた銘柄が0件です。Yahooにブロックされた可能性があるため、既存シートのクリアをスキップして保護しました。")

if __name__ == "__main__":
    main()
