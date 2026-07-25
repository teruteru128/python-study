import hashlib
import os


class ManualEntropyPool:
    def __init__(self, pool_path="entropy_pool.bin"):
        self.pool_path = pool_path
        self.pool_size = 32  # 256ビット (32バイト)
        self._load_pool()

    def _load_pool(self):
        """プールを読み込む。存在しない場合はOSの乱数で初期化"""
        if os.path.exists(self.pool_path):
            with open(self.pool_path, "rb") as f:
                self.state = f.read()
        else:
            # 初回のみ、空だとハッシュの安全性が低いためOSの乱数で基礎を作る
            self.state = os.urandom(self.pool_size)
            self._save_pool()

    def _save_pool(self):
        """現在のプール状態をファイルに保存"""
        with open(self.pool_path, "wb") as f:
            f.write(self.state)

    def feed_dice(self, dice_str):
        """サイコロの出目を6進数の数値としてプールに混ぜる（撹拌）"""
        # 1〜6以外の文字を除外し、0〜5の数値に変換
        digits = [int(c) - 1 for c in dice_str if c in "123456"]
        if not digits:
            return 0

        # 6進数から1つの巨大な整数に変換
        big_int = 0
        for d in digits:
            big_int = big_int * 6 + d

        # 整数をバイト列に変換
        byte_len = (big_int.bit_length() + 7) // 8 if big_int > 0 else 1
        dice_bytes = big_int.to_bytes(byte_len, byteorder="big")

        # 【撹拌】現在の状態 + サイコロの入力 をハッシュ化して新しい状態にする
        hasher = hashlib.sha256()
        hasher.update(self.state)
        hasher.update(dice_bytes)
        self.state = hasher.digest()
        self._save_pool()

        return len(digits)

    def extract_key(self):
        """プールから安全な256ビット乱数を取り出し、プールを自己更新する"""
        # 1. 現在のプール状態から鍵を生成（役割を分離するためソルトを付与）
        key_hasher = hashlib.sha256()
        key_hasher.update(self.state)
        key_hasher.update(b"EXTRACT_KEY")
        extracted_key = key_hasher.digest()

        # 2. 【前方向セキュリティ】プールを次の状態へ不可逆更新
        # これにより、この後にプールが盗まれても、今出力した鍵は逆算できない
        next_hasher = hashlib.sha256()
        next_hasher.update(self.state)
        next_hasher.update(b"NEXT_STATE")
        self.state = next_hasher.digest()
        self._save_pool()

        return extracted_key


# --- 使い方（UIエミュレーション） ---
if __name__ == "__main__":
    pool = ManualEntropyPool()

    print("1: サイコロの目を追加してプールを育てる")
    print("2: 256ビット乱数（鍵）を切り出す")
    choice = input("選択してください (1/2): ")

    if choice == "1":
        dice = input("サイコロの出目を入力（例: 351624）: ")
        count = pool.feed_dice(dice)
        print(f"成功: {count}個の出目をプールに撹拌しました。")
    elif choice == "2":
        key = pool.extract_key()
        print(f"生成された256ビット乱数 (HEX):\n{key.hex()}")
