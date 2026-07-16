import subprocess
import os
import time
import requests
import random
import argparse
import sqlite3

# === 設定項目 ===
CORES = 8                                              # MPIで使用する物理コア数
MIN_DIGITS = 3184                                      # 対象の最小桁数
CM_ECPP_PATH = "/usr/local/cm-0.4.4/bin/ecpp-mpi"      # cm-ecppのコマンドパス
DB_FILE = "factordb_tasks.db"                         # データベースファイル名
# ===============

# データベースの初期化
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # タスク管理兼ログテーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            serial_num INTEGER PRIMARY KEY,
            prp_number TEXT NOT NULL,
            digits INTEGER NOT NULL,
            status TEXT NOT NULL,          -- 'running', 'completed', 'failed'
            elapsed_seconds REAL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# 未完了（中断された）タスクを取得する
def get_interrupted_task():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT serial_num, prp_number, digits FROM tasks WHERE status = 'running' LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row

# タスクを新規登録、または更新する
def save_task(serial_num, prp_number, digits, status, elapsed=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (serial_num, prp_number, digits, status, elapsed_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(serial_num) DO UPDATE SET
            status = excluded.status,
            elapsed_seconds = excluded.elapsed_seconds,
            updated_at = excluded.updated_at
    ''', (serial_num, prp_number, digits, status, elapsed))
    conn.commit()
    conn.close()

# 最新の連番を取得する（引数がない場合のデフォルト用）
def get_latest_serial_num():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(serial_num) FROM tasks")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] is not None else None

# Factordbから1件だけPRPを取得する
def get_single_prp(min_dig):
    random_start = random.randint(0, 20)
    url = f"https://factordb.com/listtype.php?t=1&mindig={min_dig}&perpage=1&start={random_start}&download=1"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            if lines and lines[0].isdigit():
                return lines[0]
    except Exception as e:
        print(f"[Error] ダウンロード失敗: {e}")
    return None

# Factordbに証明結果をアップロードする
def upload_proof(cert_content):
    url = "https://factordb.com/uploadcert.php"
    payload = {'cert': cert_content}
    session_id = os.environ.get("FDB_SESSION_ID")
    cookies = {'fdbuser': session_id} if session_id else {}
    if not session_id:
        print("[Warning] 環境変数 FDB_SESSION_ID が設定されていません。匿名として送信します。")
    
    try:
        response = requests.post(url, data=payload, cookies=cookies, timeout=20)
        if response.status_code == 200:
            print("[Success] factordbへのアップロードに成功しました。")
            return True
        else:
            print(f"[Warning] アップロードのステータスコードが異常です: {response.status_code}")
    except Exception as e:
        print(f"[Error] アップロード失敗: {e}")
    return False

def main():
    init_db()
    
    # コマンドライン引数の解析
    parser = argparse.ArgumentParser(description="Factordb ECPP SQLite3 Automation Script")
    parser.add_argument('--start-num', type=int, help="新規開始時の連番。指定がない場合はDBの続きから自動再開します。")
    args = parser.parse_args()
    
    print("Factordb ECPP 自動化タスク（SQLite3管理版）を開始します。")
    
    # 1. まず前回中断されたタスク（status='running'）がないか確認
    interrupted = get_interrupted_task()
    
    if interrupted:
        current_num, prp, digits = interrupted
        # 中断タスクがあっても、それに対応するチェックポイントファイル(.cert1 / .cert2)があるか確認
        expected_prefix = f"{current_num}-cert{digits}"
        if os.path.exists(f"{expected_prefix}.cert1") or os.path.exists(f"{expected_prefix}.cert2"):
            print(f"\n[★レジューム] 前回の未完了タスクをDBから復元しました。連番: {current_num} ({digits}桁)")
            goto_calc = True
        else:
            print(f"\n[Warning] DB上は連番 {current_num} が実行中ですが、CMのチェックポイントファイルが見つかりません。")
            print("安全のため、この連番のステータスを 'failed' に変更して次へ進みます。")
            save_task(current_num, prp, digits, 'failed')
            goto_calc = False
    else:
        goto_calc = False

    # 2. 中断タスクがない場合、次の連番を決定する
    if not goto_calc:
        if args.start_num is not None:
            current_num = args.start_num
        else:
            latest_db_num = get_latest_serial_num()
            if latest_db_num is not None:
                current_num = latest_db_num + 1
                print(f"[Info] DBの履歴から自動的に次の連番 {current_num} を選択しました。")
            else:
                current_num = 5800
                print(f"[Info] 履歴がありません。デフォルトの連番 {current_num} から開始します。")

    while True:
        # 新しい数をダウンロードするフェーズ
        if not goto_calc:
            print(f"\n--- {MIN_DIGITS}桁以上のPRPを1件取得中 (startランダム) ---")
            prp = get_single_prp(MIN_DIGITS)
            
            if not prp:
                print("対象のPRPが見つからないか、エラーが発生しました。30秒後に再試行します。")
                time.sleep(30)
                continue
                
            digits = len(prp)
            print(f"ターゲットを取得しました（{digits} 桁）")
            
            # 計算開始前にステータスを 'running' としてDBに記録
            save_task(current_num, prp, digits, 'running')
        
        # 2回目以降のループは通常通りダウンロードを行わせる
        goto_calc = False
        
        # 出力ファイル名
        output_file = f"{current_num}-cert{digits}"
        
        # CM (MPI版) の実行
        cmd = [
            "mpirun", "-np", str(CORES), CM_ECPP_PATH, 
            "-c", "-g", "-t", "-f", output_file, "-n", prp
        ]
        
        print(f"ECPP-MPIを実行中... (ファイル名: {output_file}, コア数: {CORES})")
        start_time = time.time()
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_time
        
        print(f"計算終了。所要時間: {elapsed:.2f} 秒")
        
        # 証明書ファイル（.primo）の確認とアップロード
        cert_file = f"{output_file}.primo"
        
        if os.path.exists(cert_file):
            with open(cert_file, "r") as f:
                cert_content = f.read()
                
            print("証明書を factordb に送信しています...")
            upload_proof(cert_content)
            
            # DBのステータスを 'completed'（完了）に更新し、かかった時間を記録
            save_task(current_num, prp, digits, 'completed', elapsed)
            
            # CMの中間チェックポイントファイルを掃除（本家仕様通り、成功後は不要なため）
            for suffix in [".cert1", ".cert2"]:
                if os.path.exists(f"{output_file}{suffix}"):
                    os.remove(f"{output_file}{suffix}")
                
            # 次の連番へ
            current_num += 1
        else:
            print(f"[Error] 証明書ファイル ({cert_file}) が生成されませんでした。")
            # エラー時はDBのステータスを 'failed' にして、ループを抜けて終了します
            save_task(current_num, prp, digits, 'failed', elapsed)
            print("--- STDOUT ---")
            print(result.stdout)
            print("--- STDERR ---")
            print(result.stderr)
            break

        print("次のタスクまで10秒待機します...")
        time.sleep(10)

if __name__ == "__main__":
    main()

