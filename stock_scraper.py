import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# =========================================================================
# ⚙️ 修正設定：時価総額フィルター（物理テック対応）
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "Master_Watchlist"

MIN_MARKET_CAP = 300_000_000        # $300M
MAX_MARKET_CAP = 10_000_000_000     # $10B

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
        
        # 💡 【最重要修正】 .strip() を追加して、SECデータに含まれる見えない空白を完全に消し去る
        raw_tickers = [item["ticker"].upper().strip() for item in data.values()]
        
        # 不要な記号付き銘柄を排除
        clean_tickers = [t for t in raw_tickers if "." not in t and "-" not in t]
        return clean_tickers
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
        print("📭 SECからのデータが空です。")
        return
        
    # 🌟 テスト用配列：RGTI（検証用）と、最初の50社を結合
    test_tickers = ["RGTI"] + tickers[:500]
    
    print(f"🐢 【完全透過デバッグモード】先頭の {len(test_tickers)} 社をクレンジング済みの値で走査します...")
    discovered_gems = []
    current_date = time.strftime("%Y-%m-%d")

    for count, ticker in enumerate(test_tickers, 1):
        try:
            # クレンジングされたティッカーでYahooにアクセス
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # ログのブラックボックス化を防ぐため、1社ごとにステータスを明示出力
            company_name = info.get("longName", ticker)
            market_cap = info.get("marketCap", 0)
            
            # 1. 時価総額の判定
            if not (MIN_MARKET_CAP <= market_cap <= MAX_MARKET_CAP):
                print(f" ✖️ [{ticker}] スキップ: 時価総額が対象外 (${market_cap/1e6:.1f}M)")
                time.sleep(0.5)
                continue
                
            summary = info.get("longBusinessSummary", "")
            if not summary:
                print(f" ✖️ [{ticker}] スキップ: 事業概要テキストが空です")
                time.sleep(0.5)
                continue
            
            # 2. 12テーマの走査
            matched_theme = None
            summary_lower = summary.lower()
            for theme, keywords in THEME_KEYWORDS.items():
                if any(kw in summary_lower for kw in keywords):
                    matched_theme = theme
                    break
            
            if matched_theme:
                print(f" ✨ 【原石発見】[{matched_theme}] {ticker} - ${market_cap/1e6:.1f}M")
                discovered_gems.append([
                    matched_theme,
                    ticker,
                    company_name,
                    round(market_cap / 1_000_000, 2),
                    summary,
                    current_date
                ])
            else:
                print(f" 💤 [{ticker}] スキップ: 国策キーワードに該当なし")
            
            time.sleep(1.0)
            
        except Exception as e:
            # 404などのエラーが出た場合は、絶対に隠蔽せず理由を出力
            print(f" 🚨 [{ticker}] 通信または解析エラー: {e}")
            time.sleep(1.0)
            continue

    # 3. スプレッドシートへの書き込み
    if len(discovered_gems) > 0:
        ws = get_or_create_sheet()
        ws.clear()
        ws.update(range_name='A1', values=[['Theme', 'Ticker', 'Company_Name', 'Market_Cap_M', 'Business_Summary', 'Last_Updated']])
        ws.append_rows(discovered_gems)
        print(f"🎉 成功！{len(discovered_gems)} 件の原石をスプレッドシートへ格納しました。")
    else:
        print("📭 条件に合致する銘柄が検出されませんでした。")

if __name__ == "__main__":
    main()
