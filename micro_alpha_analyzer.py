import os
import json
import time
import re
import unicodedata
import requests
import yfinance as yf
import gspread
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types

# =========================================================================
# ⚙️ 設定値
# =========================================================================
SPREADSHEET_ID = "あなたのスプレッドシートID"
MASTER_SHEET_NAME = "Master_Watchlist"
ELITE_SHEET_NAME = "Elite_Watchlist"

SEC_HEADERS = {
    'User-Agent': 'CorporateAnalystResearch/1.0 (analyst_data@example.com)'
}

def get_google_sheets_client():
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    creds = Credentials.from_service_account_info(
        json.loads(secret_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return gspread.authorize(creds)

def get_quarter_label(timestamp):
    """Timestampから『2026_Q1』のようなクォーター表記を冷酷に算出"""
    try:
        year = timestamp.year
        month = timestamp.month
        if month <= 3: quarter = "Q1"
        elif month <= 6: quarter = "Q2"
        elif month <= 9: quarter = "Q3"
        else: quarter = "Q4"
        return f"{year}_{quarter}"
    except Exception:
        return None

# =========================================================================
# 🧹 【最強前処理 ＆ 10-Q/10-K 索敵モジュール】
# =========================================================================
def notebooklm_style_cleaner(html_content: str) -> str:
    """HTMLタグの除去、iXBRL置換、およびテーブルのMarkdown化を行う最強前処理"""
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer"]):
        tag.extract()

    for tag in soup.find_all(re.compile(r'^ix:')):
        tag.replace_with(tag.get_text())

    # テーブルをMarkdown形式に変換（AIの数値誤認を防ぐ核心処理）
    for table in soup.find_all("table"):
        markdown_table = []
        for row in table.find_all("tr"):
            cells = [
                re.sub(r'\s+', ' ', cell.get_text().strip())
                for cell in row.find_all(["td", "th"])
            ]
            if any(cells):
                markdown_table.append("| " + " | ".join(cells) + " |")

        if markdown_table:
            table.replace_with("\n" + "\n".join(markdown_table) + "\n")

    text = soup.get_text()
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)

    return text.strip()

def extract_target_section(clean_text: str, keywords: list, window: int = 12000) -> str:
    """キーワードの周辺をスキャンし、重複を排除しながらコンテキストを抽出"""
    text_lower = clean_text.lower()
    extracted_sections = []
    found_positions = set()

    for keyword in keywords:
        pos = 0
        while True:
            idx = text_lower.find(keyword.lower(), pos)
            if idx == -1:
                break

            # 近すぎる位置の重複を排除 (3000文字以内は同一セクションとみなす)
            is_duplicate = any(abs(idx - fp) < 3000 for fp in found_positions)
            if not is_duplicate:
                start = max(0, idx - 1500) # 前方を少し広めに(1500文字)
                end = min(len(clean_text), idx + window)
                extracted_sections.append(clean_text[start:end])
                found_positions.add(idx)

            pos = idx + 1

    if not extracted_sections:
        return ""

    return "\n\n--- [セクション区切り] ---\n\n".join(extracted_sections)

def fetch_sec_clean_context(ticker: str) -> str:
    """
    SECから10-Qまたは10-Kを自動索敵 ➔ 最強前処理 ➔ バックログキーワード周辺のみを狙い撃ち抽出
    """
    cik_padded = str(ticker).zfill(10)
    api_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    
    try:
        res = requests.get(api_url, headers=SEC_HEADERS)
        if res.status_code != 200:
            print(f" 🚨 SEC APIアクセス失敗 ({ticker}): HTTP {res.status_code}")
            return ""
            
        submission_data = res.json()
        recent_filings = submission_data.get('filings', {}).get('recent', {})
        
        # 1. 10-Q または 10-K の最新インデックスを探索
        target_index = None
        for i, form_type in enumerate(recent_filings.get('form', [])):
            if form_type in ['10-Q', '10-K']:
                target_index = i
                break
                
        if target_index is None:
            print(f" 🚨 {ticker} の直近提出書類に 10-Q/10-K が見つかりません。")
            return ""
            
        # 2. URLの組み立て
        acc_num = recent_filings['accessionNumber'][target_index]
        acc_num_clean = acc_num.replace('-', '')
        doc_name = recent_filings['primaryDocument'][target_index]
        
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(ticker)}/{acc_num_clean}/{doc_name}"
        
        # 3. 生HTMLの取得
        html_res = requests.get(doc_url, headers=SEC_HEADERS)
        if html_res.status_code != 200:
            return ""
            
        # 4. 前処理エンジンを発動
        cleaned_all_text = notebooklm_style_cleaner(html_res.text)
        
        # 5. バックログ専用のキーワード群で狙い撃ち
        backlog_keywords = [
            "backlog", "Remaining Performance Obligations", "RPO", 
            "contract backlog", "unfunded backlog"
        ]
        
        final_context = extract_target_section(cleaned_all_text, backlog_keywords, window=12000)
        return final_context
        
    except Exception as e:
        print(f" 🚨 SECコンテキスト生成エラー ({ticker}): {e}")
        return ""

# =========================================================================
# 🧠 【Gemini 超高精度マイニングエンジン】
# =========================================================================
def ask_gemini_sec_analysis(ticker, summary, sec_text):
    """Gemini APIによるバリューチェーン仕分け・バックログ（根拠テキスト付）の超高精度マイニング"""
    api_key = os.environ.get("GEMINI_API_KEY")
    default_res = {"Value_Chain": "4_General", "backlog": "N/A", "backlog_source_text": "N/A"}
    if not api_key: 
        return default_res
        
    client = genai.Client(api_key=api_key)
    input_context = f"--- Business Summary (yfinance) ---\n{summary}\n\n--- SEC 10-Q/10-K Filing Text ---\n{sec_text}"
    
    prompt = f"""
【背景・役割】
あなたは冷静沈着で妥慮を許さないシニア財務アナリストです。
提供された以下の「ファクト情報」のみに基づいて、指定された企業のバリューチェーン分類、およびバックログ（受注残高）のマイニングを行ってください。独自の知識による推測や捏造は一切禁じます。

【対象企業のファクト情報】
{input_context}

【1. Value_Chain 分類ルール】
企業のコア事業概要 (yfinance) の文脈から、以下の3つのいずれかに【直接的かつ明らかな根拠】
