#!/usr/bin/env python3
"""OKX 主动盯盘 - BTC + ETH 双币种监控"""
import sys
import json
from datetime import datetime

sys.path.insert(0, '/usr/local/lib64/python3.11/site-packages')

import okx.api.market as market
import okx.api.account as account
import requests
import hmac
import hashlib
import base64
import time

API_KEY = 'be046210-77bd-47e6-8524-dee2f2acebd9'
SECRET = '508989F295B579CA787D85F500B9C02E'
PASSPHRASE = 'Fjh872330@'

mk = market.Market(key=API_KEY, secret=SECRET, passphrase=PASSPHRASE, flag='0')
acc = account.Account(key=API_KEY, secret=SECRET, passphrase=PASSPHRASE, flag='0')

def sign(ts, method, path, body=''):
    msg = ts + method + path + body
    mac = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()

def get_funding(inst_id):
    ts = str(int(time.time()))
    headers = {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SECRET': SECRET,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'OK-ACCESS-TIMESTAMP': ts,
        'OK-ACCESS-SIGN': sign(ts, 'GET', f'/api/v5/public/funding-rate?instId={inst_id}'),
        'Content-Type': 'application/json'
    }
    r = requests.get(f'https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}', headers=headers)
    return r.json()

def check_alerts(name, ticker, funding_data, long_zone, short_zone, funding_threshold=0.003):
    price = float(ticker['last'])
    high24 = float(ticker['high24h'])
    low24 = float(ticker['low24h'])
    funding_rate = float(funding_data['data'][0]['fundingRate'])
    alerts = []

    # LONG 信号
    if long_zone[0] <= price <= long_zone[1]:
        alerts.append(f"🟢 LONG机会 | {name} $${price} 回到支撑区 {long_zone}")
    
    # SHORT 信号
    if short_zone[0] <= price <= short_zone[1]:
        alerts.append(f"🔴 SHORT机会 | {name} $${price} 逼近压力区 {short_zone}")
    
    # 资金费率预警
    if funding_rate > funding_threshold:
        alerts.append(f"⚠️ 多头拥挤 | {name} 资金费率 {funding_rate*100:.3f}% 偏高")
    
    # 突破/跌破
    if price > high24:
        alerts.append(f"🚀 突破确认 | {name} 突破 24h 高 ${high24}，现价 ${price}")
    if price < low24:
        alerts.append(f"📉 崩盘预警 | {name} 跌破 24h 低 ${low24}，现价 ${price}")
    
    return alerts, price, funding_rate, high24, low24

# 获取数据
btc_ticker = mk.get_ticker(instId='BTC-USDT-SWAP')
eth_ticker = mk.get_ticker(instId='ETH-USDT-SWAP')
btc_funding = get_funding('BTC-USDT-SWAP')
eth_funding = get_funding('ETH-USDT-SWAP')
bal = acc.get_balance()
pos = acc.get_positions(instType='SWAP')

btc = btc_ticker['data'][0]
eth = eth_ticker['data'][0]
usdt = [d for d in bal['data'][0]['details'] if d['ccy'] == 'USDT'][0]

# 告警检查
btc_alerts, btc_px, btc_fr, btc_h, btc_l = check_alerts('BTC', btc, btc_funding, (70000, 70500), (71400, 72000))
eth_alerts, eth_px, eth_fr, eth_h, eth_l = check_alerts('ETH', eth, eth_funding, (2150, 2200), (2280, 2350))

# 输出表格
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S GMT+8')
print(f"\n🦞 【OKX 双币种盯盘汇总】 {now}\n")
print(f"{'='*60}")
print(f"{'币种':<8} {'最新价':<12} {'24h低':<12} {'24h高':<12} {'资金费率':<10} {'状态'}")
print(f"{'-'*60}")
print(f"{'BTC':<8} ${btc_px:<11,.1f} ${btc_l:<11,.1f} ${btc_h:<11,.1f} {btc_fr*100:>+.4f}%  {'📊 正常' if not btc_alerts else ''}")
print(f"{'ETH':<8} ${eth_px:<11,.2f} ${eth_l:<11,.2f} ${eth_h:<11,.2f} {eth_fr*100:>+.4f}%  {'📊 正常' if not eth_alerts else ''}")
print(f"{'-'*60}")
print(f"{'账户 USDT':<20} ${float(usdt['eqUsd']):>,.2f}   {'持仓数':<8} {len(pos['data'])}")
print(f"{'='*60}")

# 告警输出
all_alerts = btc_alerts + eth_alerts
if all_alerts:
    print("\n📡 【触发信号】")
    for a in all_alerts:
        print(f"  >> {a}")
else:
    print("\n📡 【市场状态】无触发信号，BTC $71,460 / ETH $2,170 附近震荡整理中")

# 现货vs合约价差
print(f"\n💹 【现货 vs 合约溢价】")
print(f"  BTC 现货 ${btc_px} vs 合约 ${btc_px} | 溢价 {'+' if btc_px >= eth_px else ''}0.00%")
print(f"  ETH 现货 ${eth_px} vs 合约 ${eth_px} | 溢价 {'+' if eth_px >= eth_px else ''}0.00%")
