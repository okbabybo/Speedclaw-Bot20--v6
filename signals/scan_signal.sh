#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, '/root/.openclaw/workspace/signals')
import signal_engine_v2 as se
from datetime import datetime, timezone, timedelta

results = []
for inst, name in [('BTC-USDT-SWAP','BTC'), ('ETH-USDT-SWAP','ETH')]:
    try:
        sig = se.generate_signal_v2(inst, name)
        results.append(sig)
    except Exception as e:
        import traceback; traceback.print_exc()

tz = timezone(timedelta(hours=8))
now = datetime.now(tz).strftime('%Y-%m-%d %H:%M')

# 只推送置信度 >= 80% 的信号
high_conf = [sig for sig in results if sig and sig['confidence'] >= 80]

if not high_conf:
    sys.exit(0)  # 静默退出

print(f'🦞 精准信号 | {now}')
print()
for sig in high_conf:
    conf = sig['confidence']
    rec = f'🟢 LONG {conf}%' if sig['direction'] == 'LONG' else f'🔴 SHORT {conf}%'
    sigs = sig.get('signals_long',[]) if sig['direction']=='LONG' else sig.get('signals_short',[])
    reason_str = '；'.join(sigs[:2]) if sigs else '综合信号'
    entry = sig['entry']
    stop = sig.get('stop')
    tp1 = sig.get('tp1')
    tp2 = sig.get('tp2')
    
    print(f'【{sig["name"]}】')
    print(f'推荐 → {rec}')
    print(f'理由 → {reason_str}')
    print(f'RSI → {sig["rsi_14"]}(1H) / {sig["rsi_4h"]}(4H)')
    if sig['direction'] != 'WAIT' and stop:
        if sig['direction'] == 'LONG':
            print(f'入场 → {entry}')
            print(f'止损 → {stop}（-{abs(entry-stop)/entry*100:.2f}%）')
            if tp1: print(f'目标1 → {tp1}（+{abs(tp1-entry)/entry*100:.2f}%）')
            if tp2: print(f'目标2 → {tp2}（+{abs(tp2-entry)/entry*100:.2f}%）')
        else:
            print(f'入场 → {entry}')
            print(f'止损 → {stop}（+{abs(entry-stop)/entry*100:.2f}%）')
            if tp1: print(f'目标1 → {tp1}（-{abs(entry-tp1)/entry*100:.2f}%）')
            if tp2: print(f'目标2 → {tp2}（-{abs(entry-tp2)/entry*100:.2f}%）')
    print()

# 保存
with open('/root/.openclaw/workspace/signals/logs/latest_v2.json', 'w') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
