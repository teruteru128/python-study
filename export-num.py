#!/usr/bin/env python3
import base64
import sys
import math

def bytes_to_int(b):
    """バイト列を高速に整数に変換（1秒未満）"""
    return int.from_bytes(b, byteorder='big')

def parse_huge_rsa_pem(pem_path):
    """PEMファイルからpとqをバイナリのまま超高速抽出する"""
    print(f"[*] 鍵ファイル '{pem_path}' を読み込み中...")
    with open(pem_path, 'r') as f:
        lines = f.readlines()

    # PEMのヘッダーとフッターを除いたBase64部分を結合
    b64_data = "".join([l.strip() for l in lines if "---" not in l])
    der_data = base64.b64decode(b64_data)

    # ASN.1 (DER) の極めてシンプルな手動パース（200万ビット対応）
    # RSA秘密鍵 (RFC 3447) の構造: SEQUENCE { version, n, e, d, p, q, ... }
    # 巨大なデータサイズに対応するため、簡易的な長さ（Length）デコーダを実装
    idx = 0
    def read_asn1_length(data, pos):
        if pos >= len(data): return 0, pos
        b = data[pos]
        pos += 1
        if b < 128:
            return b, pos
        else:
            n_bytes = b & 0x7f
            length = int.from_bytes(data[pos:pos+n_bytes], byteorder='big')
            return length, pos + n_bytes

    # 1. 最初の SEQUENCE タグをスキップ
    if der_data[idx] != 0x30:
        raise ValueError("無効なRSA秘密鍵フォーマットです。")
    _, idx = read_asn1_length(der_data, idx + 1)

    # 2. 各要素を順番に読み込む
    elements = []
    # 必要なのは p (5番目) と q (6番目) までなので、最大6回パース
    for i in range(6):
        if idx >= len(der_data): break
        if der_data[idx] != 0x02: # INTEGER タグ
            raise ValueError(f"パースエラー: 要素 {i} が整数ではありません。")
        length, idx = read_asn1_length(der_data, idx + 1)
        value_bytes = der_data[idx:idx+length]
        idx += length
        elements.append(value_bytes)

    if len(elements) < 6:
        raise ValueError("鍵ファイルに p または q が含まれていません。公開鍵、または暗号化された鍵の可能性があります。")

    # elements[0]: version, [1]: n, [2]: e, [3]: d, [4]: p, [5]: q
    p = bytes_to_int(elements[4])
    q = bytes_to_int(elements[5])
    return p, q

def run_security_check(p, q):
    """安全性の検証を実行"""
    print("\n=== 200万ビット級 RSA鍵 安全性検証結果 ===")

    p_bits = p.bit_length()
    q_bits = q.bit_length()
    print(f"[-] 素数pの長さ: {p_bits:,} ビット")
    print(f"[-] 素数qの長さ: {q_bits:,} ビット")

    # 1. フェルマー法への耐性 (p と q の差の検証)
    diff = abs(p - q)
    diff_bits = diff.bit_length()
    print(f"[-] p と q の差の長さ: {diff_bits:,} ビット")
    if diff_bits < (max(p_bits, q_bits) // 2):
        print(" -> 【危険】p と q の値が近すぎます！フェルマー法で瞬時に破られます。")
    else:
        print(" -> 【安全】p と q の値は十分に離れています。")

    # 2. p-1, q-1 の簡易滑らかさチェック (ポラード法への耐性)
    print("[-] p-1, q-1 の滑らかさ検証（低階層）")
    for name, val in [("p-1", p-1), ("q-1", q-1)]:
        temp = val
        # 小さな素数因数を削る（高速処理のため1000まで）
        for i in range(2, 1000):
            while temp % i == 0:
                temp //= i
        reduced_bits = temp.bit_length()
        print(f" -> {name} から小さな因数を除いた残りの長さ: {reduced_bits:,} ビット")
        if reduced_bits < (val.bit_length() * 0.5):
            print(f"    【警告】{name} が滑らかな数の可能性が高く、ポラード法に脆弱な恐れがあります。")
        else:
            print(f"    【安全】{name} は小さな因数だけで構成されていません。")

if __name__ == "__main__":
    # 💡 あなたのRSA秘密鍵ファイル（PEM形式）のパスを指定してください
    # ※暗号化されていない（パスフレーズなしの）秘密鍵である必要があります。
    KEY_FILE_PATH = "../private_key.pem"

    try:
        p, q = parse_huge_rsa_pem(KEY_FILE_PATH)
        run_security_check(p, q)
    except Exception as e:
        print(f"\n[エラー] 処理に失敗しました: {e}")
