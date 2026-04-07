#!/bin/bash
# 混沌龙虾信号系统推送脚本
# 每15分钟自动运行

cd /root/.openclaw/workspace/signals
python3 signal_engine.py 2>&1 | tail -50

# 读取最新信号
python3 - << 'EOF'
import json, sys
sys.path.insert(0, '.')
import signal_engine as se

results = []
for inst, name in [("BTC-USDT-SWAP","BTC"), ("ETH-USDT-SWAP","ETH")]:
    try:
        sig = se.generate_signal(inst, name)
        results.append(sig)
    except Exception as e:
        pass

# 只推送有信号的情况
for sig in results:
    if sig and sig["confidence"] >= 40:
        print(json.dumps(sig, ensure_ascii=False))
EOF
