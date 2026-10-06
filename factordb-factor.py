#!/usr/bin/env python3
"""factordb の未分解合成数を yafu (SIQS) で分解して報告する自動化スクリプト。

factordb-process.py (ECPP の証明) と同じ作りにしてある:
  * タスクを SQLite で管理し、中断されたタスクは次回起動時に自動で再開する
    (yafu は siqs.dat から続きを計算できる)。
  * 連続して次の数へ進む。--once なら 1 件で終了。失敗したら止まる。
  * autoload.txt (カレントディレクトリ) があれば、そこに書いた数を先に処理する。

1 件の流れ:
  1. factordb の list_by_type から、指定桁数の未分解合成数 (表 C) をランダムに選び、
     api?id= で全桁を取得する (特殊形・小さい因子あり・素数らしいものは除く)。
  2. yafu に siqs(N) を実行させ、出力から素因数を取り出す。
  3. 積と素数性を検証する。
  4. factordb の現在の状態がまだ未分解 (C/CF) であることを再確認する。
  5. --upload のときだけ、JSON-RPC の report_factors で報告する。

安全のため、--upload を付けない限り factordb には何も書き込まない (ドライラン)。
ドライランは 1 件 (検証まで) で止まり、タスクは 'factored' として残る。
後から --report-pending --upload で、検証済みのタスクをまとめて報告できる。

使用例:
  ./factordb-factor.py --digits 93 --yafu ~/path/to/yafu --once              # 検証まで (送信しない)
  ./factordb-factor.py --digits 93 --yafu ~/path/to/yafu --once --upload     # 報告まで
  ./factordb-factor.py --digits 93 --yafu ~/path/to/yafu --upload            # 連続で回す
  ./factordb-factor.py --report-pending --upload                             # 検証済みの分を報告

autoload.txt の書式 (1 行目だけ読み、読んだら削除する):
  id:1100000007235234536     factordb の id を指定
  281171114807...            10 進数の数を指定 (factordb に未分解で登録済みのもの)

準備:
  * yafu: --yafu、環境変数 YAFU、PATH 上の yafu の順に探す。
  * トークン: ~/.config/factordb/token (--token-file / 環境変数 FDB_TOKEN でも可)。
    なければ匿名で報告する (クレジットなし)。トークンは画面にもログにも出さない。
  * API: https://factordb.com/api.php  (JSON-RPC 2.0 の /rpc と、従来の /api?id=)。
    因子と補因子の両方が 30 桁以上のときだけクレジットが付く。誤った因子はサーバが拒否する。
"""
import argparse
import logging
import os
import random
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time

import requests

# === 設定項目 ===
DEFAULT_DIGITS = 93                                    # 対象の桁数 (--digits で上書き可能)
DEFAULT_THREADS = 8                                    # yafu のスレッド数 (--threads で上書き可能)
SIQS_SOFT_LIMIT = 100                                  # これを超える桁数は --force が必要
DEFAULT_WORK_DIR = "~/.local/share/factordb-factor"    # DB・ログ・作業ファイルの置き場
DB_NAME = "factordb_factor.db"                         # データベースファイル名
LOG_NAME = "factordb_factor.log"                       # ログファイル名
AUTOLOAD_FILE = "autoload.txt"                         # あったら読み込むファイル (カレントディレクトリ)
DEFAULT_TOKEN_FILE = "~/.config/factordb/token"        # factordb の API トークン
WAIT_SECONDS = 10                                      # 次のタスクまでの待機秒数
RPC_URL = "https://factordb.com/rpc"
API_URL = "https://factordb.com/api"
USER_AGENT = "factordb-factor.py (teruteru128)"
# ===============
# = グローバル変数 =
logger = logging.getLogger(__name__)
# ===============


# ---------------------------------------------------------------- データベース
def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            serial_num INTEGER PRIMARY KEY AUTOINCREMENT,
            fid INTEGER NOT NULL,
            n TEXT NOT NULL,
            digits INTEGER NOT NULL,
            status TEXT NOT NULL,          -- 'running', 'factored', 'reported', 'stale', 'failed'
            factors TEXT,                  -- "P:123 P:456" の形式
            elapsed_seconds REAL,
            note TEXT,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def _db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def new_task(db_path, fid, n, digits):
    conn = _db(db_path)
    cur = conn.execute("INSERT INTO tasks (fid, n, digits, status, updated_at)"
                       " VALUES (?, ?, ?, 'running', datetime('now', 'localtime'))", (fid, str(n), digits))
    conn.commit()
    serial = cur.lastrowid
    conn.close()
    return serial


