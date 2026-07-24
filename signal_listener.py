#!/usr/bin/env python3
"""
signal_listener.py - 跟单worker 信号监听层
============================
作用: 从信号流水文件读信号 → 按用户比例拆分 → 加入跟单队列
"""

import os
import json
import time
import hmac
import hashlib
import requests
from pathlib import Path

WORKDIR = Path("/root/.openclaw/workspace")
SIGNAL_LOG = WORKDIR / "signals_master.jsonl"
MASTER_SECRET = os.environ.get("BINANCE_SECRET_MASTER", "MASTER_SIM_SECRET")

WORKER_QUEUE = WORKDIR / "copy_trade_queue.jsonl"  # worker待执行队列


def verify_signal(signal: dict) -> bool:
    """校验信号签名,防止伪造"""
    sig_received = signal.pop("_sig", "")
    msg = json.dumps(signal, sort_keys=True, separators=(",", ":"))
    sig_expected = hmac.new(
        MASTER_SECRET.encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig_received, sig_expected)


def split_by_user_balance(master_qty: float, master_balance: float,
                          user_balance: float, leverage: int = 20) -> float:
    """
    按用户账户余额比例拆分下单数量
    例: master账户1000U,master下0.01 ETH → user账户500U → user下 0.005 ETH
    """
    if master_balance <= 0:
        return 0
    ratio = (user_balance * leverage) / (master_balance * leverage)
    return round(master_qty * ratio, 6)


def load_users() -> list:
    """加载已激活的订阅用户"""
    p = WORKDIR / "paperclip_users.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    users = []
    for uid, info in data.items():
        # 只处理已激活且绑定API的用户
        if info.get("tier") and info.get("tier") not in ["trial", None]:
            if info.get("telegram_chat_id") and info.get("api_key_encrypted"):
                users.append(info)
    return users


def queue_signal_for_user(user: dict, signal: dict, user_qty: float):
    """用户的跟单单推入执行队列"""
    task = {
        "ts": signal["ts"],
        "user_id": user.get("telegram_chat_id"),
        "username": user.get("username"),
        "symbol": signal["symbol"],
        "side": signal["side"],
        "qty": user_qty,
        "price": signal["price"],
        "leverage": user.get("leverage", 20),
        "status": "PENDING",
    }
    with open(WORKER_QUEUE, "a") as f:
        f.write(json.dumps(task) + "\n")
    print(f"[QUEUE] user={user['username']} {task['symbol']} {task['side']} qty={user_qty}")


def notify_user_dry(user: dict, signal: dict, user_qty: float):
    """干跑模式:仅通知用户,不下单"""
    tg_id = user.get("telegram_chat_id")
    if not tg_id:
        return
    text = (
        f"📡 <b>跟单信号</b>\n"
        f"主账户刚刚 {signal['side']} {signal['qty']} {signal['symbol']} @ ${signal['price']}\n"
        f"按你账户比例你应该跟: {user_qty} {signal['symbol']}\n"
        f"(当前 DRY_RUN 模式,不实际下单)"
    )
    # 实际通过 trading_worker 统一推送,此处仅记录


def process_signal(signal: dict):
    """处理一条信号:找所有订阅用户 → 按比例拆单 → 入队"""
    master_qty = signal["qty"]
    # 模拟主账户余额,后续接真实bot20x余额查询
    master_balance = float(os.environ.get("MASTER_BALANCE", 1000))

    users = load_users()
    if not users:
        print("[LISTENER] no active subscribers, signal dropped")
        return

    for user in users:
        user_balance = user.get("virtual_balance", 100)  # 模拟用户账户
        user_qty = split_by_user_balance(master_qty, master_balance, user_balance)
        if user_qty <= 0:
            continue
        if os.environ.get("DRY_RUN", "1") == "1":
            notify_user_dry(user, signal, user_qty)
        else:
            queue_signal_for_user(user, signal, user_qty)


def follow_tail():
    """监听信号流水文件,只读新追加的行"""
    if not SIGNAL_LOG.exists():
        SIGNAL_LOG.touch()
        return 0
    # 简单实现:每次从最后读全部
    signals = []
    with open(SIGNAL_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                sig = json.loads(line)
                if verify_signal(sig):
                    signals.append(sig)
            except Exception:
                continue
    return len(signals)


def main_loop(poll_sec: int = 5):
    """主循环:定期扫信号文件,处理新信号"""
    print(f"[LISTENER] started, polling every {poll_sec}s")
    seen_count = 0
    while True:
        current = follow_tail()
        if current > seen_count:
            # 重读全部,处理去重由worker侧做
            with open(SIGNAL_LOG) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sig = json.loads(line)
                        # 已签名校验过的不重复
                        process_signal(sig)
                    except Exception as e:
                        print(f"[ERR] process: {e}")
            seen_count = current
        time.sleep(poll_sec)


if __name__ == "__main__":
    # 默认干跑模式,不下单,只通知
    os.environ.setdefault("DRY_RUN", "1")
    main_loop()
