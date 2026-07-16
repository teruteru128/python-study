import subprocess
import re
import os
import time
import requests

# === 設定項目 ===
CORES = 8                     # MPIで使用する物理コア数
MIN_DIGITS = 3184             # 対象の最小桁数
MAX_DIGITS = 3200             # 対象の最大桁数
CM_ECPP_PATH = "/usr/local/cm-0.4.4/bin/ecpp-mpi"      # cm-ecppのコマンドパス（環境に合わせて変更）
# ===============

# Factordbから1件だけPRPを取得する関数
def get_single_prp(min_dig, max_dig):
    # `max_dig`パラメータは該当するURLパラメータがないため後で削除する
    # バッティングを最小限にするため perpage=1 で1件だけ取得
    # "https://factordb.com/listtype.php?t=1&mindig=3184&perpage=30&start=0"
    url = f"https://factordb.com/listtype.php?t=1&mindig={min_dig}&perpage=1&start=0&download=1"
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
def upload_proof(number, cert_content):
    # 通常、factordbへの証明書報告は 'uploadcert.php' を使用します
    url = "https://factordb.com/uploadcert.php"
    
    # フォームデータ。CMの出力形式（.cert）を送信
    payload = {
        'report': cert_content
    }
    
    try:
        # 実際にはログインCookie（セッション）が必要な場合があるため、
        # 失敗する場合はブラウザのCookie（PHPSESSIDなど）をheadersに追加してください
        # セッションIDは環境変数`FDB_SESSION_ID`から取得しcookie キー`fdbuser`にセットしてください
        response = requests.post(url, data=payload, timeout=20)
        if response.status_code == 200:
            print("[Success] factordbへのアップロードに成功しました。")
            return True
        else:
            print(f"[Warning] アップロードのステータスコードが異常です: {response.status_code}")
    except Exception as e:
        print(f"[Error] アップロード失敗: {e}")
    return False

def main():
    print("Factordb ECPP 自動化タスクを開始します。")
    
    while True:
        print(f"\n--- {MIN_DIGITS}桁付近のPRPを1件取得中 ---")
        prp = get_single_prp(MIN_DIGITS, MAX_DIGITS)
        
        if not prp:
            print("対象のPRPが見つからないか、エラーが発生しました。30秒後に再試行します。")
            time.sleep(30)
            continue
            
        print(f"ターゲットを取得しました（{len(prp)} 桁）")
        
        # 1. ターゲットをファイルに保存
        input_file = "current_prp.txt"
        with open(input_file, "w") as f:
            f.write(prp)
            
        # 2. CM (MPI版) の実行
        # コマンド例: mpirun -np 4 cm-ecpp current_prp.txt
        # ※CMの引数や出力ファイルの仕様（デフォルトで input_file.cert など）に合わせて調整してください
        # `output_file`は `<連番>-cert<len(prp)>`で。連番部分は現在5800番代を使っているのでコマンドライン引数でオプション指定できると良し。
        cmd = ["mpirun", "-np", str(CORES), CM_ECPP_PATH, "-c", "-g", "-t", "-f", output_file, "-n", prp]
        
        print(f"ECPP-MPIを実行中... (MPI コア数: {CORES})")
        start_time = time.time()
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_time
        
        print(f"計算終了。所要時間: {elapsed:.2f} 秒")
        
        # 3. 証明書ファイルの確認とアップロード
        # CMの出力ファイル名が 'current_prp.txt.cert' だと仮定
        # ecpp-mpiコマンドによって対象の証明書ファイルが "<output_file>.primo"に出力されるのでそれをアップロード
        cert_file = "current_prp.txt.cert" 
        
        if os.path.exists(cert_file):
            with open(cert_file, "r") as f:
                cert_content = f.read()
                
            print("証明書を factordb に送信しています...")
            upload_proof(prp, cert_content)
            
            # 使用済みのファイルを削除（あるいは別フォルダへ退避）
            os.remove(input_file)
            os.remove(cert_file)
        else:
            print("[Error] 証明書ファイルが生成されませんでした。CMの出力を確認してください。")
            print(result.stderr)
            break # エラー時はループを止めて確認できるようにする

        # 他の作業者への配慮とマシン冷却のための短いウェイト
        print("次のタスクまで10秒待機します...")
        time.sleep(10)

if __name__ == "__main__":
    main()

