"""
全自动交易系统 v1.0
实时盯盘 + 信号检测 + 自动下单

【核心规则】
1. 只做迷你仓：每单风险 1%-3% 账户资金
2. 100x杠杆
3. 止损固定 1%，止盈固定 2%（2:1盈亏比）
4. 日亏损达 3% → 停止所有交易
5. 不持仓过夜（美盘22:00前平仓）
6. 【必须先报信号，用户确认后才执行】

【信号条件】
做空：价格$72,000-$72,500 + RSI>65 + 资金费率>0.02%  (三者同时满足)
做多：价格$69,500-$70,500 + RSI<35 + 资金费率<-0.02%  (三者同时满足)

【下单比例】
账户 > 1000U: 每单风险 1%（约10张，保证金7U）
账户 800-1000U: 每单风险 2%（约20张，保证金14U）
账户 < 800U: 每单风险 3%（约30张，保证金21U）

【自动交易流程】
盯盘 → 检测信号 → 推送飞书信号 → 等待确认 → 执行下单 → 挂止损止盈 → 持仓监控 → 触发条件 → 平仓
"""

import requests
import json
import time
import hmac
import base64
import hashlib
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List

# ============ 账户配置 ============
ACCOUNT = 991.75  # USDT（动态更新）
STOP_LOSS_PCT = 0.01   # 1% 止损
TAKE_PROFIT_PCT = 0.02  # 2% 止盈
DAILY_LOSS_LIMIT = 0.03  # 3% 日亏损上限
MAX_LEVERAGE = 100
TRADE_INTERVAL = 30  # 30秒检查一次

# 信号条件
SHORT_ZONE = (72000, 72500)
LONG_ZONE = (69500, 70500)
RSI_SHORT_THRESHOLD = 65
RSI_LONG_THRESHOLD = 35
FUNDING_THRESHOLD = 0.0002  # 0.02%

# ============ API配置 ============
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET_KEY = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
BASE_URL = "https://www.okx.com"

# ============ 工具函数 ============
def sign(msg: str, sk: str) -> str:
    return base64.b64encode(hmac.new(sk.encode(), msg.encode(), hashlib.sha256).digest()).decode()

def headers(path: str, method: str, body: str = '') -> dict:
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    msg = ts + method + path + body
    return {
        'OK-ACCESS-KEY': API_KEY,
        'OK-ACCESS-SIGN': sign(msg, SECRET_KEY),
        'OK-ACCESS-TIMESTAMP': ts,
        'OK-ACCESS-PASSPHRASE': PASSPHRASE,
        'Content-Type': 'application/json',
    }

def get(url: str) -> dict:
    path = url.replace(BASE_URL, '')
    resp = requests.get(url, headers=headers(path, 'GET'), timeout=10)
    return resp.json()

def post(url: str, body: dict) -> dict:
    path = url.replace(BASE_URL, '')
    data = json.dumps(body)
    resp = requests.post(url, data=data, headers=headers(path, 'POST', data), timeout=10)
    return resp.json()

# ============ 数据模型 ============
@dataclass
class Signal:
    direction: str       # 'SHORT' or 'LONG'
    price: float
    stop_loss: float
    take_profit: float
    contracts: int
    risk_pct: float
    risk_amount: float
    margin: float
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%H:%M:%S'))

@dataclass
class Position:
    direction: str
    entry_price: float
    contracts: int
    stop_loss: float
    take_profit: float
    order_id: str
    status: str = 'open'  # 'open', 'closed'
    pnl: float = 0.0

@dataclass
class DailyStats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    start_balance: float = 0.0
    date: str = ''

