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
企業のコア事業概要 (yfinance) の文脈から、以下の3つのいずれかに【直接的かつ明らかな根拠】がある場合のみ分類してください。
- "1_Upstream"：核燃料、ウラニウムや重要鉱物の採掘・精錬、基礎素材、または物理的なコア基盤技術を直接提供・供給している。
- "2_Midstream"：ハードウェアコンポーネント、産業用装置（タービン、変圧器、半導体製造装置など）、衛星ペイロードなどの製造・開発を行っている。
- "3_Downstream"：エンドユーザー向けサービス、電力網の運用・ユーティリティ、SaaS・ソフトウェアプラットフォームの提供、システムインテグレーションを行っている。

★重要：上記に明らかな根拠がない場合、または複数のレイヤーにまたがっていて判定が曖昧な場合は、絶対に無理に推測せず "4_General" と出力してください。

【2. Backlog Amount 抽出ルール】
SEC 10-Q/10-Kのテキストから、以下のキーワード群（表記ブレ）に該当する、直近の「最新の総額（金額）」を特定してください。
[対象キーワード: "backlog", "Remaining Performance Obligations", "RPO", "contract backlog", "unfunded backlog"]

★厳格な抽出ルール：
1. 過去（前年同期や前四半期）の数値ではなく、必ず「直近（As of [Latest Date]）」の数値を採用すること。
2. テキスト内に具体的な金額（例: $125M, $1.2B）の記載が【直接的】にある場合のみ抽出すること。
3. 抽出した金額（例: $120M）を "backlog" に格納し、その金額が書かれていた箇所の生テキスト（前後1文を含む実際の文章）を丸ごと "backlog_source_text" に格納してください。
4. 該当する記載が一切ない、または文脈から判断がつかない場合は、見栄を張らずに "backlog": "N/A", "backlog_source_text": "N/A" と出力してください。

[Strict Constraints]
- Output ONLY a valid JSON object matching this schema exactly. Do not embed inside markdown wrappers:
{{"Value_Chain": "...", "backlog": "...", "backlog_source_text": "..."}}
"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", 
                temperature=0.0
            ),
        )
        return json.loads(response.text)
    except Exception:
        return default_res

