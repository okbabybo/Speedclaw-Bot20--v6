#!/usr/bin/env python3
"""
signal_broadcaster.py - 跟单模式信号广播层
============================
作用: bot20x/master账户成交 → 推信号到 Redis + Telegram channel
触发: bot20x成交回调 (on_trade) / 或定时轮询 / 或模拟器模式
"""

import os
import json
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path

# ===== 配置 =====
WORKDIR = Path("/root/.openclaw/workspace")
SIGNAL_LOG = WORKDIR / "signals_master.jsonl"  # 信号流水(追加写)
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OWNER_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

# 模拟主账户(后续真实替换为bot20x真实成交)
MASTER_ACCOUNT = {
    "exchange": "binance",
    "api_key": os.environ.get("BINANCE_API_KEY_MASTER", "MASTER_SIM"),
    "secret": os.environ.get("BINANCE_SECRET_MASTER", "MASTER_SIM_SECRET"),
    "testnet": False,
}


def sign_payload(payload: dict) -> str:
    """签名防伪: 防止信号被篡改"""
    msg = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    sig = hmac.new(
        MASTER_ACCOUNT["secret"].encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()
    return sig


def make_signal(symbol: str, side: str, qty: float, price: float) -> dict:
    """生成标准跟单信号"""
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "side": side.upper(),  # BUY / SELL
        "qty": round(qty, 6),
        "price": round(price, 2),
        "master": "bot20x",
        "mode": os.environ.get("SIGNAL_MODE", "SIM"),  # SIM 模拟 / LIVE 真实
    }


def broadcast_to_telegram_channel(signal: dict):
    """推信号到 Telegram channel (后续用户订阅此channel)"""
    if not TG_TOKEN:
        return False
    # 这里用 BotFather 创建的 @chaos_warrior_signals channel
    # 用户通过 /subscribe 命令加入,接收成交推送
    text = (
        f"🎯 <b>跟单信号</b>\n"
        f"标的: {signal['symbol']}\n"
        f"方向: {signal['side']}\n"
        f"数量: {signal['qty']}\n"
        f"价格: ${signal['price']}\n"
        f"来源: {signal['master']} ({signal['mode']})\n"
        f"时间: {signal['ts'][:19]}Z"
    )
    # 真实channel ID 待老板创建后填入
    channel_id = os.environ.get("SIGNAL_CHANNEL_ID", "")
    if not channel_id:
        print(f"[SIM broadcast] {text}")
        return True  # 模拟成功
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": channel_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=5,
        )
    except Exception as e:
        print(f"[ERR] tg broadcast: {e}")
        return False


def log_signal(signal: dict):
    """信号追加写流水文件,供跟单worker读取"""
    sig = sign_payload(signal)
    signal["_sig"] = sig  # 给worker校验用
    with open(SIGNAL_LOG, "a") as f:
        f.write(json.dumps(signal) + "\n")


def emit(symbol: str, side: str, qty: float, price: float):
    """主入口: 生成信号→广播→落盘"""
    sig = make_signal(symbol, side, qty, price)
    log_signal(sig)
    broadcast_to_telegram_channel(sig)
    print(f"[SIGNAL] {sig['ts']} {sig['symbol']} {sig['side']} qty={qty} px={price}")
    return sig


# ===== 模拟器模式 =====
def simulator_loop(interval_sec: int = 30):
    """模拟主账户成交,定时推信号 — 仅用于链路验证"""
    print(f"[SIM] master signal simulator started, interval={interval_sec}s")
    sim_seq = [
        ("BTCUSDT", "BUY", 0.001, 67500),
        ("ETHUSDT", "BUY", 0.01, 3300),
        ("BTCUSDT", "SELL", 0.001, 67800),
        ("ETHUSDT", "SELL", 0.01, 3320),
        ("BTCUSDT", "BUY", 0.002, 67200),
    ]
    i = 0
    while True:
        sym, side, qty, px = sim_seq[i % len(sim_seq)]
        emit(sym, side, qty, px + (i % 3) * 5)  # 微调价格模拟波动
        i += 1
        time.sleep(interval_sec)


if __name__ == "__main__":
    if os.environ.get("SIMULATOR") == "1":
        simulator_loop()
    else:
        # 手动发射一条测试信号
        emit("BTCUSDT", "BUY", 0.001, 67500)
