import os
import json
import time
import requests
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types

# =========================================================================
# ⚙️ 設定値
# =========================================================================
SPREADSHEET_ID = "あなたのスプレッドシートID"
MASTER_SHEET_NAME = "Master_Watchlist"
ELITE_SHEET_NAME = "Elite_Watchlist"

SEC_HEADERS = {"User-Agent": "YourName yourname@example.com"}

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

def fetch_sec_raw_text(ticker):
    """SEC EDGARから対象企業の直近の【10-Q】または【10-K】テキストを取得"""
    try:
        cik_url = f"https://data.sec.gov/submissions/CIK{ticker.zfill(10)}.json"
        res = requests.get(cik_url, headers=SEC_HEADERS)
        if res.status_code != 200: return ""
        
        data = res.json()
        recent_filings = data.get("filings", {}).get("recent", {})
        
        # 直近の提出書類を上から順にスキャン
        for i, form_type in enumerate(recent_filings.get("form", [])):
            # 🌟 10-Q（四半期）または 10-K（通期）のいずれか最新のものをターゲットにする
            if form_type in ["10-Q", "10-K"]:
                accession = recent_filings.get("accessionNumber", [])[i].replace("-", "")
                doc_name = recent_filings.get("primaryDocument", [])[i]
                
                text_url = f"https://www.sec.gov/Archives/edgar/data/{data['cik']}/{accession}/{doc_name}"
                raw_html = requests.get(text_url, headers=SEC_HEADERS).text
                
                # 💡 次のステップで、この raw_html に BeautifulSoup の前処理を噛ませます
                return raw_html
        return ""
    except Exception:
        return ""

