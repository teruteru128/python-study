import base64
from datetime import datetime, timezone, timedelta

# Xのカスタムエポック（変更なし）
CUSTOM_EPOCH = 1288834974657
# 今回判明した後半の未知のフィールド長
SHIFT_BITS = 46

def x_90bit_decode(filename):
    """ファイル名から時刻情報（JST）を取り出す"""
    padding = '=' * ((4 - len(filename) % 4) % 4)
    raw_bytes = base64.urlsafe_b64decode(filename + padding)
    total_value = int.from_bytes(raw_bytes, byteorder='big')
    
    # 46ビット右シフトして時刻フィールド（上位42ビット）を取り出す
    timestamp_ms = total_value >> SHIFT_BITS
    epoch_ms = timestamp_ms + CUSTOM_EPOCH
    
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    dt_jst = dt.astimezone(timezone(timedelta(hours=9)))
    
    print(f"--- デコード結果 ---")
    print(f"エポックミリ秒: {epoch_ms}")
    print(f"アップロード日時: {dt_jst.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} (JST)")
    return epoch_ms

def x_90bit_encode(epoch_ms):
    """エポックミリ秒から時刻情報を埋め込んだ擬似ファイル名を生成する"""
    timestamp_ms = epoch_ms - CUSTOM_EPOCH
    
    # 46ビット左シフトして上位に配置（後半46ビットは0で埋まる）
    total_value = timestamp_ms << SHIFT_BITS
    
    # 11バイト（88ビット）のバイナリに変換
    raw_bytes = total_value.to_bytes(11, byteorder='big')
    
    # URLセーフBase64エンコード（パディング無し）
    b64_str = base64.urlsafe_b64encode(raw_bytes).decode('utf-8').rstrip('=')
    
    print(f"--- エンコード結果 ---")
    print(f"生成されたファイル名: {b64_str}")
    return b64_str

# テスト実行
epoch = x_90bit_decode("HMAAAAAAMAEtxeb")
x_90bit_encode(epoch)

