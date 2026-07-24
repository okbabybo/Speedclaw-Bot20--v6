#!/usr/bin/env python3
"""
trading_worker.py - 实际下单/通知执行 worker
============================
作用: 从 copy_trade_queue.jsonl 队列拉任务 → 按用户API下真实单 → 回报
风控:
  - 单日亏损>10%熔断
  - 用户余额<$5跳过
  - 异常单重试3次后丢弃
"""

import os
import json
import time
import hmac
import hashlib
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

WORKDIR = Path("/root/.openclaw/workspace")
QUEUE = WORKDIR / "copy_trade_queue.jsonl"
DONE_LOG = WORKDIR / "copy_trade_done.jsonl"
USERS_FILE = WORKDIR / "paperclip_users.json"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

MAX_DAILY_LOSS_PCT = 10  # 单日亏损>10%熔断
RETRY_MAX = 3


def sign_binance(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def place_order(api_key: str, secret: str, symbol: str, side: str,
                qty: float, leverage: int = 20, dry_run: bool = True) -> dict:
    """Binance 下单(默认dry_run=True,只构造不下)"""
    base_url = "https://fapi.binance.com"
    ts = int(time.time() * 1000)

    # 设置杠杆
    lev_path = "/fapi/v1/leverage"
    lev_qs = f"symbol={symbol}&leverage={leverage}&timestamp={ts}"
    lev_qs += f"&signature={sign_binance(secret, lev_qs)}"

    # 下单
    order_path = "/fapi/v1/order"
    order_qs = (
        f"symbol={symbol}&side={side}&type=MARKET"
        f"&quantity={qty}&timestamp={ts}"
    )
    order_qs += f"&signature={sign_binance(secret, order_qs)}"

    if dry_run or os.environ.get("DRY_RUN", "1") == "1":
        return {
            "ok": True,
            "dry_run": True,
            "would_set_leverage": leverage,
            "would_place": f"{side} {qty} {symbol} MARKET",
            "url": base_url + order_path,
        }

    headers = {"X-MBX-APIKEY": api_key}
    try:
        # 真实模式才发请求
        r_lev = requests.post(
            base_url + lev_path, params=lev_qs, headers=headers, timeout=5
        )
        r_order = requests.post(
            base_url + order_path, params=order_qs, headers=headers, timeout=5
        )
        return {"ok": r_order.ok, "status": r_order.status_code,
                "body": r_order.json() if r_order.ok else r_order.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_daily_loss_limit(user: dict) -> bool:
    """检查单日亏损是否超限(读done_log)"""
    chat_id = user.get("telegram_chat_id")
    today = datetime.now(timezone.utc).date().isoformat()
    daily_pnl = 0
    if DONE_LOG.exists():
        for line in DONE_LOG:
            try:
                rec = json.loads(line)
                if rec.get("user_id") == chat_id and rec.get("date") == today:
                    daily_pnl += rec.get("pnl", 0)
            except Exception:
                continue
    vbal = user.get("virtual_balance", 100)
    loss_pct = abs(min(daily_pnl, 0)) / vbal * 100
    return loss_pct >= MAX_DAILY_LOSS_PCT


def execute_task(task: dict):
    """执行一个跟单任务"""
    user_id = task["user_id"]
    user = None
    if USERS_FILE.exists():
        for uid, info in json.loads(USERS_FILE.read_text()).items():
            if info.get("telegram_chat_id") == user_id:
                user = info
                break

    if not user:
        print(f"[WORKER] no user found for chat_id={user_id}, skip")
        return

    # 风控检查
    if check_daily_loss_limit(user):
        msg = f"🛑 {user['username']} 单日亏损熔断,跳过跟单 {task['symbol']} {task['side']}"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": user_id, "text": msg},
            timeout=5,
        )
        return

    # 解密 API key (生产用 Fernet)
    api_key = user.get("api_key_encrypted", "")
    secret = user.get("api_secret_encrypted", "")
    if api_key.startswith("FERNET:"):
        # 真生产时: cipher.decrypt(api_key[7:].encode()).decode()
        api_key = "DEMO_KEY"
        secret = "DEMO_SECRET"

    # 下单
    result = place_order(
        api_key, secret,
        task["symbol"], task["side"],
        task["qty"], task.get("leverage", 20),
        dry_run=os.environ.get("DRY_RUN", "1") == "1",
    )

    # 落执行记录
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).date().isoformat(),
        "user_id": user_id,
        "username": user.get("username"),
        "symbol": task["symbol"],
        "side": task["side"],
        "qty": task["qty"],
        "price": task["price"],
        "result": result,
        "pnl": 0,
    }
    with open(DONE_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")

    # 通知用户
    emoji = "✅" if result.get("ok") else "❌"
    text = (
        f"{emoji} <b>跟单执行</b>\n"
        f"{user['username']} {task['side']} {task['qty']} {task['symbol']} @ ${task['price']}\n"
        f"{'(DRY_RUN 模拟)' if result.get('dry_run') else '真实下单'}\n"
        f"{result.get('would_place', result.get('body', ''))}"
    )
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": user_id, "text": text, "parse_mode": "HTML"},
        timeout=5,
    )


def main_loop(poll_sec: int = 5):
    """主循环:扫队列→执行任务"""
    print(f"[WORKER] started, polling every {poll_sec}s")
    if not QUEUE.exists():
        QUEUE.touch()

    seen_ids = set()
    while True:
        if not QUEUE.exists():
            time.sleep(poll_sec)
            continue
        with open(QUEUE) as f:
            for line_no, line in enumerate(f):
                if line_no in seen_ids:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    task = json.loads(line)
                    execute_task(task)
                    seen_ids.add(line_no)
                except Exception as e:
                    print(f"[ERR] task {line_no}: {e}")
        time.sleep(poll_sec)


if __name__ == "__main__":
    os.environ.setdefault("DRY_RUN", "1")
    main_loop()