def update_task(db_path, serial, status, factors=None, elapsed=None, note=None):
    conn = _db(db_path)
    conn.execute("UPDATE tasks SET status = ?, factors = COALESCE(?, factors),"
                 " elapsed_seconds = COALESCE(?, elapsed_seconds), note = COALESCE(?, note),"
                 " updated_at = datetime('now', 'localtime') WHERE serial_num = ?",
                 (status, factors, elapsed, note, serial))
    conn.commit()
    conn.close()


def get_tasks(db_path, status):
    conn = _db(db_path)
    rows = conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY serial_num", (status,)).fetchall()
    conn.close()
    return rows


def known_fids(db_path):
    conn = _db(db_path)
    rows = conn.execute("SELECT DISTINCT fid FROM tasks").fetchall()
    conn.close()
    return {r["fid"] for r in rows}


# ---------------------------------------------------------------- factordb
def _call(method, url, **kw):
    """requests の薄いラッパー。429 は Retry-After だけ待って数回やり直す。"""
    kw.setdefault("timeout", 60)
    kw.setdefault("headers", {})["User-Agent"] = USER_AGENT
    for attempt in range(4):
        try:
            r = requests.request(method, url, **kw)
        except requests.RequestException:
            logger.exception("通信エラー (試行 %d/4)", attempt + 1)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429:
            wait = min(int(r.headers.get("Retry-After", "30") or 30), 600)
            logger.warning("HTTP 429 (レート制限・割り当て超過)。%d 秒待ちます", wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"factordb への要求が失敗しました: {method} {url}")


def rpc(method, params, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Fdb-User-Token"] = token  # トークンはここでしか使わない (ログには出さない)
    r = _call("POST", RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers)
    d = r.json()
    if "error" in d:
        raise RuntimeError(f"{method} が失敗: {d['error']}")
    return d["result"]


def api_by_id(fid):
    """従来の /api?id= (全桁を返す)。"""
    return _call("GET", API_URL, params={"id": fid}).json()


def load_token(path=None):
    """トークンを返す (なければ None)。中身は絶対に表示しない。"""
    env = os.environ.get("FDB_TOKEN")
    if env:
        return env.strip()
    p = os.path.expanduser(path or DEFAULT_TOKEN_FILE)
    if os.path.isfile(p):
        with open(p) as f:
            return f.read().strip()
    return None


# ---------------------------------------------------------------- 数論
def is_probable_prime(n, rounds=64):
    """gmpy2 があればそれを使い、なければ Miller-Rabin (固定の底 + 乱数の底)。"""
    try:
        import gmpy2
        return bool(gmpy2.is_prime(n, 40))
    except ImportError:
        pass
    if n < 2:
        return False
    for sp in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53):
        if n % sp == 0:
            return n == sp
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    rng = random.Random(0x5EED)
    for a in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37] + [rng.randrange(2, n - 1) for _ in range(rounds)]:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def has_small_factor(n, bound=10**6):
    for p in range(2, bound):
        if n % p == 0:
            return p
    return None


# ---------------------------------------------------------------- 数の取得
_count_cache = {}


def count_in_digits(digits):
    """表 C の中で、ちょうど digits 桁の数がいくつあるか (list_by_type は小さい順) を二分探索で求める。"""
    if digits in _count_cache:
        return _count_cache[digits]

    def digits_at(off):
        rows = rpc("list_by_type", {"table": "C", "min_digits": digits, "offset": off, "limit": 1})["rows"]
        return rows[0]["digits"] if rows else None

    if digits_at(0) != digits:
        _count_cache[digits] = 0
        return 0
    hi = 1
    while digits_at(hi) == digits:
        hi *= 2
    lo = hi // 2
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if digits_at(mid) == digits:
            lo = mid
        else:
            hi = mid
        time.sleep(0.1)
    _count_cache[digits] = hi
    return hi


def usable_number(fid, digits=None):
    """fid の数が、分解の対象として使えるなら (N) を返す。使えなければ理由をログに出して None。"""
    d = api_by_id(fid)
    if d["status"] != "C" or len(d["factors"]) != 1 or d["factors"][0][1] != 1:
        logger.info("id=%s は未分解の合成数ではありません (状態 %s)", fid, d["status"])
        return None
    n = int(d["factors"][0][0])
    if digits is not None and len(str(n)) != digits:
        return None
    if n % 2 == 0 or has_small_factor(n) or is_probable_prime(n):
        logger.info("id=%s は小さい因子を持つか素数らしいので除外します", fid)
        return None
    return n


