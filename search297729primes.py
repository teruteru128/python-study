#!/usr/bin/env python3

import gmpy2

def search_large_range(start_n, end_n):
    print(f"探索開始: n={start_n} から n={end_n-1} まで")
    constant_part = 268000
    
    for n in range(start_n, end_n):
        # 巨大整数の計算
        # val = (268000 * 10^n - 439) / 9
        val = (constant_part * pow(10, n) - 439) // 9
        
        # 高速な確率的素数判定
        if gmpy2.is_prime(val):
            print(f"\n[発見] n = {n}")
            print(f"桁数: {len(str(val))} 桁")
            # 巨大すぎるため、値そのものの出力は慎重に
        
        # 1000件ごとに進捗を表示
        if n % 1000 == 0:
            print(f".", end="", flush=True)

# 1001以上 100000未満の探索
search_large_range(1001, 100000)

