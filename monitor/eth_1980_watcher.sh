#!/bin/bash
# ETH 1980 下方预警监控脚本
# 触发条件：ETH-USDT 永续合约价格 < 1980

ALERT_THRESHOLD=1980
STATE_FILE="/root/.openclaw/workspace/monitor/eth_1980_state.json"
LOG_FILE="/root/.openclaw/workspace/monitor/eth_1980_watch.log"

# 获取OKX ETH价格
PRICE=$(curl -s "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(d['data'][0]['last'])
" 2>/dev/null)

if [ -z "$PRICE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 获取价格失败" >> "$LOG_FILE"
    exit 1
fi

RESULT=$(python3 -c "
price = float('$PRICE')
threshold = $ALERT_THRESHOLD
print(f'price:{price:.2f}')
print(f'threshold:{threshold}')
print(f'below:{price < threshold}')
")

PRICE_VAL=$(echo "$RESULT" | grep '^price:' | cut -d: -f2)
BELOW=$(echo "$RESULT" | grep '^below:' | cut -d: -f2)

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ETH价格: \$$PRICE_VAL (警戒线: \$$ALERT_THRESHOLD)" >> "$LOG_FILE"

# 如果低于阈值
if [ "$BELOW" == "True" ]; then
    # 检查是否已发过
    if [ -f "$STATE_FILE" ]; then
        LAST_ALERT=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('last_alert',''))" 2>/dev/null)
        LAST_TIME=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('last_time',''))" 2>/dev/null)
        
        # 10分钟内不重复发
        if [ ! -z "$LAST_ALERT" ]; then
            LAST_EPOCH=$(python3 -c "
import time, json
d = json.load(open('$STATE_FILE'))
if 'last_time_epoch' in d:
    print(d['last_time_epoch'])
else:
    from datetime import datetime
    dt = datetime.fromisoformat('$LAST_TIME')
    print(int(dt.timestamp()))
" 2>/dev/null)
            
            NOW_EPOCH=$(date +%s)
            DIFF=$((NOW_EPOCH - LAST_EPOCH))
            
            if [ $DIFF -lt 600 ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] 10分钟内已发过预警，跳过" >> "$LOG_FILE"
                exit 0
            fi
        fi
    fi
    
    # 发送预警
    MSG="🚨 ETH 预警
━━━━━━━━━━━━━━━━━━
📍 当前价格：\$$PRICE_VAL
⚠️ 触发条件：跌破 \$$ALERT_THRESHOLD
⏰ 触发时间：$(date '+%Y-%m-%d %H:%M:%S GMT+8')
━━━━━━━━━━━━━━━━━━
建议关注是否企稳，随时准备操作"

    echo "$MSG"
    
    # 记录已发状态
    python3 -c "
import json
from datetime import datetime
d = {
    'last_alert': '$MSG',
    'last_time': datetime.now().isoformat(),
    'last_time_epoch': $(date +%s),
    'price_triggered': $PRICE_VAL
}
json.dump(d, open('$STATE_FILE', 'w'))
"

    # 通过openclaw发送消息
    echo "ALERT_TRIGGERED"
    exit 0
else
    echo "ETH \$$PRICE_VAL 未触发预警（需<\$$ALERT_THRESHOLD）"
fi