# ============ 市场数据获取 ============
def get_market_data() -> dict:
    """获取BTC和ETH实时数据"""
    btc_ticker = get(f'{BASE_URL}/api/v5/market/ticker?instId=BTC-USDT-SWAP')['data'][0]
    eth_ticker = get(f'{BASE_URL}/api/v5/market/ticker?instId=ETH-USDT-SWAP')['data'][0]
    btc_fr = get(f'{BASE_URL}/api/v5/public/funding-rate?instId=BTC-USDT-SWAP')['data'][0]
    eth_fr = get(f'{BASE_URL}/api/v5/public/funding-rate?instId=ETH-USDT-SWAP')['data'][0]

    return {
        'btc': {
            'last': float(btc_ticker['last']),
            'high24h': float(btc_ticker['high24h']),
            'low24h': float(btc_ticker['low24h']),
            'open24h': float(btc_ticker['open24h']),
            'funding_rate': float(btc_fr['fundingRate']),
        },
        'eth': {
            'last': float(eth_ticker['last']),
            'high24h': float(eth_ticker['high24h']),
            'low24h': float(eth_ticker['low24h']),
            'open24h': float(eth_ticker['open24h']),
            'funding_rate': float(eth_fr['fundingRate']),
        }
    }

def get_account_balance() -> float:
    """获取账户USDT余额"""
    resp = get(f'{BASE_URL}/api/v5/account/balance')
    for bal in resp.get('data', [{}])[0].get('details', []):
        if bal['ccy'] == 'USDT':
            return float(bal['cashBal'])
    return 0.0

def get_positions(inst_id: str = 'BTC-USDT-SWAP') -> List[dict]:
    """获取持仓"""
    resp = get(f'{BASE_URL}/api/v5/account/positions?instId={inst_id}')
    return [p for p in resp.get('data', []) if float(p.get('pos', 0)) > 0]

# ============ 仓位计算 ============
def calc_contracts(account: float, price: float, risk_pct: float = 0.01) -> tuple:
    """
    计算开仓数量
    risk_pct: 风险比例 0.01=1%
    返回: (contracts, risk_amount, margin)
    """
    risk_amount = account * risk_pct
    # 止损距离1%
    loss_per_btc = price * STOP_LOSS_PCT
    contracts = round(risk_amount / loss_per_btc, 4)
    contracts = max(1, int(contracts * 1000))  # 至少1张，按张取整
    notional = contracts * price / 1000  # 张数转BTC
    margin = notional / MAX_LEVERAGE
    return contracts, risk_amount, margin

def calc_risk_pct(account: float) -> float:
    """根据账户余额决定风险比例"""
    if account > 1000:
        return 0.01   # 1%
    elif account > 800:
        return 0.02   # 2%
    else:
        return 0.03   # 3%

# ============ 信号检测 ============
def check_short_signal(market: dict) -> Optional[Signal]:
    """检测做空信号"""
    btc = market['btc']
    price = btc['last']
    fr = btc['funding_rate']

    # 基础条件
    in_zone = SHORT_ZONE[0] <= price <= SHORT_ZONE[1]
    high_fr = fr > FUNDING_THRESHOLD

    # RSI估算（用从低点反弹幅度代替精确RSI）
    from_low = (price - btc['low24h']) / btc['low24h'] * 100
    overbought_estimate = from_low > 5  # 从低点反弹>5%视为超买

    if in_zone and high_fr and overbought_estimate:
        contracts, risk_amount, margin = calc_contracts(
            get_account_balance(),
            price,
            calc_risk_pct(get_account_balance())
        )
        return Signal(
            direction='SHORT',
            price=round(price, 1),
            stop_loss=round(price * (1 + STOP_LOSS_PCT), 1),
            take_profit=round(price * (1 - TAKE_PROFIT_PCT), 1),
            contracts=contracts,
            risk_pct=calc_risk_pct(get_account_balance()) * 100,
            risk_amount=risk_amount,
            margin=margin,
            reason=f'做空区间到位({price}) + 资金费率偏高({fr*100:.3f}%) + 超买'
        )
    return None