def ask_gemini_sec_analysis(ticker, summary, sec_text):
    """Gemini APIによるバリューチェーン仕分け・バックログ（根拠テキスト付）の超高精度マイニング"""
    api_key = os.environ.get("GEMINI_API_KEY")
    default_res = {"Value_Chain": "4_General", "backlog": "N/A", "backlog_source_text": "N/A"}
    if not api_key: 
        return default_res
        
    client = genai.Client(api_key=api_key)
    
    # yfinance(事業概要)とSEC(10-Q/10-K)のインプットをドッキング
    input_context = f"--- Business Summary (yfinance) ---\n{summary}\n\n--- SEC 10-Q/10-K Filing Text ---\n{sec_text}"
    
    prompt = f"""
【背景・役割】
あなたは冷静沈着で妥協を許さないシニア財務アナリストです。
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

def main():
    gc = get_google_sheets_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    
    # 1. データの事前読込
    master_ws = sh.worksheet(MASTER_SHEET_NAME)
    master_records = master_ws.get_all_records()
    
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_data = elite_ws.get_all_records()
    except gspread.exceptions.WorksheetNotFound:
        elite_data = []

    existing_vc_map = {r['Ticker']: r['Value_Chain'] for r in elite_data if 'Ticker' in r}
    existing_backlog_map = {r['Ticker']: r['Backlog_Amount'] for r in elite_data if 'Ticker' in r}
    existing_schedule_map = {r['Ticker']: r['Upcoming_Schedule'] for r in elite_data if 'Ticker' in r}

    # =========================================================================
    # 🌟 1パス目：全銘柄を巡回して「存在する全クォーター名」を冷酷に洗い出す
    # =========================================================================
    print("🔄 1パス目：全銘柄の決算タイムスタンプをスキャン中...")
    detected_quarters = set()
    stock_raw_financials = {} # 2パス目のためにデータをキャッシュして通信を節約
    
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker: continue
        try:
            stock = yf.Ticker(ticker)
            q_financials = stock.quarterly_financials
            q_balance = stock.quarterly_balance_sheet
            
            # キャッシュに保存
            stock_raw_financials[ticker] = (stock, q_financials, q_balance)
            
            rev_idx = [i for i in q_financials.index if "Revenue" in i]
            if rev_idx:
                timestamps = q_financials.loc[rev_idx[0]].dropna().index
                for ts in timestamps[:3]: # 直近3期分をチェック
                    lbl = get_quarter_label(ts)
                    if lbl: detected_quarters.add(lbl)
            time.sleep(0.5)
        except Exception:
            continue

    # 時系列順（古い順）にソートされたクォーター列リストを生成
    sorted_quarters = sorted(list(detected_quarters))
    print(f"📈 検出されたクォーター列: {sorted_quarters}")

    # =========================================================================
    # 🌟 2パス目：動的なヘッダー構造を確定させて、マッピングを実行
    # =========================================================================
    base_headers = ['Theme', 'Ticker', 'Company_Name', 'Value_Chain', 'Market_Cap_M', 'Volume_Ratio']
    tail_headers = ['RD_Ratio', 'Cash_Runway', 'Backlog_Amount', 'Upcoming_Schedule', 'Last_Updated']
    
    # 動的にクォーター列を中間に挟み込む
    final_headers = base_headers + [f"Rev_YoY_{q}" for q in sorted_quarters] + tail_headers
    
    updated_elite_rows = []
    current_date = time.strftime("%Y-%m-%d")
    
    print("🔄 2パス目：詳細データのマッピングとGemini解析を実行中...")
    for item in master_records:
        ticker = item.get("Ticker")
        if not ticker or ticker not in stock_raw_financials: continue
        
        stock, q_financials, q_balance = stock_raw_financials[ticker]
        
        # AI記憶の再利用判定
        has_past_data = ticker in existing_vc_map and existing_vc_map[ticker] != "" and "4_General" not in existing_vc_map[ticker]
        if has_past_data:
            vc_layer = existing_vc_map[ticker]
            backlog_val = existing_backlog_map.get(ticker, "N/A")
            schedule_val = existing_schedule_map.get(ticker, "N/A")
        else:
            sec_text = fetch_sec_raw_text(ticker)
            ai_res = ask_gemini_sec_analysis(ticker, item.get("Business_Summary", ""), sec_text)
            vc_layer = ai_res.get("vc", "4_General")
            backlog_val = ai_res.get("backlog", "N/A")
            schedule_val = ai_res.get("schedule", "N/A")
            time.sleep(0.5)

        # 財務計算
        try:
            history = stock.history(period="30d")
            volume_ratio = round(history['Volume'].iloc[-1] / history['Volume'].mean(), 2) if len(history) >= 2 else 1.0
            
            rev_idx = [i for i in q_financials.index if "Revenue" in i]
            rd_idx = [i for i in q_financials.index if "Research" in i or "R&D" in i]
            net_inc_idx = [i for i in q_financials.index if "Net Income" in i]
            cash_idx = [i for i in q_balance.index if "Cash And Cash Equivalents" in i or "Cash" in i]
            
            # クォーターごとの数値を格納する辞書を初期化（デフォルトは空欄 ""）
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

        # 行データの組み立て（動的クォーター部分を辞書からリスト化して結合）
        q_row_parts = [ticker_q_values[f"Rev_YoY_{q}"] for q in sorted_quarters]
        
        row = [
            item.get("Theme"), ticker, item.get("Company_Name"), vc_layer, item.get("Market_Cap_M"), volume_ratio
        ] + q_row_parts + [
            rd_ratio_str, cash_runway, backlog_val, schedule_val, current_date
        ]
        updated_elite_rows.append(row)
        time.sleep(0.5)

    # 3. シートへの最終書き込み（列数をヘッダーに合わせて自動再生成）
    try:
        elite_ws = sh.worksheet(ELITE_SHEET_NAME)
        elite_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        elite_ws = sh.add_worksheet(title=ELITE_SHEET_NAME, rows="1000", cols=str(len(final_headers)))
        
    elite_ws.update(range_name='A1', values=[final_headers])
    if updated_elite_rows:
        elite_ws.append_rows(updated_elite_rows)
    print("🎉 完璧です。動的列管理によるElite_Watchlistの上書きが完了しました！")

if __name__ == "__main__":
    main()
