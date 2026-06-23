import os
import json
import time
import re
import math
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
SPREADSHEET_ID = "1u3HtebzKnq2zmXDDnZq7OslCbgcnpXPPkD8LQbCvMQM"
MASTER_SHEET_NAME = "Master_Watchlist"
ELITE_SHEET_NAME = "Elite_Watchlist"

# SEC EDGARアクセス用の正規ヘッダー
SEC_HEADERS = {
    'User-Agent': 'CorporateAnalystResearch/1.0 (analyst_data@example.com)'
}

# CIKマッピング用のグローバルキャッシュ
_TICKER_TO_CIK_MAP = None

def get_google_sheets_client():
    secret_json = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    creds = Credentials.from_service_account_info(
        json.loads(secret_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return gspread.authorize(creds)

def get_quarter_label(timestamp):
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
# 🎯 【SEC CIK 変換エンジン】
# =========================================================================
def load_sec_cik_map():
    """SEC公式からTicker->CIKのコンバージョン表を一度だけロードしてキャッシュ"""
    global _TICKER_TO_CIK_MAP
    if _TICKER_TO_CIK_MAP is not None:
        return _TICKER_TO_CIK_MAP
        
    _TICKER_TO_CIK_MAP = {}
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        res = requests.get(url, headers=SEC_HEADERS)
        if res.status_code == 200:
            data = res.json()
            for item in data.values():
                ticker_upper = str(item['ticker']).upper()
                _TICKER_TO_CIK_MAP[ticker_upper] = str(item['cik_str']).zfill(10)
        else:
            print(f" 🚨 SEC Tickerマップ取得失敗: HTTP {res.status_code}")
    except Exception as e:
        print(f" 🚨 CIKマップ初期化エラー: {e}")
    return _TICKER_TO_CIK_MAP

# =========================================================================
# 🧹 【最強前処理 ＆ 10-Q/10-K 索敵モジュール】
# =========================================================================
def notebooklm_style_cleaner(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer"]):
        tag.extract()

    for tag in soup.find_all(re.compile(r'^ix:')):
        tag.replace_with(tag.get_text())

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
    text_lower = clean_text.lower()
    extracted_sections = []
    found_positions = set()

    for keyword in keywords:
        pos = 0
        while True:
            idx = text_lower.find(keyword.lower(), pos)
            if idx == -1:
                break

            is_duplicate = any(abs(idx - fp) < 3000 for fp in found_positions)
            if not is_duplicate:
                start = max(0, idx - 1500)
                end = min(len(clean_text), idx + window)
                extracted_sections.append(clean_text[start:end])
                found_positions.add(idx)

            pos = idx + 1

    if not extracted_sections:
        return ""

    return "\n\n--- [セクション区切り] ---\n\n".join(extracted_sections)

def fetch_sec_clean_context(ticker: str) -> str:
    """
    TickerをCIKに正規変換 ➔ 10-Q/10-Kをクローリング
    """
    cik_map = load_sec_cik_map()
    cik_padded = cik_map.get(str(ticker).upper())
    
    if not cik_padded:
        print(f" 🚨 CIKが見つかりません (Ticker: {ticker})")
        return ""
        
    api_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    
    try:
        res = requests.get(api_url, headers=SEC_HEADERS)
        if res.status_code != 200:
            print(f" 🚨 SEC APIアクセス失敗 ({ticker} / CIK: {cik_padded}): HTTP {res.status_code}")
            return ""
            
        submission_data = res.json()
        recent_filings = submission_data.get('filings', {}).get('recent', {})
        
        target_index = None
        for i, form_type in enumerate(recent_filings.get('form', [])):
            if form_type in ['10-Q', '10-K']:
                target_index = i
                break
                
        if target_index is None:
            return ""
            
        acc_num = recent_filings['accessionNumber'][target_index]
        acc_num_clean = acc_num.replace('-', '')
        doc_name = recent_filings['primaryDocument'][target_index]
        
        # CIKの先頭ゼロを外した整数値をURLに組み込む必要あり
        cik_int = int(cik_padded)
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_num_clean}/{doc_name}"
        
        html_res = requests.get(doc_url, headers=SEC_HEADERS)
        if html_res.status_code != 200:
            return ""
            
        cleaned_all_text = notebooklm_style_cleaner(html_res.text)
        
        backlog_keywords = [
            "backlog", "Remaining Performance Obligations", "RPO", 
            "contract backlog", "unfunded backlog"
        ]
        final_context = extract_target_section(cleaned_all_text, backlog_keywords, window=12000)
        
        print(f"   [DEBUG] {ticker}: SECから抽出された文字数 = {len(final_context)}文字 (キーワードヒット数チェック)")
        
        return final_context
        
    except Exception as e:
        print(f" 🚨 SECクローリング例外 ({ticker}): {e}")
        return ""

# =========================================================================
# 🧠 【Gemini 超高精度マイニングエンジン】
# =========================================================================
def ask_gemini_sec_analysis(ticker, summary, sec_text):
    api_key = os.environ.get("GEMINI_API_KEY")
    default_res = {"Value_Chain": "4_General", "backlog": "N/A", "backlog_source_text": "N/A"}
    if not api_key: 
        return default_res
        
    client = genai.Client(api_key=api_key)
    
    # あまりに長すぎる場合はGeminiの集中力を維持するため先頭12万文字に丸める
    truncated_sec = sec_text[:120000]
    input_context = f"--- Business Summary (yfinance) ---\n{summary}\n\n--- SEC 10-Q/10-K Text ---\n{truncated_sec}"
    
    prompt = f"""
【役割】
あなたは米国の成長株・マイクロキャップを専門とするプロフェッショナルな株式投資リサーチャーです。
提供されたファクト情報（Business Summary および SEC提出書類の抜粋テキスト）を網羅的に分析し、以下の2つのタスクを遂行してください。

【対象企業情報】
{input_context}

---

【タスク1：Value_Chain（バリューチェーンレイヤー）の分類】
企業のビジネスモデルから、最も当てはまるレイヤーを1つ選択してください。100%完璧な記述がなくても、事業概要から合理的に推認できる場合は分類を割り当ててください。

- "1_Upstream" (上流):
  ウラニウム・ヘリウム・重要鉱物などの資源採掘、精錬、コア基礎素材の供給、または物理的なコア基盤技術の開発・提供。
- "2_Midstream" (中流):
  産業用コンポーネント、電力インフラ設備（変圧器、タービンなど）、半導体製造装置、製造用ハードウェア、データセンター向け設備などの開発・製造。
- "3_Downstream" (下流):
  エンドユーザー/企業向けサービス、電力網・ユーティリティ運用、SaaS、ソフトウェアプラットフォーム、システムインテグレーションの提供。
- "4_General" (その他/複合):
  上記3つのいずれにも明確にマッピングできない、または完全に複数のレイヤーに均等にまたがっている場合。

---

【タスク2：Backlog（受注残高）のマイニング】
SECテキストから、企業の直近の「バックログ（受注残高）」または「RPO（Remaining Performance Obligations、残存履行義務）」の金額を探索してください。

[抽出ガイドライン]
1. テキスト内に "$12.5 million" や "$150M", "$1.2 billion" のような【金額表現】を伴うバックログ/RPOの記載があるか探してください。
2. 直近（最新四半期）の数値と思われるものを優先してください。
3. 金額が見つかった場合は "backlog" にその金額（例: "$25.3M"）を格納し、"backlog_source_text" にはその金額が記載されている文脈（前後1〜2文）をそのまま抜き出して格納してください。
4. 探索キーワード（backlog, RPO等）がテキスト内に存在しても、具体的な数値/金額の記載が一切ない場合のみ、"backlog": "N/A", "backlog_source_text": "N/A" としてください。

---

[出力フォーマット制約]
必ず以下のJSONスキーマに完全準拠したJSONオブジェクトのみを出力してください。マークダウンの ```json などのラッパーは一切不要です。
{{
  "Value_Chain": "1_Upstream または 2_Midstream または 3_Downstream または 4_General",
  "backlog": "抽出した金額（なければN/A）",
  "backlog_source_text": "金額の根拠テキスト（なければN/A）"
}}
"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", 
                temperature=0.1  # わずかに柔軟性を持たせる
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f" 🚨 Geminiパースエラー ({ticker}): {e}")
        return default_res
# =========================================================================
# 🚀 【メインプロセッサ】
# =========================================================================
def main():
    gc = get_google_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    
    master_ws = sh.worksheet(MASTER_SHEET_NAME)
    all_master_records = master_ws.get_all_records()
    
    master_records = all_master_records[:10]
    print(f"🧪 完全統合デバッグモード：先頭 {len(master_records)} 銘柄を処理します。")
    
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_data = elite_ws.get_all_records()
    except gspread.exceptions.WorksheetNotFound:
        elite_data = []

    existing_vc_map = {r['Ticker']: r['Value_Chain'] for r in elite_data if 'Ticker' in r}
    existing_backlog_map = {r['Ticker']: r['Backlog_Amount'] for r in elite_data if 'Ticker' in r}
    existing_source_map = {r['Ticker']: r.get('Backlog_Source_Text', 'N/A') for r in elite_data if 'Ticker' in r}

    # =========================================================================
    # 🌟 1パス目：動的クォーター検出 ➔ 最新3期に冷酷に制限
    # =========================================================================
    print("🔄 1パス目：決算タイムスタンプをスキャン中...")
    detected_quarters = set()
    stock_raw_financials = {}
    
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker: continue
        try:
            stock = yf.Ticker(ticker)
            q_financials = stock.quarterly_financials
            q_balance = stock.quarterly_balance_sheet
            
            stock_raw_financials[ticker] = (stock, q_financials, q_balance)
            
            rev_idx = [i for i in q_financials.index if "Revenue" in i]
            if rev_idx:
                timestamps = q_financials.loc[rev_idx[0]].dropna().index
                for ts in timestamps[:3]:
                    lbl = get_quarter_label(ts)
                    if lbl: detected_quarters.add(lbl)
            time.sleep(0.3)
        except Exception:
            continue

    # 🌟【改善】横に無駄に伸びないよう、検出された中から降順で最新3期のみを厳選
    sorted_quarters = sorted(list(detected_quarters), reverse=True)[:3]
    sorted_quarters.reverse() # 時系列順（古い順）に並び替え
    print(f"📈 厳選された最新3クォーター列: {sorted_quarters}")

    # =========================================================================
    # 🌟 2パス目：マッピング
    # =========================================================================
    base_headers = ['Ticker', 'Value_Chain', 'Volume_Ratio']
    tail_headers = ['RD_Ratio', 'Cash_Runway', 'Backlog_Amount', 'Backlog_Source_Text', 'Last_Updated']
    
    final_headers = base_headers + [f"Rev_YoY_{q}" for q in sorted_quarters] + tail_headers
    
    updated_elite_rows = []
    current_date = time.strftime("%Y-%m-%d")
    
    print("🔄 2パス目：詳細データのマッピングを実行中...")
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker or ticker not in stock_raw_financials:
            continue

        stock, q_financials, q_balance = stock_raw_financials[ticker]

        # yfinanceのオブジェクトから直接生の事業概要を確実に引っこ抜く
        try:
            biz_summary = stock.info.get("longBusinessSummary", "")
        except Exception:
            biz_summary = ""

        # AI記憶再利用
        has_past_data = ticker in existing_vc_map and existing_vc_map[ticker] != "" and "4_General" not in existing_vc_map[ticker]
        if has_past_data:
            vc_layer = existing_vc_map[ticker]
            backlog_val = existing_backlog_map.get(ticker, "N/A")
            backlog_source = existing_source_map.get(ticker, "N/A")
        else:
            print(f" ➔ 📡 {ticker}: 10-Q/10-K を正規索敵中...")
            sec_text = fetch_sec_clean_context(ticker)
            ai_res = ask_gemini_sec_analysis(ticker, biz_summary, sec_text)
            vc_layer = ai_res.get("Value_Chain", "4_General")
            backlog_val = ai_res.get("backlog", "N/A")
            backlog_source = ai_res.get("backlog_source_text", "N/A")
            time.sleep(0.3)

        # 財務計算（ここから元のコードに綺麗に繋がります）
        try:
            history = stock.history(period="30d")

        # 財務計算
        try:
            history = stock.history(period="30d")
            volume_ratio = round(history['Volume'].iloc[-1] / history['Volume'].mean(), 2) if len(history) >= 2 else 1.0
            
            rev_idx = [i for i in q_financials.index if "Revenue" in i]
            rd_idx = [i for i in q_financials.index if "Research" in i or "R&D" in i]
            net_inc_idx = [i for i in q_financials.index if "Net Income" in i]
            cash_idx = [i for i in q_balance.index if "Cash" in i]
            
            ticker_q_values = {f"Rev_YoY_{q}": "" for q in sorted_quarters}
            
            if rev_idx:
                rev_data = q_financials.loc[rev_idx[0]].dropna()
                timestamps = rev_data.index
                rev_series = rev_data.iloc[::-1]
                if len(rev_series) >= 5:
                    yoy_series = rev_series.pct_change(periods=4).iloc[::-1]
                    for i in range(min(3, len(yoy_series))):
                        val = yoy_series.iloc[i]
                        q_lbl = get_quarter_label(timestamps[i])
                        col_key = f"Rev_YoY_{q_lbl}"
                        if col_key in ticker_q_values and not math.isnan(val):
                            ticker_q_values[col_key] = f"{round(val * 100, 1)}%"

            # 🌟【改善】R&D項目の有無で、エラーと開示なしを冷酷に区別
            rd_ratio_str = "NO_R&D(開示なし)"
            if rev_idx:
                latest_rev = q_financials.loc[rev_idx[0]].iloc[0]
                if rd_idx:
                    latest_rd = q_financials.loc[rd_idx[0]].iloc[0]
                    if latest_rev > 0 and not math.isnan(latest_rd):
                        rd_ratio_str = f"{round((abs(latest_rd) / latest_rev) * 100, 1)}%"
                else:
                    rd_ratio_str = "NO_R&D"

            cash_runway = "N/A"
            if cash_idx and net_inc_idx:
                latest_cash = q_balance.loc[cash_idx[0]].iloc[0]
                latest_loss = q_financials.loc[net_inc_idx[0]].iloc[0]
                if latest_loss < 0 and not math.isnan(latest_cash):
                    cash_runway = f"{round(abs(latest_cash) / abs(latest_loss), 1)} Q"
                elif latest_loss >= 0:
                    cash_runway = "Black (黒字)"
        except Exception:
            volume_ratio, rd_ratio_str, cash_runway = 1.0, "Calc_Error", "Calc_Error"
            ticker_q_values = {f"Rev_YoY_{q}": "" for q in sorted_quarters}

        # 行データの組み立て
        q_row_parts = [ticker_q_values[f"Rev_YoY_{q}"] for q in sorted_quarters]
        
        row = [
            ticker, vc_layer, volume_ratio
        ] + q_row_parts + [
            rd_ratio_str, cash_runway, backlog_val, backlog_source, current_date
        ]
        updated_elite_rows.append(row)
        time.sleep(0.3)

    # 3. シートへの最終書き込み
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        elite_ws = sh.add_worksheet(title=ELITE_SHEET_NAME, rows="1000", cols=str(len(final_headers)))
        
    elite_ws.update(range_name='A1', values=[final_headers])
    if updated_elite_rows:
        elite_ws.append_rows(updated_elite_rows)
    print("🎉 SEC APIの紐付け成功 ＆ 財務表示の最適化版が正常終了しました！")

if __name__ == "__main__":
    main()
