import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

# 📊 畑作業に最適なスモールキャップの制約
MIN_MARKET_CAP = 100_000_000      # $100M
MAX_MARKET_CAP = 1_500_000_000    # $1.5B

# 📡 12の国策テーマ
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
    """⚠️ SECの公式サーバーから、現時点で米国に上場している全ティッカーをリアルタイムに自動回収する関数"""
    url = "https://www.sec.gov/files/company_tickers.json"
    # SECのAPIを叩く際は、User-Agent（誰がアクセスしているか）の明記が義務付けられています
    headers = {"User-Agent": "YourName YourEmail@example.com"} 
    
    try:
        res = requests.get(url, headers=headers)
        data = res.json()
        # SECのJSON構造からティッカー（大文字）だけをリストに抽出
        all_tickers = [item["ticker"].upper() for item in data.values()]
        print(f"✅ SECから全米の上場企業リストを取得完了。総数: {len(all_tickers)} 社")
        return all_tickers
    except Exception as e:
        print(f"❌ SECからのティッカーリスト取得に失敗: {e}")
        # 万が一SECが落ちていた場合のミニマムなフォールバック
        return ["AEHR", "ATOM", "SKYT", "QUIK", "NVTS", "OKLO", "SMR", "UUUU"]

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
    # 🌟 手動の配列を廃止し、実行した瞬間の全米上場リスト（数千社）を自動取得
    tickers_to_scan = get_sec_all_tickers()
    
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")
    
    print("🕵️ 全米のスクリーニングを開始します（これには数分かかります）...")
    
    for ticker in tickers_to_scan:
        # ドット（.）が含まれるティッカー（例: BRK.Bなど）はYahoo Financeでエラーになりやすいためスキップ
        if "." in ticker or "-" in ticker:
            continue
            
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # 1. 時価総額の冷酷なフィルター（$100M 〜 $1.5B 以外は問答無用で弾く）
            market_cap = info.get("marketCap", 0)
            if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                continue
                
            summary = info.get("longBusinessSummary", "").lower()
            if not summary:
                continue
            
            # 2. 12の国策テーマに適合するか走査
            matched_theme = None
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary for kw in keywords):
                    matched_theme = theme
                    break
            
            # 3. 適合した場合のみ、あなたの理想の縦型構造で記録
            if matched_theme:
                print(f" ✨ 【原石発見】[{matched_theme}] {ticker} - 時価総額: ${market_cap/1e6:.1f}M")
                discovered_gems.append([
                    matched_theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2),
                    info.get("longBusinessSummary", ""), # プロフィール全文
                    current_date
                ])
                
            # ⚠️ 大量にAPIを叩くため、ブロック（Rate Limit）を回避する制約（ウェイト）を入れる
            time.sleep(0.2)
            
        except Exception as e:
            # エラーが出た銘柄（上場廃止直後など）はログを出して冷酷に無視
            continue

    # 4. 炙り出された原石をスプレッドシートへ一括上書き
    if discovered_gems:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update('A1', [['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 処理完了！全米から {len(discovered_gems)} 件のテーマ別新興スモールキャップを縦型でマッピングしました。")
    else:
        print("📭 今回の走査では条件に完全に一致する銘柄は見つかりませんでした。")

if __name__ == "__main__":
    main()
