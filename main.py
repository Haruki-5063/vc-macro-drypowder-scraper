import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# =========================================================================
# 📊 【接続先スプレッドシートの設定】※PEと同じIDを使い、タブ名だけを完全に分ける
# =========================================================================
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
SHEET_NAME = "VC_PE_Market_DryPowder"  # 👈 新しい出力ページ（タブ名）

# クローリング先のターゲットURL（例：PitchBookの最新ベンチャーモニターやPreqinの集計ページなど）
TARGET_URLS = {
    "PitchBook_VC_Report": "https://pitchbook.com/news/reports/..." ,
    "Preqin_Dry_Powder": "https://www.preqin.com/..."
}

# 相手サーバーにブロックされないための偽装ヘッダー
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_or_create_sheet(spreadsheet_id: str, sheet_name: str):
    """環境変数から鍵を拾ってスプレッドシートの指定タブを捕捉するインフラ関数"""
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not secret_json:
        raise ValueError("GCP_SERVICE_ACCOUNT_KEY が設定されていません。")
    
    service_account_info = json.loads(secret_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)
    
    sh = gc.open_by_key(spreadsheet_id)
    try:
        return sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"📌 シート '{sheet_name}' を新規作成します。")
        # ヘッダー行を初期値としてセットして作成
        worksheet = sh.add_worksheet(title=sheet_name, rows="100", cols="5")
        worksheet.update('A1', [['Date', 'Source', 'Metric_Name', 'Value_Billions']])
        return worksheet

def scrape_macro_drypowder():
    """
    PitchBookやPreqinからデータをクレンジングして構造化する関数
    """
    print("🚀 VC/PE業界のドライパウダーデータの巡回を開始...")
    extracted_data = []
    current_date = time.strftime("%Y-%m-%d")

    # --- 1. ここに特定のページからBeautifulSoupで数字を抜くロジックを実装します ---
    try:
        # 例：PitchBookの特定ページをFetch
        # res = requests.get(TARGET_URLS["PitchBook_VC_Report"], headers=HEADERS)
        # soup = BeautifulSoup(res.text, 'html.parser')
        
        # 数値のダミー抽出（後ほど具体的なHTML構造に合わせて書き換えます）
        pb_value = 310.5  # 例：$310.5B の待機資金
        extracted_data.append([current_date, "PitchBook", "US_VC_Dry_Powder_Billion", pb_value])
        
    except Exception as e:
        print(f"❌ PitchBookのデータ抽出に失敗: {e}")

    return pd.DataFrame(extracted_data, columns=['Date', 'Source', 'Metric_Name', 'Value_Billions'])

def run_vc_pipeline():
    # 1. データのスクレイピング
    df_new = scrape_macro_drypowder()
    if df_new.empty:
        print("⚠ 抽出されたデータが空です。処理を中断します。")
        return
        
    # 2. スプレッドシートへの接続
    worksheet = get_or_create_sheet(SPREADSHEET_ID, SHEET_NAME)
    
    # 3. 既存データの最下行に追記（アペンド）するロジック
    # ※マクロ集計は毎日変わるものではないため、重複を避けるか、単純追記にしてPower BI側で最新日付にフィルターします
    existing_records = worksheet.get_all_values()
    next_row = len(existing_records) + 1
    
    data_to_write = df_new.values.tolist()
    worksheet.update(f'A{next_row}', data_to_write)
    print(f"✨ 『{SHEET_NAME}』シートの {next_row} 行目へマクロデータを射出しました。")

if __name__ == "__main__":
    run_vc_pipeline()