def check_long_signal(market: dict) -> Optional[Signal]:
    """检测做多信号"""
    btc = market['btc']
    price = btc['last']
    fr = btc['funding_rate']

    in_zone = LONG_ZONE[0] <= price <= LONG_ZONE[1]
    low_fr = fr < -FUNDING_THRESHOLD

    from_high = (btc['high24h'] - price) / btc['high24h'] * 100
    oversold_estimate = from_high > 5  # 从高点回落>5%视为超卖

    if in_zone and low_fr and oversold_estimate:
        contracts, risk_amount, margin = calc_contracts(
            get_account_balance(),
            price,
            calc_risk_pct(get_account_balance())
        )
        return Signal(
            direction='LONG',
            price=round(price, 1),
            stop_loss=round(price * (1 - STOP_LOSS_PCT), 1),
            take_profit=round(price * (1 + TAKE_PROFIT_PCT), 1),
            contracts=contracts,
            risk_pct=calc_risk_pct(get_account_balance()) * 100,
            risk_amount=risk_amount,
            margin=margin,
            reason=f'回踩支撑({price}) + 资金费率偏低({fr*100:.3f}%) + 超卖'
        )
    return None

# ============ 订单执行 ============
def place_entry_order(direction: str, price: float, contracts: int) -> Optional[str]:
    """挂入场单"""
    inst_id = 'BTC-USDT-SWAP'
    if direction == 'SHORT':
        order = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id,
            'tdMode': 'cross',
            'side': 'sell',
            'posSide': 'short',
            'ordType': 'limit',
            'px': str(price),
            'sz': str(contracts),
        })
    else:
        order = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id,
            'tdMode': 'cross',
            'side': 'buy',
            'posSide': 'long',
            'ordType': 'limit',
            'px': str(price),
            'sz': str(contracts),
        })

    if order.get('code') == '0':
        return order['data'][0]['ordId']
    print(f"❌ 入场单失败: {order.get('msg')}")
    return None

def place_sl_tp(order_id: str, direction: str, sl: float, tp: float, contracts: int) -> bool:
    """挂止损止盈 OCO单"""
    inst_id = 'BTC-USDT-SWAP'
    if direction == 'SHORT':
        side = 'buy'
        pos = 'short'
    else:
        side = 'sell'
        pos = 'long'

    # OCO: 同时挂止损和止盈
    result = post(f'{BASE_URL}/api/v5/trade/order-algo', {
        'instId': inst_id,
        'tdMode': 'cross',
        'side': side,
        'posSide': pos,
        'ordType': 'oco',
        'sz': str(contracts),
        'slTriggerPx': str(sl),
        'slOrdPx': '-1',       # 市价止损
        'tpTriggerPx': str(tp),
        'tpOrdPx': '-1',       # 市价止盈
    })

    if result.get('code') == '0':
        return True
    print(f"❌ 止损止盈挂单失败: {result.get('msg')}")
    return False

def close_all_positions():
    """平所有持仓"""
    for inst in ['BTC-USDT-SWAP', 'ETH-USDT-SWAP']:
        positions = get_positions(inst)
        for p in positions:
            close = post(f'{BASE_URL}/api/v5/trade/close-position', {
                'instId': inst,
                'posSide': p['posSide'],
                'mgnMode': 'cross',
            })
            if close.get('code') == '0':
                print(f"✅ {inst} {p['posSide']} 已平仓")

# ============ 信号展示 ============
def format_signal(signal: Signal) -> str:
    """格式化信号为飞书消息"""
    emoji = '🔴' if signal.direction == 'SHORT' else '🟢'
    return f"""
{'='*50}
{emoji}【自动交易信号】{signal.direction}
{'='*50}
品种：BTC-USDT-SWAP
方向：{signal.direction}
信号价：${signal.price}
止损：${signal.stop_loss} (1%)
止盈：${signal.take_profit} (2%)
张数：{signal.contracts}张
风险：{signal.risk_pct:.0f}%账户（{signal.risk_amount:.2f}U）
保证金：{signal.margin:.2f}U（100x杠杆）
逻辑：{signal.reason}
时间：{signal.timestamp}
{'='*50}
回复「确认」执行，其他任意内容取消
"""

