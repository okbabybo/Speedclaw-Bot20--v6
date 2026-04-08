"""
OKX 永续合约自动交易脚本
由AI信号驱动，自动执行开多/开空/平仓
"""
import hmac
import base64
import hashlib
import time
import json
import requests
from datetime import datetime

# ============ API配置 ============
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET_KEY = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
BASE_URL = "https://www.okx.com"

# ============ 签名工具 ============
def sign(message, secretKey):
    mac = hmac.new(secretKey.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode('utf-8')

def get_headers(path, method, body=''):
    timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    message = timestamp + method + path + body
    signature = sign(message, SECRET_KEY)
    return {
        'Content-Type': 'application/json',
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': signature,
        'OK-ACCESS-TIMESTAMP': timestamp,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
    }

# ============ 交易操作 ============
def place_order(instId, side, posSide, price, sz, tdMode='cross'):
    """下单"""
    path = '/api/v5/trade/order'
    url = BASE_URL + path
    body = json.dumps({
        "instId": instId,
        "tdMode": tdMode,
        "side": side,
        "posSide": posSide,
        "ordType": "limit",
        "px": str(price),
        "sz": str(sz),
    })
    headers = get_headers(path, 'POST', body)
    resp = requests.post(url, data=body, headers=headers, timeout=10)
    data = resp.json()
    if data.get('code') == '0':
        orderId = data['data'][0]['ordId']
        print(f"✅ 下单成功 | 订单ID: {orderId} | {instId} | {side} {posSide} @ {price}")
        return orderId
    else:
        print(f"❌ 下单失败: {data.get('msg')}")
        return None

def close_position(instId, posSide):
    """平仓"""
    path = '/api/v5/trade/close-position'
    url = BASE_URL + path
    body = json.dumps({
        "instId": instId,
        "posSide": posSide,
        "mgnMode": "cross",
    })
    headers = get_headers(path, 'POST', body)
    resp = requests.post(url, data=body, headers=headers, timeout=10)
    data = resp.json()
    if data.get('code') == '0':
        print(f"✅ 平仓成功 | {instId} {posSide}")
        return True
    else:
        print(f"❌ 平仓失败: {data.get('msg')}")
        return False

def get_position(instId):
    """查询持仓"""
    path = f'/api/v5/account/positions?instId={instId}'
    url = BASE_URL + path
    headers = get_headers(path, 'GET')
    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json()
    if data.get('code') == '0':
        positions = data['data']
        if positions:
            for p in positions:
                print(f"持仓 | {p['instId']} | {p['posSide']} | 数量:{p['pos']} | 均价:{p['avgPx']} | PnL:{p['upl']}")
        else:
            print(f"无持仓 | {instId}")
        return positions
    else:
        print(f"❌ 查询失败: {data.get('msg')}")
        return []

def get_account():
    """查询账户"""
    path = '/api/v5/account/balance'
    url = BASE_URL + path
    headers = get_headers(path, 'GET')
    resp = requests.get(url, headers=headers, timeout=10)
    data = resp.json()
    if data.get('code') == '0':
        for bal in data['data'][0]['details']:
            if bal['ccy'] == 'USDT':
                print(f"账户 | USDT余额: {bal['cashBal']} | 可用: {bal['availBal']}")
    else:
        print(f"❌ 查询失败: {data.get('msg')}")

def get_ticker(instId):
    """查询行情"""
    path = f'/api/v5/market/ticker?instId={instId}'
    url = BASE_URL + path
    resp = requests.get(url, timeout=10)
    data = resp.json()
    if data.get('code') == '0':
        t = data['data'][0]
        print(f"行情 | {instId} | 现价: {t['last']} | 24h高: {t['high24h']} | 24h低: {t['low24h']}")
    return data.get('data', [{}])[0].get('last', 'N/A')

# ============ 演示/测试 ============
if __name__ == "__main__":
    print("=" * 40)
    print("OKX 自动交易测试")
    print("=" * 40)
    
    # 1. 查询行情
    print("\n【行情查询】")
    btc_price = get_ticker("BTC-USDT-SWAP")
    eth_price = get_ticker("ETH-USDT-SWAP")
    
    # 2. 查询账户
    print("\n【账户查询】")
    get_account()
    
    # 3. 查询持仓
    print("\n【持仓查询】")
    get_position("BTC-USDT-SWAP")
    get_position("ETH-USDT-SWAP")
    
    print("\n✅ 测试完成！脚本已就绪，等待AI信号驱动。")
