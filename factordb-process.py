import subprocess
import os
import time
import requests
import random
import argparse

# === 設定項目 ===
CORES = 8                                              # MPIで使用する物理コア数
MIN_DIGITS = 3184                                      # 対象の最小桁数
CM_ECPP_PATH = "/usr/local/cm-0.4.4/bin/ecpp-mpi"      # cm-ecppのコマンドパス
# ===============

# Factordbから1件だけPRPを取得する関数
def get_single_prp(min_dig):
    # 他の作業者とのバッティングを最小限にするため、start位置を0〜20の間でランダムにずらす
    random_start = random.randint(0, 20)
    url = f"https://factordb.com/listtype.php?t=1&mindig={min_dig}&perpage=1&start={random_start}&download=1"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            if lines and lines[0].isdigit():
                return lines[0] # 1件目の数字を返す
    except Exception as e:
        print(f"[Error] ダウンロード失敗: {e}")
    return None

# Factordbに証明結果をアップロードする関数
def upload_proof(cert_content):
    url = "https://factordb.com/uploadcert.php"
    
    # フォームデータ。CMの出力形式（.primoの中身）を送信
    payload = {
        'cert': cert_content
    }
    
    # 環境変数からセッションIDを取得してCookieにセット
    session_id = os.environ.get("FDB_SESSION_ID")
    cookies = {}
    if session_id:
        cookies['fdbuser'] = session_id
    else:
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
    # コマンドライン引数で開始連番を指定できるようにする
    parser = argparse.ArgumentParser(description="Factordb ECPP Automation Script")
    parser.add_argument('--start-num', type=int, default=5800, help="出力ファイル名に使用する開始連番 (デフォルト: 5800)")
    args = parser.parse_args()
    
    current_num = args.start_num
    print("Factordb ECPP 自動化タスクを開始します。")
    print(f"現在の開始連番: {current_num}")
    
    while True:
        print(f"\n--- {MIN_DIGITS}桁以上のPRPを1件取得中 (startランダム) ---")
        prp = get_single_prp(MIN_DIGITS)
        
        if not prp:
            print("対象のPRPが見つからないか、エラーが発生しました。30秒後に再試行します。")
            time.sleep(30)
            continue
            
        digits = len(prp)
        print(f"ターゲットを取得しました（{digits} 桁）")
        
        # 出力ファイル名の決定 (<連番>-cert<桁数>)
        output_file = f"{current_num}-cert{digits}"
        
        # 1. CM (MPI版) の実行
        # -n で素数文字列を直接渡し、-f で出力ファイル名を指定
        cmd = [
            "mpirun", "-np", str(CORES), CM_ECPP_PATH, 
            "-c", "-g", "-t", "-f", output_file, "-n", prp
        ]
        
        print(f"ECPP-MPIを実行中... (ファイル名: {output_file}, コア数: {CORES})")
        start_time = time.time()
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_time
        
        print(f"計算終了。所要時間: {elapsed:.2f} 秒")
        
        # 2. 証明書ファイル（.primo）の確認とアップロード
        cert_file = f"{output_file}.primo"
        
        if os.path.exists(cert_file):
            with open(cert_file, "r") as f:
                cert_content = f.read()
                
            print("証明書を factordb に送信しています...")
            upload_proof(cert_content)
            
            # 使用済みの証明書ファイルを削除 (ログとして残したい場合はコメントアウトしてください)
            #try:
            #    os.remove(cert_file)
            #except Exception as e:
            #    print(f"[Warning] ファイルの削除に失敗しました: {e}")
                
            # 無事に完了したら連番をインクリメント
            current_num += 1
        else:
            print(f"[Error] 証明書ファイル ({cert_file}) が生成されませんでした。CMの出力を確認してください。")
            print("--- STDOUT ---")
            print(result.stdout)
            print("--- STDERR ---")
            print(result.stderr)
            break # 予期せぬエラー時はループを止めて確認できるようにする

        # 他の作業者への配慮とマシン冷却のためのウェイト
        print("次のタスクまで10秒待機します...")
        time.sleep(10)

if __name__ == "__main__":
    main()