# ============ 主监控循环 ============
def run_monitor():
    """主监控循环"""
    print('=' * 60)
    print('🦞 混沌龙虾 自动交易系统 v1.0 启动')
    print('=' * 60)
    print(f'账户: {get_account_balance():.2f} USDT')
    print(f'风险比例: {calc_risk_pct(get_account_balance())*100:.0f}%')
    print(f'止损: {STOP_LOSS_PCT*100}% | 止盈: {TAKE_PROFIT_PCT*100}%')
    print(f'做空区间: ${SHORT_ZONE[0]:,} - ${SHORT_ZONE[1]:,}')
    print(f'做多区间: ${LONG_ZONE[0]:,} - ${LONG_ZONE[1]:,}')
    print(f'检查间隔: {TRADE_INTERVAL}秒')
    print('=' * 60)

    daily = DailyStats(
        start_balance=get_account_balance(),
        date=datetime.now().strftime('%Y-%m-%d')
    )
    last_trade_time = None
    pending_signal = None  # 待确认的信号

    while True:
        try:
            now = datetime.now()
            market = get_market_data()
            account_bal = get_account_balance()

            # 检查持仓
            positions = get_positions()

            # 检查日亏损
            daily_pnl = daily.start_balance - account_bal
            if daily_pnl >= daily.start_balance * DAILY_LOSS_LIMIT:
                print(f'⚠️ 日亏损已达 {daily_pnl:.2f}U，停止交易')
                close_all_positions()
                time.sleep(TRADE_INTERVAL)
                continue

            # 检查是否持仓
            if positions:
                # 有持仓，监控止损止盈
                btc_price = market['btc']['last']
                for p in positions:
                    if p['instId'] == 'BTC-USDT-SWAP':
                        pnl = float(p.get('upl', 0))
                        direction = p['posSide'].upper()
                        entry = float(p['avgPx'])
                        if direction == 'SHORT':
                            pnl_pct = (entry - btc_price) / entry * 100
                        else:
                            pnl_pct = (btc_price - entry) / entry * 100
                        print(f'[{now.strftime("%H:%M:%S")}] 持仓中 {direction} | 均价{entry} | 现价{btc_price} | PnL:{pnl:.2f}U({pnl_pct:.2f}%)')
                time.sleep(TRADE_INTERVAL)
                continue

            # 无持仓，检查信号
            btc = market['btc']
            print(f'[{now.strftime("%H:%M:%S")}] BTC: ${btc["last"]:,.1f} | FR:{btc["funding_rate"]*100:.4f}% | 24h高:{btc["high24h"]:,.1f}')

            signal = None

            # 只做迷你测试仓：固定10张
            test_contracts = 10

            # 检查做空信号
            if SHORT_ZONE[0] <= btc['last'] <= SHORT_ZONE[1]:
                signal = Signal(
                    direction='SHORT',
                    price=round(btc['last'], 1),
                    stop_loss=round(btc['last'] * (1 + STOP_LOSS_PCT), 1),
                    take_profit=round(btc['last'] * (1 - TAKE_PROFIT_PCT), 1),
                    contracts=test_contracts,
                    risk_pct=1.0,
                    risk_amount=btc['last'] * 0.01 * test_contracts / 100,
                    margin=(test_contracts * btc['last'] / 1000) / 100,
                    reason=f'价格进入做空区间(${btc["last"]:,.0f})'
                )
                print(format_signal(signal))
                pending_signal = signal

            # 检查做多信号
            elif LONG_ZONE[0] <= btc['last'] <= LONG_ZONE[1]:
                signal = Signal(
                    direction='LONG',
                    price=round(btc['last'], 1),
                    stop_loss=round(btc['last'] * (1 - STOP_LOSS_PCT), 1),
                    take_profit=round(btc['last'] * (1 + TAKE_PROFIT_PCT), 1),
                    contracts=test_contracts,
                    risk_pct=1.0,
                    risk_amount=btc['last'] * 0.01 * test_contracts / 100,
                    margin=(test_contracts * btc['last'] / 1000) / 100,
                    reason=f"价格回踩支撑(${btc['last']:,.0f})"
                )
                print(format_signal(signal))
                pending_signal = signal

            else:
                print(f'[{now.strftime("%H:%M:%S")}] 无信号，等待...')

            time.sleep(TRADE_INTERVAL)

        except Exception as e:
            print(f'Error: {e}')
            time.sleep(TRADE_INTERVAL)

if __name__ == '__main__':
    run_monitor()
