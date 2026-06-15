import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================================
# ⚙️ 最適化設定
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

# 畑作業に最適な「新興スモールキャップ」の制約
MIN_MARKET_CAP = 100_000_000      # $100M
MAX_MARKET_CAP = 1_500_000_000    # $1.5B

# 並列実行するスレッド数（GitHub Actionsの環境下で最も効率が良い20スレッドを採用）
MAX_WORKERS = 10

# 📡 12の国策テーマと検索キーワード（小文字で判定）
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
    """SECの公式からリアルタイムに全米上場ティッカーを自動回収"""
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {"User-Agent": "YourName YourEmail@example.com"} # SECの規則に従いUser-Agentを明記
    
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

def process_single_ticker(ticker):
    """【スレッド個別処理】1つのティッカーを冷酷に審査する関数"""
    try:
        stock = yf.Ticker(ticker)
        # ⚠️ Ticker.infoの内部通信を1回にまとめるため、一括でオブジェクト化
        info = stock.info
        
        # 1. 時価総額フィルター
        market_cap = info.get("marketCap", 0)
        if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
            return None
            
        summary = info.get("longBusinessSummary", "").lower()
        if not summary:
            return None
        
        # 2. 12の国策テーマのキーワード走査
        for theme, keywords in THEME_KEYWORDS.items():
            if any(kw in summary for kw in keywords):
                return [
                    theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2), # $M 単位
                    info.get("longBusinessSummary", ""),
                    time.strftime("%Y-%m-%d")
                ]
    except Exception:
        # エラー（上場廃止直後など）はノイズなのでログすら出さずに静かにスキップ
        return None
    return None

def main():
    tickers = get_sec_all_tickers()
    if not tickers:
        print("📭 スキャン対象のティッカーが空です。処理を終了します。")
        return
        
    print(f"🕵️ マルチスレッドによる全米スクリーニングを開始します (並列数: {MAX_WORKERS})...")
    discovered_gems = []
    
    # ⚡ ThreadPoolExecutorによる爆速並列化
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # 全ティッカーの非同期タスクを仕込む
        future_to_ticker = {executor.submit(process_single_ticker, ticker): ticker for ticker in tickers}
        
        # 完了したものから冷酷にデータを回収していく
        for count, future in enumerate(as_completed(future_to_ticker), 1):
            result = future.result()
            if result:
                print(f" ✨ 【原石発見】[{result[0]}] {result[1]} - ${result[3]:.1f}M")
                discovered_gems.append(result)
                
            if count % 500 == 0:
                print(f" 🟩 全米全企業の走査進捗: {count} / {len(tickers)} 社完了...")

    # 3. 炙り出された原石をスプレッドシートへ一括書き込み
    if discovered_gems:
        ws = get_or_create_sheet()
        ws.clear() # 既存の古いリストを全クリア
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 処理完了！全米から {len(discovered_gems)} 件のテーマ別新興スモールキャップを完全縦型マッピングしました。")
    else:
        print("📭 条件に完全に一致する銘柄は今回は見つかりませんでした。")

if __name__ == "__main__":
    main()
