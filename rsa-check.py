#!/usr/bin/env python3
import base64
import sys

def bytes_to_int(b):
    return int.from_bytes(b, byteorder='big')

def parse_huge_rsa_p8_pem(pem_path):
    print(f"[*] PKCS#8 鍵ファイル '{pem_path}' を読み込み中...")
    with open(pem_path, 'r') as f:
        lines = f.readlines()
    
    # Base64部分を結合
    b64_data = "".join([l.strip() for l in lines if "---" not in l])
    der_data = base64.b64decode(b64_data)
    
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

    # 外側の SEQUENCE をスキップ
    if der_data[idx] != 0x30:
        raise ValueError("無効なフォーマットです。")
    _, idx = read_asn1_length(der_data, idx + 1)
    
    # 1. Version (INTEGER) をスキップ
    if der_data[idx] != 0x02: raise ValueError("パース失敗: Version")
    length, idx = read_asn1_length(der_data, idx + 1)
    idx += length # 通常は1バイト(0x00)
    
    # 2. rsaEncryption タグが含まれる AlgorithmIdentifier (SEQUENCE) をスキップ
    if der_data[idx] != 0x30: raise ValueError("パース失敗: AlgorithmIdentifier")
    length, idx = read_asn1_length(der_data, idx + 1)
    idx += length # 構造（rsaEncryptionのOID等）を丸ごとスキップ
    
    # 3. 実際の鍵データが格納されている PrivateKey (OCTET STRING) のタグをパース
    if der_data[idx] != 0x04: raise ValueError("パース失敗: PrivateKey OctetString")
    length, idx = read_asn1_length(der_data, idx + 1)
    
    # ここから内部の RSA Private Key (PKCS#1構造) が始まる
    if der_data[idx] != 0x30: raise ValueError("パース失敗: 内部のRSAPrivateKey SEQUENCE")
    _, idx = read_asn1_length(der_data, idx + 1)
    
    # 内部の各要素 (version, n, e, d, p, q) をパース
    elements = []
    for i in range(6):
        if idx >= len(der_data): break
        if der_data[idx] != 0x02: # INTEGER タグ
            raise ValueError(f"パースエラー: 要素 {i} が整数ではありません。")
        length, idx = read_asn1_length(der_data, idx + 1)
        value_bytes = der_data[idx:idx+length]
        idx += length
        elements.append(value_bytes)
        
    if len(elements) < 6:
        raise ValueError("鍵ファイルから p または q を抽出できませんでした。")
        
    p = bytes_to_int(elements[4])
    q = bytes_to_int(elements[5])
    return p, q

def run_security_check(p, q):
    print("\n=== 200万ビット級 RSA鍵 安全性検証結果 ===")
    p_bits = p.bit_length()
    q_bits = q.bit_length()
    print(f"[-] 素数pの長さ: {p_bits:,} ビット")
    print(f"[-] 素数qの長さ: {q_bits:,} ビット")
    
    # 1. フェルマー法への耐性
    diff = abs(p - q)
    diff_bits = diff.bit_length()
    print(f"[-] p と q の差の長さ: {diff_bits:,} ビット")
    if diff_bits < (max(p_bits, q_bits) // 2):
        print(" -> 【危険】p と q の値が近すぎます！フェルマー法で瞬時に破られます。")
    else:
        print(" -> 【安全】p と q の値は十分に離れています。")
        
    # 2. p-1, q-1 の簡易滑らかさチェック
    print("[-] p-1, q-1 の滑らかさ検証（低階層）")
    for name, val in [("p-1", p-1), ("q-1", q-1)]:
        temp = val
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
    # 💡 あなたのPKCS#8形式秘密鍵ファイル（PEM形式）のパスを指定してください
    KEY_FILE_PATH = "../private_key.pem"

    try:
        p, q = parse_huge_rsa_p8_pem(KEY_FILE_PATH)
        run_security_check(p, q)
    except Exception as e:
        print(f"\n[エラー] 処理に失敗しました: {e}")
