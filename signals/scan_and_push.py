#!/usr/bin/env python3
"""
混沌龙虾信号推送器
每15分钟扫一次，有高置信信号则推送飞书
"""
import json, subprocess, sys, os
sys.path.insert(0, os.path.dirname(__file__))

import signal_engine as se

WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

def send_feishu(text):
    if not WEBHOOK:
        print("NO_WEBHOOK:", text)
        return
    r = __import__("requests").post(WEBHOOK, json={"msg_type":"text","content":{"text": text}}, timeout=10)
    print("PUSH:", r.status_code)

results = []
for inst, name in [("BTC-USDT-SWAP","BTC"), ("ETH-USDT-SWAP","ETH")]:
    try:
        sig = se.generate_signal(inst, name)
        results.append(sig)
    except Exception as e:
        print(f"ERROR {inst}: {e}", file=sys.stderr)

# 推送高置信信号
for sig in results:
    if sig and sig["confidence"] >= 50:
        msg = se.format_signal(sig).replace("**", "").replace("───", "─")
        send_feishu(f"🦞 信号 | {sig['name']}\n{msg}")

# 同时保存
with open(os.path.join(os.path.dirname(__file__), "logs/latest.json"), "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
