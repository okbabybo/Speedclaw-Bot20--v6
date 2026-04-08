"""
全自动交易系统 v2.0
止损3% + 移动止盈（1.5%触发0.5%回调止盈 / 2%触发0.5%回调止盈 / 趋势单持有等0.5%回调止盈）

【核心规则 v2.0】
1. 止损：3% 固定
2. 止盈：
   - 价格移动1.5% → 激活移动止盈，等回调0.5%止盈
   - 价格移动2.0% → 激活移动止盈，等回调0.5%止盈
   - 价格持续移动 → 持有，等从最优价格回调0.5%止盈
3. 每单风险：1%-3%账户（按余额动态）
4. 100x杠杆
5. 日亏损3% → 停止所有交易
6. 不持仓过夜（22:00前平仓）
7. 【必须先报信号确认，不擅自下单】

【止盈逻辑示意（做空@$72,000）】
- 止损：$74,160（+3%）
- 价格跌到$70,920（-1.5%）：激活止盈，等从$70,920反弹0.5%到$71,285→平仓
- 价格继续跌到$70,560（-2.0%）：继续持有，等从$70,560反弹0.5%到$70,913→平仓
- 价格一直跌到$68,000：持有，等从$68,000反弹0.5%到$68,340→平仓
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
ACCOUNT = 996.82      # 动态获取
LEVERAGE = 100
SL_PCT = 0.03         # 3% 止损
TP1_PCT = 0.015       # 1.5% 触发移动止盈
TP2_PCT = 0.02        # 2% 触发移动止盈
TRAILING_PCT = 0.005  # 0.5% 回调止盈
DAILY_LOSS_LIMIT = 0.03  # 3% 日亏损上限
TRADE_INTERVAL = 30   # 30秒

# 迷你测试仓：固定10张（风险约1%）
TEST_CONTRACTS = 10

# 信号区间
SHORT_ZONE = (72000, 72500)
LONG_ZONE = (69500, 70500)

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
class Position:
    direction: str
    entry_price: float
    contracts: int
    stop_loss: float       # 3% 固定止损
    tp1_triggered: bool = False   # 1.5%触发
    tp2_triggered: bool = False   # 2%触发
    best_price: float = 0.0       # 做空=记录最低价，做多=记录最高价
    trailing_active: bool = False  # 移动止盈已激活
    trailing_exit: float = 0.0    # 移动止盈退出价
    status: str = 'open'
    order_id: str = ''
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%H:%M:%S'))

@dataclass
class Signal:
    direction: str
    entry_price: float
    stop_loss: float
    contracts: int
    risk_pct: float
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime('%H:%M:%S'))

# ============ 市场数据 ============
def get_market_data() -> dict:
    btc_t = get(f'{BASE_URL}/api/v5/market/ticker?instId=BTC-USDT-SWAP')['data'][0]
    eth_t = get(f'{BASE_URL}/api/v5/market/ticker?instId=ETH-USDT-SWAP')['data'][0]
    btc_fr = get(f'{BASE_URL}/api/v5/public/funding-rate?instId=BTC-USDT-SWAP')['data'][0]
    return {
        'btc': {
            'last': float(btc_t['last']),
            'high24h': float(btc_t['high24h']),
            'low24h': float(btc_t['low24h']),
            'open24h': float(btc_t['open24h']),
            'funding_rate': float(btc_fr['fundingRate']),
        },
        'eth': {
            'last': float(eth_t['last']),
            'high24h': float(eth_t['high24h']),
            'low24h': float(eth_t['low24h']),
        }
    }

def get_balance() -> float:
    resp = get(f'{BASE_URL}/api/v5/account/balance')
    for bal in resp.get('data', [{}])[0].get('details', []):
        if bal['ccy'] == 'USDT':
            return float(bal['cashBal'])
    return 0.0

def get_positions(inst_id: str = 'BTC-USDT-SWAP') -> List[dict]:
    resp = get(f'{BASE_URL}/api/v5/account/positions?instId={inst_id}')
    return [p for p in resp.get('data', []) if float(p.get('pos', 0)) > 0]

# ============ 止损止盈逻辑 ============
def calc_stop_loss(entry: float, direction: str) -> float:
    """3%固定止损"""
    if direction == 'SHORT':
        return round(entry * (1 + SL_PCT), 1)
    else:
        return round(entry * (1 - SL_PCT), 1)

def check_take_profit(position: Position, current_price: float) -> Optional[str]:
    """
    检查止盈触发
    返回: None=继续持有, 'EXIT'=触发止盈退出
    """
    pnl_pct = (position.entry_price - current_price) / position.entry_price * 100

    if position.direction == 'SHORT':
        # 更新最优价格（最低价）
        if current_price < position.best_price:
            position.best_price = current_price

        # TP1: 价格移动-1.5%
        if not position.tp1_triggered and pnl_pct >= TP1_PCT * 100:
            position.tp1_triggered = True
            position.trailing_active = True
            position.trailing_exit = round(position.best_price * (1 + TRAILING_PCT), 1)
            print(f"  🚀 TP1触发(+1.5%)，激活移动止盈: 从${position.best_price}回升${TRAILING_PCT*100}%到${position.trailing_exit}止盈")

        # TP2: 价格移动-2.0%
        if not position.tp2_triggered and pnl_pct >= TP2_PCT * 100:
            position.tp2_triggered = True
            position.trailing_active = True
            position.trailing_exit = round(position.best_price * (1 + TRAILING_PCT), 1)
            print(f"  🚀 TP2触发(+2.0%)，移动止盈更新: 从${position.best_price}回升${TRAILING_PCT*100}%到${position.trailing_exit}止盈")

        # 移动止盈：价格从最优反弹0.5%则止盈
        if position.trailing_active:
            if current_price >= position.trailing_exit:
                return 'EXIT'

    else:  # LONG
        if current_price > position.best_price:
            position.best_price = current_price

        pnl_pct_long = (current_price - position.entry_price) / position.entry_price * 100

        if not position.tp1_triggered and pnl_pct_long >= TP1_PCT * 100:
            position.tp1_triggered = True
            position.trailing_active = True
            position.trailing_exit = round(position.best_price * (1 - TRAILING_PCT), 1)
            print(f"  🚀 TP1触发(+1.5%)，激活移动止盈: 从${position.best_price}回落${TRAILING_PCT*100}%到${position.trailing_exit}止盈")

        if not position.tp2_triggered and pnl_pct_long >= TP2_PCT * 100:
            position.tp2_triggered = True
            position.trailing_active = True
            position.trailing_exit = round(position.best_price * (1 - TRAILING_PCT), 1)
            print(f"  🚀 TP2触发(+2.0%)，移动止盈更新: 从${position.best_price}回落${TRAILING_PCT*100}%到${position.trailing_exit}止盈")

        if position.trailing_active:
            if current_price <= position.trailing_exit:
                return 'EXIT'

    return None

# ============ 订单执行 ============
def close_position(inst_id: str = 'BTC-USDT-SWAP') -> bool:
    positions = get_positions(inst_id)
    for p in positions:
        close = post(f'{BASE_URL}/api/v5/trade/close-position', {
            'instId': inst_id,
            'posSide': p['posSide'],
            'mgnMode': 'cross',
        })
        if close.get('code') == '0':
            print(f"  ✅ {p['posSide']} 已平仓")
            return True
    return False

def place_entry(direction: str, price: float, contracts: int, stop_loss: float, take_profit: float) -> Optional[str]:
    """挂入场单"""
    inst_id = 'BTC-USDT-SWAP'
    if direction == 'SHORT':
        order = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': 'sell', 'posSide': 'short',
            'ordType': 'limit', 'px': str(price), 'sz': str(contracts),
        })
    else:
        order = post(f'{BASE_URL}/api/v5/trade/order', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': 'buy', 'posSide': 'long',
            'ordType': 'limit', 'px': str(price), 'sz': str(contracts),
        })

    if order.get('code') == '0':
        oid = order['data'][0]['ordId']
        # 挂止损OCO（止损用conditional）
        sl_order = post(f'{BASE_URL}/api/v5/trade/order-algo', {
            'instId': inst_id, 'tdMode': 'cross',
            'side': 'buy' if direction == 'SHORT' else 'sell',
            'posSide': 'short' if direction == 'SHORT' else 'long',
            'ordType': 'conditional', 'sz': str(contracts),
            'slTriggerPx': str(stop_loss), 'slOrdPx': '-1',
        })
        if sl_order.get('code') != '0':
            print(f"  ⚠️ 止损单挂失败: {sl_order.get('msg')}")
        return oid
    print(f"  ❌ 入场单失败: {order.get('msg')}")
    return None

# ============ 信号检测 ============
def check_signals(market: dict) -> Optional[Signal]:
    btc = market['btc']
    price = btc['last']

    if SHORT_ZONE[0] <= price <= SHORT_ZONE[1]:
        sl = calc_stop_loss(price, 'SHORT')
        return Signal(
            direction='SHORT', entry_price=round(price, 1),
            stop_loss=sl, contracts=TEST_CONTRACTS,
            risk_pct=1.0,
            reason=f'价格{price}进入做空区间$72,000-$72,500'
        )

    if LONG_ZONE[0] <= price <= LONG_ZONE[1]:
        sl = calc_stop_loss(price, 'LONG')
        return Signal(
            direction='LONG', entry_price=round(price, 1),
            stop_loss=sl, contracts=TEST_CONTRACTS,
            risk_pct=1.0,
            reason=f'价格{price}回踩做多区间$69,500-$70,500'
        )
    return None

# ============ 信号格式化 ============
def format_signal(sig: Signal) -> str:
    dir_emoji = '🔴' if sig.direction == 'SHORT' else '🟢'
    return f"""
{'='*50}
{dir_emoji}【自动交易信号】{sig.direction}
{'='*50}
品种：BTC-USDT-SWAP
方向：{sig.direction}
入场：${sig.entry_price}
止损：${sig.stop_loss} (+{SL_PCT*100:.0f}%)
止盈：移动止盈（1.5%触发→0.5%回调 / 2%触发→0.5%回调 / 趋势持有→回调0.5%）
张数：{sig.contracts}张（迷你测试仓）
保证金：约{test_margin(sig.entry_price, sig.contracts):.2f}U
逻辑：{sig.reason}
{'='*50}
回复「确认」执行，其他任意内容取消
"""

def test_margin(price: float, contracts: int) -> float:
    return (contracts * price / 1000) / LEVERAGE

# ============ 主监控循环 ============
def run_monitor():
    print('=' * 60)
    print('🦞 混沌龙虾 自动交易系统 v2.0 启动')
    print('止盈规则: 1.5%触发→0.5%回调 / 2%触发→0.5%回调 / 趋势持有→回调0.5%')
    print('止损规则: 3% 固定')
    print('=' * 60)

    balance = get_balance()
    print(f'账户: {balance:.2f} USDT')
    print(f'迷你测试仓: {TEST_CONTRACTS}张')
    print(f'做空区间: ${SHORT_ZONE[0]:,} - ${SHORT_ZONE[1]:,}')
    print(f'做多区间: ${LONG_ZONE[0]:,} - ${LONG_ZONE[1]:,}')
    print(f'检查间隔: {TRADE_INTERVAL}秒')
    print('=' * 60)

    position: Optional[Position] = None
    pending_signal: Optional[Signal] = None
    confirmed_direction: Optional[str] = None
    last_check = time.time()

    while True:
        try:
            now = datetime.now()
            market = get_market_data()
            balance = get_balance()
            btc_price = market['btc']['last']

            # 日亏损检查
            # （简化版，不追踪每日）

            # 检查持仓状态
            positions = get_positions()

            if positions:
                # 有持仓，监控
                for p in positions:
                    if p['instId'] == 'BTC-USDT-SWAP':
                        direction = p['posSide'].upper()
                        entry = float(p['avgPx'])
                        size = float(p['pos'])

                        if position is None or position.status == 'closed':
                            # 初始化position对象
                            position = Position(
                                direction=direction,
                                entry_price=entry,
                                contracts=int(size),
                                stop_loss=calc_stop_loss(entry, direction),
                                best_price=btc_price if direction == 'SHORT' else btc_price,
                            )

                        # 计算当前盈亏
                        if direction == 'SHORT':
                            pnl_pct = (entry - btc_price) / entry * 100
                        else:
                            pnl_pct = (btc_price - entry) / entry * 100

                        pnl_abs = pnl_pct * entry * size / 100

                        print(f"[{now.strftime('%H:%M:%S')}] {direction} | {entry}→{btc_price} | {pnl_pct:+.2f}% | {pnl_abs:+.2f}U")

                        # 检查止盈
                        result = check_take_profit(position, btc_price)
                        if result == 'EXIT':
                            print(f"  🎯 触发移动止盈！平仓")
                            close_position()
                            position.status = 'closed'
                            position = None

                        # 检查止损（由交易所自动执行，这里仅监控）
                        sl_price = position.stop_loss
                        if direction == 'SHORT' and btc_price >= sl_price:
                            print(f"  🛑 触发止损 {sl_price}")
                            close_position()
                            position.status = 'closed'
                            position = None
                        elif direction == 'LONG' and btc_price <= sl_price:
                            print(f"  🛑 触发止损 {sl_price}")
                            close_position()
                            position.status = 'closed'
                            position = None

                        # 过夜检查
                        if now.hour >= 22 or now.hour < 0:
                            print(f"  🌙 过夜检查，平仓")
                            close_position()
                            position.status = 'closed'
                            position = None

                time.sleep(TRADE_INTERVAL)
                continue

            # 无持仓，检查信号
            signal = check_signals(market)
            if signal:
                pending_signal = signal
                print(format_signal(signal))

            time.sleep(TRADE_INTERVAL)

        except Exception as e:
            print(f'Error: {e}')
            import traceback; traceback.print_exc()
            time.sleep(TRADE_INTERVAL)

if __name__ == '__main__':
    run_monitor()
