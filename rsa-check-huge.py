#!/usr/bin/env python3
import mmap
import os
import struct

def check_with_raw_bitmap(p, q, bitmap_path):
    print(f"\n[*] 生形式素数リスト '{bitmap_path}' を読み込み中...")
    
    targets = {"p-1": p - 1, "q-1": q - 1}
    file_size = os.path.getsize(bitmap_path)
    
    with open(bitmap_path, "rb") as f:
        with mmap.mmap(f.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            
            # 1. 先頭の8バイトからlong配列の長さを取得
            array_length = struct.unpack(">Q", mm[0:8])[0]
            print(f"[-] ファイル内記録の配列長: {array_length:,} 個")
            
            # データ領域は9バイト目から開始
            header_offset = 8
            
            # 実際のファイルサイズと、記録されている長さが一致するか一応検証
            expected_size = header_offset + (array_length * 8)
            if file_size < expected_size:
                print(f"【警告】ファイルサイズが足りません。途中で切れている可能性があります。")
                # 安全のため、実際のファイルサイズに合わせてループ回数を調整
                array_length = (file_size - header_offset) // 8
            
            print(f"[*] 2,748億以下の素数（全 {array_length * 64:,} ビット）に対する検証を開始...")
            
            # 進捗表示用
            percent_milestone = max(1, array_length // 10)
            
            # 2. ビットセットを順にスキャン
            for block_idx in range(array_length):
                
                # 10%ごとに進捗を表示
                if block_idx % percent_milestone == 0 and block_idx > 0:
                    print(f" -> 進捗: {int(block_idx / array_length * 100)}% 完了")
                
                pos = header_offset + (block_idx * 8)
                block_bytes = mm[pos : pos + 8]
                
                # 64bit Big Endian を整数に変換
                word = struct.unpack(">Q", block_bytes)[0]
                if word == 0:
                    continue  # 素数フラグが1つも無ければスキップ
                
                # 64ビットの各ビットを検査
                for bit_idx in range(64):
                    if (word >> bit_idx) & 1:
                        # 0番目のビット = 3, 1番目 = 5, ...
                        prime = 2 * (block_idx * 64 + bit_idx) + 3
                        
                        # p-1, q-1 を割れるだけ割る
                        for name in targets:
                            while targets[name] % prime == 0:
                                targets[name] //= prime

    print("\n=== 2,748億範囲・生形式最終検証結果 ===")
    for name, final_val in targets.items():
        original_bits = (p - 1).bit_length() if name == "p-1" else (q - 1).bit_length()
        reduced_bits = final_val.bit_length()
        print(f"[-] {name} の最終残り長さ: {reduced_bits:,} / {original_bits:,} ビット")
        if reduced_bits < (original_bits * 0.9):
            print(f"    【警告】{name} から多くの中規模因数が見つかりました。ポラード法への耐性が不安です。")
        else:
            print(f"    【安全】{name} は2,748億以下の範囲に危険な因数を【一切持っていません】！")


# 呼び出し例:
# p, q = parse_huge_rsa_p8_pem("../private_key.pem")
# check_with_raw_bitmap(p, q, "生形式ファイルのパス.bin")
if __name__ == '__main__':
    pass
