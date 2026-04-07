#!/bin/bash
# 混沌龙虾实时行情监控系统
# 每5分钟运行一次，有大变化推送给用户

API_KEY="be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET="508989F295B579CA787D85F500B9C02E"
PASSPHRASE="Fjh872330@"
FEISHU_ID="ou_ce5a94cfca07b266414b003138b8f1f8"

cd /root/.openclaw/workspace/signals

# 获取当前状态
BTC=$(curl -s "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['last'])")
ETH=$(curl -s "https://www.okx.com/api/v5/market/ticker?instId=ETH-USDT-SWAP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['last'])")

echo "[$(date '+%H:%M')] BTC: $BTC | ETH: $ETH"