# =========================================================================
# 🚀 【メインプロセッサ】
# =========================================================================
def main():
    gc = get_google_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    
    # 1. データの事前読込
    master_ws = sh.worksheet(MASTER_SHEET_NAME)
    all_master_records = master_ws.get_all_records()
    
    # 🌟【テスト用制限】最初の10件のみに対象を冷酷にスライス
    master_records = all_master_records[:10]
    print(f"🧪 テストモード起動：Master内の全{len(all_master_records)}銘柄中、先頭の {len(master_records)} 銘柄のみを処理します。")
    
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_data = elite_ws.get_all_records()
    except gspread.exceptions.WorksheetNotFound:
        elite_data = []

    existing_vc_map = {r['Ticker']: r['Value_Chain'] for r in elite_data if 'Ticker' in r}
    existing_backlog_map = {r['Ticker']: r['Backlog_Amount'] for r in elite_data if 'Ticker' in r}
    existing_source_map = {r['Ticker']: r.get('Backlog_Source_Text', 'N/A') for r in elite_data if 'Ticker' in r}

    # =========================================================================
    # 🌟 1パス目：全銘柄を巡回して「存在する全クォーター名」を冷酷に洗い出す
    # =========================================================================
    print("🔄 1パス目：テスト銘柄の決算タイムスタンプをスキャン中...")
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
            time.sleep(0.5)
        except Exception:
            continue

    sorted_quarters = sorted(list(detected_quarters))
    print(f"📈 検出されたクォーター列: {sorted_quarters}")

    # =========================================================================
    # 🌟 2パス目：動的なヘッダー構造を確定させて、マッピングを実行
    # =========================================================================
    base_headers = ['Theme', 'Ticker', 'Company_Name', 'Value_Chain', 'Market_Cap_M', 'Volume_Ratio']
    tail_headers = ['RD_Ratio', 'Cash_Runway', 'Backlog_Amount', 'Backlog_Source_Text', 'Last_Updated']
    
    final_headers = base_headers + [f"Rev_YoY_{q}" for q in sorted_quarters] + tail_headers
    
    updated_elite_rows = []
    current_date = time.strftime("%Y-%m-%d")
    
    print("🔄 2パス目：詳細データのマッピングとGemini解析を実行中...")
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker or ticker not in stock_raw_financials: continue
        
        stock, q_financials, q_balance = stock_raw_financials[ticker]
        
        # AI記憶の再利用判定（防衛ライン）
        has_past_data = ticker in existing_vc_map and existing_vc_map[ticker] != "" and "4_General" not in existing_vc_map[ticker]
        if has_past_data:
            vc_layer = existing_vc_map[ticker]
            backlog_val = existing_backlog_map.get(ticker, "N/A")
            backlog_source = existing_source_map.get(ticker, "N/A")
            print(f" ➔ {ticker}: 既存のAI解析結果を再利用（API消費ゼロ）")
        else:
            print(f" ➔ 💡 {ticker}: 新規マイニング発動。10-Q/10-Kをクローリング中...")
            sec_text = fetch_sec_clean_context(ticker)
            ai_res = ask_gemini_sec_analysis(ticker, item.get("Business_Summary", ""), sec_text)
            
            vc_layer = ai_res.get("Value_Chain", "4_General")
            backlog_val = ai_res.get("backlog", "N/A")
            backlog_source = ai_res.get("backlog_source_text", "N/A")
            print(f" ➔ Gemini確定 [VC: {vc_layer} | Backlog: {backlog_val}]")
            time.sleep(0.5)

        # 財務計算
        try:
            history = stock.history(period="30d")
            volume_ratio = round(history['Volume'].iloc[-1] / history['Volume'].mean(), 2) if len(history) >= 2 else 1.0
            
            rev_idx = [i for i in q_financials.index if "Revenue" in i]
            rd_idx = [i for i in q_financials.index if "Research" in i or "R&D" in i]
            net_inc_idx = [i for i in q_financials.index if "Net Income" in i]
            cash_idx = [i for i in q_balance.index if "Cash And Cash Equivalents" in i or "Cash" in i]
            
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
                        if col_key in ticker_q_values and not os.sys.math.isnan(val):
                            ticker_q_values[col_key] = f"{round(val * 100, 1)}%"

            rd_ratio_str, cash_runway = "N/A", "N/A"
            if rev_idx and rd_idx:
                latest_rev = q_financials.loc[rev_idx[0]].iloc[0]
                latest_rd = q_financials.loc[rd_idx[0]].iloc[0]
                if latest_rev > 0 and not os.sys.math.isnan(latest_rd):
                    rd_ratio_str = f"{round((abs(latest_rd) / latest_rev) * 100, 1)}%"

            if cash_idx and net_inc_idx:
                latest_cash = q_balance.loc[cash_idx[0]].iloc[0]
                latest_loss = q_financials.loc[net_inc_idx[0]].iloc[0]
                if latest_loss < 0 and not os.sys.math.isnan(latest_cash):
                    cash_runway = f"{round(abs(latest_cash) / abs(latest_loss), 1)} Q"
                elif latest_loss >= 0:
                    cash_runway = "Black (黒字)"
        except Exception:
            volume_ratio, rd_ratio_str, cash_runway = 1.0, "Error", "Error"
            ticker_q_values = {f"Rev_YoY_{q}": "" for q in sorted_quarters}

        # 行データの組み立て
        q_row_parts = [ticker_q_values[f"Rev_YoY_{q}"] for q in sorted_quarters]
        
        row = [
            item.get("Theme"), ticker, item.get("Company_Name"), vc_layer, item.get("Market_Cap_M"), volume_ratio
        ] + q_row_parts + [
            rd_ratio_str, cash_runway, backlog_val, backlog_source, current_date
        ]
        updated_elite_rows.append(row)
        time.sleep(0.5)

    # 3. シートへの最終書き込み
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        elite_ws = sh.add_worksheet(title=ELITE_SHEET_NAME, rows="1000", cols=str(len(final_headers)))
        
    elite_ws.update(range_name='A1', values=[final_headers])
    if updated_elite_rows:
        elite_ws.append_rows(updated_elite_rows)
    print(f"🎉 テスト完了。先頭10銘柄での Elite_Watchlist 生成が正常終了しました！")

if __name__ == "__main__":
    main()
