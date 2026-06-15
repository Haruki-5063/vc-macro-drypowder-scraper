import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# =========================================================================
# ⚙️ 本番仕様：時価総額フィルター（物理テック対応・低速安定フルスキャン）
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 300_000_000        # $300M (足切りライン)
MAX_MARKET_CAP = 10_000_000_000     # $10B  (大本命の物理テックまでカバー)

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
        
        # 1. 前後の空白を削り、すべて大文字に統一
        raw_tickers = [item["ticker"].upper().strip() for item in data.values()]
        
        # 2. 不要な記号付き（ドットやハイフン）を排除
        clean_tickers = [t for t in raw_tickers if "." not in t and "-" not in t]
        
        # 3. set() で重複を完全に抹殺し、アルファベット順にソート
        unique_tickers = sorted(list(set(clean_tickers)))
        
        print(f"📋 SECから重複のないクリーンな {len(unique_tickers)} 社のティッカーを取得しました。")
        return unique_tickers
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
        ws.update(range_name='A1', values=[['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        return ws

def main():
    tickers = get_sec_all_tickers()
    if not tickers:
        print("📭 スキャン対象のティッカーが空です。")
        return
        
    print(f"🐢 【本番フルスキャン仕様】全 {len(tickers)} 社の安全第一巡回を開始します（1社1秒のウェイトを維持）...")
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")

    for count, ticker in enumerate(tickers, 1):
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            market_cap = info.get("marketCap", 0)
            
            # 1. 時価総額の判定（$300M 〜 $10B）
            if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                # 判定落ちの際も、相手のサーバーへ配慮してごく微小なインターバル
                time.sleep(0.1)
                continue
                
            summary = info.get("longBusinessSummary", "")
            if not summary:
                time.sleep(0.1)
                continue
            
            # 2. 12テーマの走査
            matched_theme = None
            summary_lower = summary.lower()
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary_lower for kw in keywords):
                    matched_theme = theme
                    break
            
            if matched_theme:
                # 本番ログの視認性を保つため、ヒット時のみ鮮烈にログを残す
                print(f" ✨ 【原石発見】[{matched_theme}] {ticker} - ${market_cap/1e6:.1f}M")
                discovered_gems.append([
                    matched_theme,
                    ticker,
                    info.get("longName", ticker),
                    round(market_cap / 1_000_000, 2),
                    summary,
                    current_date
                ])
            
            # 人間的なマイルド・ウェイト（1.0秒）
            time.sleep(1.0)
            
        except Exception as e:
            # 404などの異常通信は埋もれさせずログに刻む
            print(f" 🚨 [{ticker}] 解析または通信スキップ: {e}")
            time.sleep(1.0)
            continue

        # 進行状況を100社ごとにシンプルに出力
        if count % 100 == 0:
            print(f" 🟩 進捗: {count} / {len(tickers)} 社を安全に通過完了... (現在の累計発見数: {len(discovered_gems)}件)")

    # 3. 過去データをリセットし、最新の成果をスプレッドシートへ射出
    if len(discovered_gems) > 0:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update(range_name='A1', values=[['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 本番スキャン完了！{len(discovered_gems)} 件の最新原石でシートを完全肥沃化しました。")
    else:
        print("📭 今回の全米スキャンでは、条件に合致する銘柄が検出されませんでした。")

if __name__ == "__main__":
    main()
