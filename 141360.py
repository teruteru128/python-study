import sys
from sympy import cyclotomic_poly, Symbol

sys.set_int_max_str_digits(100000)

# 指数と底数の設定
n = 141360
base = 6

# SymPy用の数式変数 'x' を定義
x = Symbol('x')

# 141360の約数をすべて列挙
divisors = sorted([d for d in range(1, n + 1) if n % d == 0])

print(f"計算を開始します。全 {len(divisors)} 個のパーツをファイルに保存します。")

# 各約数に対応する円分多項式に x=6 を代入して計算
with open("factordb_inputs.txt", "w") as f:
    # 最初に共通因数の「6」を書き出す
    f.write(f"{base}\n")
    
    # 80個のパーツを順に計算して書き出し
    for i, d in enumerate(divisors, 1):
        print(f"計算中... {i:2d}/80 (約数: {d:6d})")
        
        # 円分多項式 Φ_d(x) を取得
        poly = cyclotomic_poly(d, x)
        
        # 【修正箇所】 .eval() ではなく .subs(x, base) で値を代入
        val = poly.subs(x, base)
        
        # ファイルに保存
        f.write(f"{val}\n")

print("\nすべての計算が完了しました！")
print("プロジェクトフォルダ内に 'factordb_inputs.txt' が作成されています。")