def get_single_composite(digits, exclude):
    """未分解の合成数 (表 C) をランダムに 1 件選ぶ。(fid, N) か None。"""
    count = count_in_digits(digits)
    if count == 0:
        logger.warning("%d 桁の未分解合成数が見つかりません", digits)
        return None
    logger.info("%d 桁の未分解合成数 (表 C): %d 件", digits, count)
    for _ in range(20):
        off = secrets.randbelow(max(1, count - 40))
        rows = rpc("list_by_type", {"table": "C", "min_digits": digits, "offset": off, "limit": 40})["rows"]
        for row in rows:
            if row["digits"] != digits or row["term"] or row["fid"] in exclude:
                continue  # 特殊形 (式で保存されたもの) と、すでに扱った数は避ける
            n = usable_number(row["fid"], digits)
            if n:
                logger.debug("offset=%d", off)
                return row["fid"], n
        time.sleep(0.3)
    return None


def read_autoload():
    """autoload.txt の 1 行目を読んで (fid, N) を返す。読んだらファイルを削除する。"""
    if not (os.path.isfile(AUTOLOAD_FILE) and os.access(AUTOLOAD_FILE, os.R_OK)):
        return None
    logger.info("ファイル %s を検知しました。", AUTOLOAD_FILE)
    with open(AUTOLOAD_FILE, encoding="utf-8") as f:
        line = f.readline().strip()
    os.remove(AUTOLOAD_FILE)
    logger.info("読み込みに成功したので %s を削除しました。", AUTOLOAD_FILE)
    try:
        if line.startswith("id:"):
            fid = int(line[3:])
        elif line.isdigit():
            res = rpc("get_id", {"expr": line, "create": False})["id"]
            fid = int(res["fid"] if isinstance(res, dict) else res)  # 辞書 {"fid", "kind"} で返ってくる
        else:
            logger.warning("autoload の書式が不正です: %r", line[:40])
            return None
    except (ValueError, KeyError, RuntimeError):
        logger.warning("autoload の数を factordb で解決できませんでした (未登録の数は扱えません)")
        return None
    n = usable_number(fid)
    return (fid, n) if n else None


# ---------------------------------------------------------------- 分解
def find_yafu(arg):
    cand = arg or os.environ.get("YAFU") or shutil.which("yafu")
    if not cand:
        raise SystemExit("yafu が見つかりません。--yafu か環境変数 YAFU で指定してください")
    cand = os.path.abspath(os.path.expanduser(cand))
    if not os.access(cand, os.X_OK):
        raise SystemExit(f"yafu を実行できません: {cand}")
    return cand


FACTOR_LINE = re.compile(r"^(P|C)(\d+) = (\d+)\s*$")


def parse_factors(text):
    """yafu の出力から "P36 = ..." / "C58 = ..." の行を、最後の "factors found" 以降から集める。"""
    lines = [l.rstrip("\r") for l in text.replace("\r", "\n").split("\n")]
    start = 0
    for i, l in enumerate(lines):
        if "***factors found***" in l:
            start = i
    out = []
    for l in lines[start:]:
        m = FACTOR_LINE.match(l.strip())
        if m:
            out.append((m.group(1), int(m.group(3))))
    return out


def run_yafu(yafu, wd, n, threads, progress, resume):
    """yafu で siqs(N) を実行して [(kind, value), ...] を返す。失敗したら None。"""
    siqs_dat = os.path.join(wd, "siqs.dat")
    if not resume and os.path.exists(siqs_dat):
        os.remove(siqs_dat)  # 新規の分解で前回の中断データから再開してしまわないように
    logpath = os.path.join(wd, "yafu.log")
    logger.info("yafu を実行中... (threads=%d, ログ: %s)%s", threads, logpath,
                " [siqs.dat から再開]" if resume and os.path.exists(siqs_dat) else "")
    with open(logpath, "ab") as lf:
        # yafu はコマンドライン引数の式を端末でしか実行しない。標準入力から式を渡せば端末は不要。
        proc = subprocess.Popen([yafu, "-v", "-threads", str(threads)], cwd=wd,
                                stdin=subprocess.PIPE, stdout=lf, stderr=subprocess.STDOUT)
        proc.stdin.write(f"siqs({n})\n".encode())
        proc.stdin.close()
        last = time.time()
        try:
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - last >= progress:
                    last = time.time()
                    try:
                        with open(logpath, "rb") as f:
                            f.seek(max(0, os.path.getsize(logpath) - 4000))
                            tail = f.read().decode(errors="replace").replace("\r", "\n")
                        prog = [l for l in tail.split("\n") if "rels found" in l]
                        if prog:
                            logger.info("  %s", prog[-1].strip()[:110])
                    except OSError:
                        pass
        except KeyboardInterrupt:
            # Ctrl-C は端末の同じプロセスグループの yafu にも届いている。
            # yafu が siqs.dat に状態を保存し終えるのを待ってから終了する (次回はそこから再開できる)。
            logger.warning("中断を検知しました。yafu の状態保存を待ちます (最大120秒)...")
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
    with open(logpath, "rb") as f:
        facs = parse_factors(f.read().decode(errors="replace"))
    prod = 1
    for _, v in facs:
        prod *= v
    if not facs or prod != n:
        logger.error("yafu の出力から N を復元できませんでした (code %s)。ログ: %s", proc.returncode, logpath)
        return None
    return facs


