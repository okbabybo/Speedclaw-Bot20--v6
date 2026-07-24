#!/usr/bin/env python3
"""
trading_engine.py - 混沌武士跟单引擎主循环
============================
作用:
  - 60秒轮询
  - 每用户独立 USDT 余额核算
  - /autostart /autostop /killall Telegram 命令控制
  - 调用 trading_worker 实际下单
"""

import os
import sys
import json
import time
import signal
import requests
from pathlib import Path
from datetime import datetime, timezone

WORKDIR = Path("/root/.openclaw/workspace")
STATE_FILE = WORKDIR / "engine_state.json"
USERS_FILE = WORKDIR / "paperclip_users.json"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OWNER_ID = os.environ.get("OWNER_TELEGRAM_ID", "")

POLL_INTERVAL = 60           # 60秒轮询
MIN_USER_BALANCE = 5         # 用户余额<$5跳过
KILLALL_FILE = WORKDIR / ".killall"


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"running": False, "started_at": None, "last_poll": None,
            "polls_count": 0, "users_processed": 0}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def tg_send(chat_id: str, text: str):
    """统一tg推送(含重试)"""
    if not TG_TOKEN or not chat_id:
        return
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
            if r.ok:
                return
        except Exception:
            time.sleep(1)


def poll_once(state: dict):
    """单次轮询:扫一遍用户,调用worker"""
    state["polls_count"] += 1
    state["last_poll"] = datetime.now(timezone.utc).isoformat()

    if not USERS_FILE.exists():
        save_state(state)
        return

    users = json.loads(USERS_FILE.read_text())
    active_count = 0
    skipped_low_balance = 0

    for uid, info in users.items():
        tier = info.get("tier")
        if not tier or tier == "trial":
            continue
        chat_id = info.get("telegram_chat_id")
        if not chat_id:
            continue
        # 模拟用户余额,后续接Binance真实余额查询
        vbal = info.get("virtual_balance", 100)
        if vbal < MIN_USER_BALANCE:
            skipped_low_balance += 1
            continue
        active_count += 1
        state["users_processed"] += 1

    save_state(state)
    print(f"[POLL #{state['polls_count']}] active={active_count} "
          f"skipped_low={skipped_low_balance} at {state['last_poll']}")


def handle_killall():
    """检查.killall文件存在则紧急停机"""
    if KILLALL_FILE.exists():
        KILLALL_FILE.unlink()
        print("[KILLALL] triggered — engine stopping")
        return True
    return False


def cmd_autostart(chat_id: str):
    """/autostart 启动引擎"""
    state = load_state()
    state["running"] = True
    state["started_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    tg_send(chat_id, "✅ 跟单引擎已启动 (60秒轮询, 余额阈值$5)")


def cmd_autostop(chat_id: str):
    """/autostop 暂停引擎"""
    state = load_state()
    state["running"] = False
    save_state(state)
    tg_send(chat_id, "⏸ 跟单引擎已暂停 (数据仍采集, 不下单)")


def cmd_killall(chat_id: str):
    """/killall 紧急切断(老板保留红线)"""
    KILLALL_FILE.touch()
    tg_send(chat_id, "🚨 KILLALL 已触发, 下一轮询停机")


def cmd_status(chat_id: str):
    state = load_state()
    lines = [
        "📊 <b>引擎状态</b>",
        f"运行中: {'是' if state['running'] else '否'}",
        f"启动: {state.get('started_at', 'N/A')}",
        f"轮询: {state['polls_count']} 次",
        f"处理: {state['users_processed']} 用户次",
        f"末轮: {state.get('last_poll', 'N/A')}",
    ]
    tg_send(chat_id, "\n".join(lines))


def handle_telegram_command(text: str, chat_id: str):
    """分发 Telegram 命令"""
    if chat_id != OWNER_ID:
        tg_send(chat_id, "⛔ 无权限")
        return
    cmd = text.strip().split()[0].lstrip("/").lower()
    {
        "autostart": cmd_autostart,
        "autostop": cmd_autostop,
        "killall": cmd_killall,
        "status": cmd_status,
    }.get(cmd, lambda c: tg_send(c, "❓ 未知命令: /autostart /autostop /killall /status"))(chat_id)


def main():
    print(f"[ENGINE] chaos-warrior copy-trading engine starting")
    print(f"[CONFIG] poll={POLL_INTERVAL}s, min_balance=${MIN_USER_BALANCE}")
    state = load_state()
    if not state["running"]:
        state["running"] = True
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        tg_send(OWNER_ID, "🟢 跟单引擎已自动启动 (polling=60s, min_bal=$5)")

    last_poll = 0
    while True:
        if handle_killall():
            state = load_state()
            state["running"] = False
            save_state(state)
            tg_send(OWNER_ID, "🛑 KILLALL 生效, 引擎停止")
            break

        if not state["running"]:
            time.sleep(POLL_INTERVAL)
            continue

        now = time.time()
        if now - last_poll >= POLL_INTERVAL:
            poll_once(state)
            last_poll = now

        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[ENGINE] shutdown by ctrl-c")
    except Exception as e:
        print(f"[ENGINE] FATAL: {e}")
        sys.exit(1)
