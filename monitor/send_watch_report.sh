#!/bin/bash
# 定时盯盘汇报 - 推送至飞书

WORKDIR="/root/.openclaw/workspace"
LOG="$WORKDIR/monitor/cron_report.log"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S GMT+8') 盯盘汇报 ==="
  
  python3 << 'PYEOF'
import hmac, base64, requests, json, datetime, sys
sys.path.insert(0, '/usr/local/lib64/python3.11/site-packages')

API_KEY = 'be046210-77bd-47e6-8524-dee2f2acebd9'
SECRET = '508989F295B579CA787D85F500B9C02E'
PASSPHRASE = 'Fjh872330@'

def get(path):
    ts = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    msg = ts + 'GET' + path
    sig = base64.b64encode(hmac.new(SECRET.encode(), msg.encode(), 'sha256').digest()).decode()
    headers = {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': sig,
        'OK-ACCESS-TIMESTAMP': ts,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json',
    }
    return requests.get(f'https://www.okx.com{path}', headers=headers, timeout=5).json()

headers_pub = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

for inst in ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']:
    tk = requests.get(f'https://www.okx.com/api/v5/market/ticker?instId={inst}', headers=headers_pub, timeout=5).json()['data'][0]
    mp = requests.get(f'https://www.okx.com/api/v5/public/mark-price?instId={inst}', headers=headers_pub, timeout=5).json()['data'][0]
    fr = requests.get(f'https://www.okx.com/api/v5/public/funding-rate?instId={inst}', headers=headers_pub, timeout=5).json()['data'][0]
    last = float(tk['last'])
    mark = float(mp['markPx'])
    basis = (mark - last) / last * 100
    fr_val = float(fr['fundingRate']) * 100
    print(f"{inst}|{last}|{mark}|{basis:.4f}|{fr_val:.4f}|{tk['vol24h']}|{tk['high24h']}|{tk['low24h']}|{tk['bidPx']}|{tk['askPx']}")

bal = get('/api/v5/account/balance')
usdt_eq = next((d['eqUsd'] for d in bal['data'][0]['details'] if d['ccy'] == 'USDT'), '0')
avail_eq = next((d['availEq'] for d in bal['data'][0]['details'] if d['ccy'] == 'USDT'), '0')

orders = get('/api/v5/trade/orders-pending?instType=SWAP')
pending = orders['data']

print(f"账户|USDT净值:{usdt_eq}|可用:{avail_eq}")
for o in pending:
    print(f"挂单|{o['instId']}|{o['side'].upper()}|{o['sz']}张|@{o['px']}|{o['lever']}x")
PYEOF

} >> $LOG 2>&1