def encode_factors(facs):
    return " ".join(f"{k}:{v}" for k, v in facs)


def decode_factors(s):
    return [(k, int(v)) for k, v in (t.split(":") for t in s.split())]


# ---------------------------------------------------------------- 検証と報告
def verify(n, facs):
    """積が N と一致し、P の因子が素数であることを確認する。"""
    prod = 1
    for _, v in facs:
        prod *= v
    if prod != n:
        logger.error("因子の積が N と一致しません")
        return False
    if len(facs) < 2 or any(v <= 1 or v >= n for _, v in facs):
        logger.error("自明でない因子が 2 つ以上必要です")
        return False
    for kind, v in facs:
        if kind == "P" and not is_probable_prime(v):
            logger.error("素数のはずの因子が素数判定に失敗: %d", v)
            return False
    logger.info("検証OK: 因子の積 == N (%s 桁)、P の因子は素数判定を通過",
                " × ".join(str(len(str(v))) for _, v in facs))
    return True


def report_task(db_path, task, args):
    """検証済みのタスクを factordb に報告する。送信した/止めた結果を DB に反映する。"""
    serial, fid, n = task["serial_num"], task["fid"], int(task["n"])
    facs = decode_factors(task["factors"])
    if not verify(n, facs):
        update_task(db_path, serial, "failed", note="verification failed")
        return False
    d = api_by_id(fid)
    logger.info("factordb の現在の状態: %s", d["status"])
    if d["status"] not in ("C", "CF"):
        logger.warning("状態が %s です (すでに分解済み、または素数)。送信しません", d["status"])
        update_task(db_path, serial, "stale", note=f"factordb status {d['status']}")
        return True
    token = load_token(args.token_file)
    credit = (not args.no_credit) and token is not None
    if token is None:
        logger.warning("トークンが見つかりません。匿名で報告します (クレジットなし)")
    if credit and any(len(str(v)) < 30 for _, v in facs):
        logger.warning("30 桁未満の因子があるため、クレジットは付かない可能性があります")
    if not args.upload:
        logger.info("ドライラン: --upload が指定されていないので送信しません")
        return True
    res = rpc("report_factors", {"target": {"id": fid}, "factors": [str(v) for _, v in facs], "credit": credit},
              token=token)
    logger.info("送信結果: status=%s 新規id=%s credited=%s", res.get("status"), res.get("created_ids"),
                res.get("credited"))
    d = api_by_id(fid)
    logger.info("factordb の送信後の状態: %s", d["status"])
    for base, exp in d["factors"]:
        logger.info("  %s ^ %s", base, exp)
    update_task(db_path, serial, "reported", note=f"{d['status']} credited={res.get('credited')}")
    return True


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="Factordb SIQS 分解 自動化スクリプト (SQLite3管理版)")
    parser.add_argument("--digits", type=int, default=DEFAULT_DIGITS, help=f"対象の桁数。既定は{DEFAULT_DIGITS}。")
    parser.add_argument("--yafu", help="yafu の実行ファイル (既定: 環境変数 YAFU または PATH 上の yafu)")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                        help=f"yafu のスレッド数。既定は{DEFAULT_THREADS}。他の計算と同時に回すときに絞る用途を想定している。")
    parser.add_argument("--once", action="store_true",
                        help="1件処理したら終了する。指定しない場合は連続して次の数へ進む。")
    parser.add_argument("--upload", action="store_true",
                        help="factordb に実際に報告する。指定しない場合はドライラン (検証まで。1件で止まる)。")
    parser.add_argument("--report-pending", action="store_true",
                        help="分解・検証済みで未報告のタスク (status='factored') を報告して終了する。")
    parser.add_argument("--no-credit", action="store_true", help="アカウントへのクレジットを要求しない。")
    parser.add_argument("--token-file", help=f"トークンのファイル (既定 {DEFAULT_TOKEN_FILE}、環境変数 FDB_TOKEN も可)")
    parser.add_argument("--workdir", default=DEFAULT_WORK_DIR, help=f"DB・ログ・作業ファイルの置き場。既定は{DEFAULT_WORK_DIR}。")
    parser.add_argument("--progress", type=int, default=60, help="進捗ログの間隔 (秒)。既定は60。")
    parser.add_argument("--force", action="store_true", help=f"{SIQS_SOFT_LIMIT} 桁を超える入力を許可する。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="ログの出力レベル。デフォルトはINFO。詳細調査時はDEBUGを指定してください。")
    args = parser.parse_args()

    if args.threads < 1:
        parser.error(f"--threads は1以上を指定してください: {args.threads}")
    if args.digits > SIQS_SOFT_LIMIT and not args.force and not args.report_pending:
        parser.error(f"{args.digits} 桁は SIQS では重すぎる目安 (> {SIQS_SOFT_LIMIT}) です。続けるなら --force")

    workdir = os.path.abspath(os.path.expanduser(args.workdir))
    os.makedirs(workdir, exist_ok=True)
    db_path = os.path.join(workdir, DB_NAME)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(workdir, LOG_NAME))],
    )
    init_db(db_path)
    logger.info("Factordb SIQS 自動化タスク（SQLite3管理版）を開始します。(workdir: %s)", workdir)

    # 検証済みの未報告タスクを報告して終了するモード
    if args.report_pending:
        pending = get_tasks(db_path, "factored")
        logger.info("未報告のタスク: %d 件", len(pending))
        for task in pending:
            report_task(db_path, task, args)
        return

    yafu = find_yafu(args.yafu)
    once = args.once or not args.upload
    if not args.upload and not args.once:
        logger.info("--upload がないため、1 件 (検証まで) で終了します。")

    while True:
        # 1. 前回中断されたタスク (status='running') があれば、それを再開する
        interrupted = get_tasks(db_path, "running")
        if interrupted:
            t = interrupted[0]
            serial, fid, n, digits = t["serial_num"], t["fid"], int(t["n"]), t["digits"]
            logger.info("[★レジューム] 前回の未完了タスクをDBから復元しました。連番: %d (id=%d, %d桁)", serial, fid, digits)
            resume = True
        else:
            # 2. 新しい数を決める: autoload.txt があればそれを先に、なければ factordb からランダムに
            picked = read_autoload()
            if not picked:
                logger.info("--- %d桁の未分解合成数を1件取得中 (ランダム) ---", args.digits)
                picked = get_single_composite(args.digits, known_fids(db_path))
            if not picked:
                logger.warning("対象の数が見つからないか、エラーが発生しました。30秒後に再試行します。")
                time.sleep(30)
                continue
            fid, n = picked
            digits = len(str(n))
            serial = new_task(db_path, fid, n, digits)
            logger.info("ターゲットを取得しました。連番: %d (id=%d, %d桁)", serial, fid, digits)
            logger.info("N = %d", n)
            resume = False

        # 3. 分解
        wd = os.path.join(workdir, f"{serial}-{fid}")
        os.makedirs(wd, exist_ok=True)
        start = time.time()
        facs = run_yafu(yafu, wd, n, args.threads, args.progress, resume)
        if not facs and resume:
            # 強制終了などで siqs.dat が壊れていると、再開に失敗することがある。
            # その場合は止まらずに、データを捨てて最初からやり直す。
            logger.warning("再開に失敗しました (siqs.dat が壊れている可能性)。データを捨てて最初からやり直します。")
            facs = run_yafu(yafu, wd, n, args.threads, args.progress, False)
        elapsed = time.time() - start
        if not facs:
            update_task(db_path, serial, "failed", elapsed=elapsed, note="yafu failed")
            logger.error("分解に失敗したので終了します。(連番 %d)", serial)
            break
        logger.info("分解結果: %s (所要 %.0f 秒)", " × ".join(f"{k}{len(str(v))}" for k, v in facs), elapsed)
        for k, v in facs:
            logger.info("  %s%d = %d", k, len(str(v)), v)
        update_task(db_path, serial, "factored", factors=encode_factors(facs), elapsed=elapsed)

        # 4. 検証して報告 (--upload のときだけ送信する)
        task = [t for t in get_tasks(db_path, "factored") if t["serial_num"] == serial][0]
        if not report_task(db_path, task, args):
            break
        if args.upload:
            # 容量を食う中間ファイルを掃除する (成功後は不要)
            for name in ("siqs.dat",):
                p = os.path.join(wd, name)
                if os.path.exists(p):
                    os.remove(p)

        if once:
            logger.info("1件処理したので終了します。")
            break
        logger.info("次のタスクまで%d秒待機します...", WAIT_SECONDS)
        time.sleep(WAIT_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
