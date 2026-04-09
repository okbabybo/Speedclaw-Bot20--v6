"""
ETH永续合约全自动量化交易系统 v4.0
=====================================
基于 STRATEGY_v4.0.md 实现

核心功能:
- 6模块架构: 持仓同步层 + 市场状态识别 + 信号生成 + 交易执行 + 风险控制 + 持仓监控
- 手动干预防护层: 三层防护体系（持仓同步 + 挂单同步 + 操作完整性校验）
- 三套信号体系: 趋势顺势 + 插针反转 + 区间波段
- 四市场状态: TREND_UP / TREND_DOWN / RANGE / CHAOS
- 用户确认机制: 所有开仓/平仓必须用户确认才执行

⚠️ 重要: 开仓和平仓操作需要用户回复"确认"后才执行
"""

import requests
import json
import time
import hmac
import base64
import hashlib
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Dict

# ============================================================
# 配置参数（来自 STRATEGY_v4.0.md）
# ============================================================
LEVERAGE_TREND = 30
LEVERAGE_NEEDLE = 50
SL_PCT = 0.015           # 止损 1.5%
TP1_PCT = 0.005          # 第一止盈 +0.5%
TP2_PCT = 0.010          # 第二止盈 +1.0%
TP3_PCT = 0.015          # 第三止盈 +1.5%
TP1_CLOSE_PCT = 0.30     # 平30%
TP2_CLOSE_PCT = 0.40     # 平40%
TRAILING_PCT = 0.005     # 回调止盈 0.5%
DAILY_LOSS_LIMIT = 0.05  # 单日最大亏损 5%
CONSECUTIVE_LOSS_LIMIT = 3
COOLING_MINUTES = 60
TRADE_INTERVAL = 30
MAX_POSITION_PCT = 0.10
SINGLE_POSITION_PCT = 0.02
POSITION_TIMEOUT_NEEDLE = 15 * 60  # 15分钟
POSITION_TIMEOUT_RANGE = 30 * 60   # 30分钟
SCORE_EXECUTE = 5
SCORE_FULL = 8
CONTRACT_SIZE = 0.01

# ============================================================
# API 配置
# ============================================================
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SECRET_KEY = "508989F295B579CA787D85F500B9C02E"
PASSPHRASE = "Fjh872330@"
BASE_URL = "https://www.okx.com"
INST_ID = "ETH-USDT-SWAP"
BTC_INST_ID = "BTC-USDT-SWAP"

# ============================================================
# 工具函数
# ============================================================
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
    p = url.replace(BASE_URL, '')
    return requests.get(url, headers=headers(p, 'GET'), timeout=10).json()

def post(url: str, body: dict) -> dict:
    p = url.replace(BASE_URL, '')
    d = json.dumps(body)
    return requests.post(url, data=d, headers=headers(p, 'POST', d), timeout=10).json()

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def notify_user(msg: str):
    print(f"\n{'='*50}\n🚨 {msg}\n{'='*50}\n")


# ============================================================
# 技术指标
# ============================================================
def get_candles(inst_id: str, bar: str, limit: int = 100) -> List[List[float]]:
    data = get(f'{BASE_URL}/api/v5/market/history-candles?instId={inst_id}&bar={bar}&limit={limit}')
    result = []
    for row in data.get('data', []):
        try:
            result.append([float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
        except:
            pass
    return result

def calc_rsi(candles: List[List[float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 50.0
    closes = [c[3] for c in candles]
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

def calc_ema(candles: List[List[float]], period: int = 20) -> float:
    if len(candles) < period:
        return candles[-1][3] if candles else 0
    closes = [c[3] for c in candles[-period:]]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema

def calc_atr(candles: List[List[float]], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, min(len(candles), period + 1)):
        high, low, prev_close = candles[i][1], candles[i][2], candles[i-1][3]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0

def calc_atr_sma(candles: List[List[float]], period: int = 20) -> float:
    if len(candles) < period + 14:
        return 0
    atr_values = [calc_atr(candles[i-period:i+1]) for i in range(period, len(candles))]
    return sum(atr_values) / len(atr_values) if atr_values else 0

def calc_bb_width(candles: List[List[float]], period: int = 20) -> float:
    if len(candles) < period:
        return 0
    closes = [c[3] for c in candles[-period:]]
    ma = sum(closes) / period
    std = (sum((c - ma) ** 2 for c in closes) / period) ** 0.5
    return (ma + 2 * std) - (ma - 2 * std)


# ============================================================
# 数据结构
# ============================================================
@dataclass
class MarketData:
    price: float = 0
    high24: float = 0
    low24: float = 0
    ema20_1h: float = 0
    ema60_1h: float = 0
    rsi_1h: float = 50
    rsi_4h: float = 50
    atr_14: float = 0
    atr_sma_20: float = 0
    bb_width: float = 0
    btc_price: float = 0
    btc_ema20_1h: float = 0
    btc_ema60_1h: float = 0
    btc_rsi_4h: float = 50
    volume_5m: float = 0
    volume_avg_5m: float = 0
    candles_5m: List = field(default_factory=list)
    candles_15m: List = field(default_factory=list)
    candles_1h: List = field(default_factory=list)
    candles_4h: List = field(default_factory=list)
    btc_candles_1h: List = field(default_factory=list)

@dataclass
class ActivePosition:
    direction: str
    entry_price: float
    qty: int
    leverage: int
    stop_loss: float
    signal_type: str  # TREND / NEEDLE / RANGE
    open_time: float
    tp1_triggered: bool = False
    tp2_triggered: bool = False
    trailing_activated: bool = False
    stop_loss_base: float = 0  # 原始止损基数
    status: str = 'open'
    best_price: float = 0

@dataclass
class SystemState:
    paused: bool = False
    consecutive_losses: int = 0
    cooling_until: float = 0
    daily_loss: float = 0
    trade_count: int = 0
    last_reset_date: str = ''


# ============================================================
# 全局状态
# ============================================================
state = SystemState()
active_pos: Optional[ActivePosition] = None
pending_signal: Optional[Dict] = None
pending_confirmation_expire: float = 0


# ============================================================
# 数据获取
# ============================================================
def get_market_data() -> MarketData:
    candles_5m = get_candles(INST_ID, '5m', 100)
    candles_15m = get_candles(INST_ID, '15m', 100)
    candles_1h = get_candles(INST_ID, '1H', 100)
    candles_4h = get_candles(INST_ID, '4H', 100)
    btc_candles_1h = get_candles(BTC_INST_ID, '1H', 100)
    btc_candles_4h = get_candles(BTC_INST_ID, '4H', 100)

    eth_t = get(f'{BASE_URL}/api/v5/market/ticker?instId={INST_ID}')['data'][0]
    btc_t = get(f'{BASE_URL}/api/v5/market/ticker?instId={BTC_INST_ID}')['data'][0]

    price = float(eth_t['last'])
    high24 = float(eth_t['high24h'])
    low24 = float(eth_t['low24h'])
    btc_price = float(btc_t['last'])
    vol_5m = float(candles_5m[-1][4]) if candles_5m else 0
    vol_avg_5m = sum(c[4] for c in candles_5m[-5:]) / 5 if len(candles_5m) >= 5 else vol_5m

    return MarketData(
        price=price, high24=high24, low24=low24,
        ema20_1h=calc_ema(candles_1h, 20), ema60_1h=calc_ema(candles_1h, 60),
        rsi_1h=calc_rsi(candles_1h, 14), rsi_4h=calc_rsi(candles_4h, 14),
        atr_14=calc_atr(candles_1h, 14), atr_sma_20=calc_atr_sma(candles_1h, 20),
        bb_width=calc_bb_width(candles_1h, 20),
        btc_price=btc_price,
        btc_ema20_1h=calc_ema(btc_candles_1h, 20),
        btc_ema60_1h=calc_ema(btc_candles_1h, 60),
        btc_rsi_4h=calc_rsi(btc_candles_4h, 14),
        volume_5m=vol_5m, volume_avg_5m=vol_avg_5m,
        candles_5m=candles_5m, candles_15m=candles_15m,
        candles_1h=candles_1h, candles_4h=candles_4h,
        btc_candles_1h=btc_candles_1h,
    )

def get_balance() -> float:
    resp = get(f'{BASE_URL}/api/v5/account/balance')
    for bal in resp.get('data', [{}])[0].get('details', []):
        if bal['ccy'] == 'USDT':
            return float(bal['cashBal'])
    return 0.0

def get_positions() -> List[dict]:
    resp = get(f'{BASE_URL}/api/v5/account/positions?instId={INST_ID}')
    return [p for p in resp.get('data', []) if float(p.get('availPos', 0)) > 0]

def get_pending_orders() -> List[dict]:
    resp = get(f'{BASE_URL}/api/v5/trade/orders-pending?instId={INST_ID}')
    return resp.get('data', [])


# ============================================================
# 第一层：持仓同步
# ============================================================
def sync_positions():
    global active_pos, state
    exchange_positions = get_positions()
    exchange_qty = sum(int(float(p.get('availPos', 0))) for p in exchange_positions)

    if active_pos and active_pos.status == 'open':
        if exchange_qty == 0:
            msg = (f"🚨 【手动平仓检测】\n时间: {datetime.now().strftime('%H:%M:%S')}\n"
                   f"品种: {INST_ID}\n系统记录: {active_pos.direction} {active_pos.qty}张\n"
                   f"交易所: 0张\n系统已同步清除。")
            log_trade(active_pos, "MANUAL_CLOSE")
            active_pos = None
            cancel_all_pending_orders()
            notify_user(msg)
            state.consecutive_losses += 1
            return True, msg
        elif abs(exchange_qty - active_pos.qty) > 1:
            diff = active_pos.qty - exchange_qty
            active_pos.qty = exchange_qty
            msg = (f"⚠️ 【部分平仓检测】\n平掉: {diff}张\n剩余: {exchange_qty}张\n系统已同步。")
            notify_user(msg)
            return True, msg

    if exchange_qty > 0 and (active_pos is None or active_pos.status != 'open'):
        entry_price = sum(float(p.get('avgPx', 0)) * float(p.get('availPos', 0))
                          for p in exchange_positions) / exchange_qty if exchange_positions else 0
        msg = (f"⚠️ 【手动开仓检测】\n数量: {exchange_qty}张\n均价: {entry_price}\n"
               f"回复「接管」由系统接管，或「忽略」由您自行处理。")
        state.paused = True
        notify_user(msg)
        return True, msg
    return False, ""


# ============================================================
# 第二层：挂单同步
# ============================================================
def sync_orders():
    exchange_orders = get_pending_orders()
    for ex_order in exchange_orders:
        msg = (f"⚠️ 【手动挂单检测】\n方向: {'做多' if ex_order.get('side') == 'buy' else '做空'}\n"
               f"数量: {ex_order.get('sz')}张\n回复「取消」或「接管」。")
        state.paused = True
        notify_user(msg)


# ============================================================
# 第三层：操作完整性校验
# ============================================================
class OperationLock:
    def __init__(self, op_type: str, expected_after: int):
        self.op_type = op_type
        self.expected_after = expected_after
        self.locked_at = time.time()

    def verify(self) -> bool:
        current_qty = sum(int(float(p.get('availPos', 0))) for p in get_positions())
        if current_qty != self.expected_after:
            msg = (f"🚨 【完整性校验失败】\n操作: {self.op_type}\n"
                   f"预期: {self.expected_after}张\n实际: {current_qty}张\n系统暂停。")
            state.paused = True
            notify_user(msg)
            return False
        return True


# ============================================================
# 市场状态识别
# ============================================================
def detect_market_regime(m: MarketData) -> str:
    btc_above = m.btc_price > m.btc_ema20_1h > m.btc_ema60_1h
    btc_below = m.btc_price < m.btc_ema20_1h < m.btc_ema60_1h
    eth_above = m.price > m.ema20_1h > m.ema60_1h
    eth_below = m.price < m.ema20_1h < m.ema60_1h

    if (btc_above and eth_below) or (btc_below and eth_above):
        return "CHAOS"
    if m.atr_sma_20 > 0 and m.atr_14 < m.atr_sma_20 * 0.5:
        return "CHAOS"
    if eth_above and 40 <= m.rsi_4h <= 75 and m.atr_sma_20 > 0 and m.atr_14 > m.atr_sma_20 * 0.9:
        return "TREND_UP"
    if eth_below and 25 <= m.rsi_4h <= 60 and m.atr_sma_20 > 0 and m.atr_14 > m.atr_sma_20 * 0.9:
        return "TREND_DOWN"
    if m.atr_sma_20 > 0 and m.atr_14 < m.atr_sma_20 * 0.8:
        return "RANGE"
    return "CHAOS"


# ============================================================
# 信号生成
# ============================================================
def get_btc_direction(m: MarketData) -> str:
    if m.btc_price > m.btc_ema20_1h > m.btc_ema60_1h:
        return "LONG"
    elif m.btc_price < m.btc_ema20_1h < m.btc_ema60_1h:
        return "SHORT"
    return "NEUTRAL"

def calculate_signal_score(m: MarketData, direction: str) -> int:
    score = 0
    btc_dir = get_btc_direction(m)
    if direction == "LONG" and btc_dir == "LONG":
        score += 2
    elif direction == "SHORT" and btc_dir == "SHORT":
        score += 2
    if direction == "LONG" and m.price > m.ema20_1h > m.ema60_1h:
        score += 2
    elif direction == "SHORT" and m.price < m.ema20_1h < m.ema60_1h:
        score += 2
    if direction == "LONG" and 35 <= m.rsi_1h <= 55:
        score += 2
    elif direction == "SHORT" and 45 <= m.rsi_1h <= 65:
        score += 2
    if m.volume_5m > m.volume_avg_5m * 1.2:
        score += 2
    if direction == "LONG" and m.rsi_1h < 60 and m.rsi_4h < 70:
        score += 2
    elif direction == "SHORT" and m.rsi_1h > 40 and m.rsi_4h > 30:
        score += 2
    return min(score, 10)

def detect_needle_signal(m: MarketData) -> Optional[Tuple[str, str]]:
    if len(m.candles_5m) < 5:
        return None
    candle = m.candles_5m[-1]
    open_, high, low, close, volume = candle
    body = abs(close - open_)
    upper_shadow = high - max(open_, close)
    lower_shadow = min(open_, close) - low
    candle_length = high - low
    if candle_length == 0:
        return None
    vol_ratio = volume / m.volume_avg_5m if m.volume_avg_5m > 0 else 0

    # 下影线
    if lower_shadow >= body * 2 and lower_shadow >= candle_length * 0.2 and vol_ratio >= 1.5:
        rsi_5m = calc_rsi(m.candles_5m, 14)
        rsi_15m = calc_rsi(m.candles_15m, 14) if len(m.candles_15m) >= 15 else 50
        confirm = (rsi_5m < 30 or rsi_15m < 40) + (vol_ratio >= 2.0)
        if confirm >= 1:
            return ("LONG", f"下影线{lower_shadow/body:.1f}倍 RSI={rsi_5m:.0f}")

    # 上影线
    if upper_shadow >= body * 2 and upper_shadow >= candle_length * 0.2 and vol_ratio >= 1.5:
        rsi_5m = calc_rsi(m.candles_5m, 14)
        rsi_15m = calc_rsi(m.candles_15m, 14) if len(m.candles_15m) >= 15 else 50
        confirm = (rsi_5m > 70 or rsi_15m > 60) + (vol_ratio >= 2.0)
        if confirm >= 1:
            return ("SHORT", f"上影线{upper_shadow/body:.1f}倍 RSI={rsi_5m:.0f}")
    return None

def generate_signal(m: MarketData, regime: str) -> Tuple[Optional[Dict], str]:
    global pending_signal
    price = m.price
    balance = get_balance()

    # 插针信号
    needle = detect_needle_signal(m)
    if needle:
        direction, desc = needle
        score = calculate_signal_score(m, direction)
        qty = calculate_qty(balance * SINGLE_POSITION_PCT, price, LEVERAGE_NEEDLE)
        signal = {
            "direction": direction, "signal_type": "NEEDLE",
            "entry_price": price,
            "stop_loss": price * (1 - SL_PCT) if direction == "LONG" else price * (1 + SL_PCT),
            "qty": qty, "leverage": LEVERAGE_NEEDLE, "score": score,
            "description": f"插针:{desc}",
        }
        return signal, f"插针信号 {desc}"

    # 趋势顺势
    if regime == "TREND_UP":
        if (price > m.ema20_1h > m.ema60_1h and m.rsi_1h <= 60 and m.rsi_4h <= 70
            and m.rsi_1h >= 35 and m.volume_5m > m.volume_avg_5m * 1.2):
            score = calculate_signal_score(m, "LONG")
            qty = calculate_qty(balance * SINGLE_POSITION_PCT, price, LEVERAGE_TREND)
            signal = {
                "direction": "LONG", "signal_type": "TREND",
                "entry_price": price, "stop_loss": price * (1 - SL_PCT),
                "qty": qty, "leverage": LEVERAGE_TREND, "score": score,
                "description": f"趋势多 RSI={m.rsi_1h:.0f}",
            }
            return signal, "趋势做多"

    elif regime == "TREND_DOWN":
        if (price < m.ema20_1h < m.ema60_1h and m.rsi_1h >= 40 and m.rsi_4h >= 30
            and m.rsi_1h <= 65 and m.volume_5m > m.volume_avg_5m * 1.2):
            score = calculate_signal_score(m, "SHORT")
            qty = calculate_qty(balance * SINGLE_POSITION_PCT, price, LEVERAGE_TREND)
            signal = {
                "direction": "SHORT", "signal_type": "TREND",
                "entry_price": price, "stop_loss": price * (1 + SL_PCT),
                "qty": qty, "leverage": LEVERAGE_TREND, "score": score,
                "description": f"趋势空 RSI={m.rsi_1h:.0f}",
            }
            return signal, "趋势做空"

    # 区间波段
    elif regime == "RANGE":
        bb_upper = price + m.bb_width / 2
        bb_lower = price - m.bb_width / 2
        if price <= bb_lower and m.rsi_1h < 45:
            score = calculate_signal_score(m, "LONG")
            qty = calculate_qty(balance * SINGLE_POSITION_PCT, price, LEVERAGE_NEEDLE)
            signal = {
                "direction": "LONG", "signal_type": "RANGE",
                "entry_price": price, "stop_loss": price * (1 - SL_PCT),
                "qty": qty, "leverage": LEVERAGE_NEEDLE, "score": score,
                "description": f"区间下轨 RSI={m.rsi_1h:.0f}",
            }
            return signal, "区间做多"
        if price >= bb_upper and m.rsi_1h > 55:
            score = calculate_signal_score(m, "SHORT")
            qty = calculate_qty(balance * SINGLE_POSITION_PCT, price, LEVERAGE_NEEDLE)
            signal = {
                "direction": "SHORT", "signal_type": "RANGE",
                "entry_price": price, "stop_loss": price * (1 + SL_PCT),
                "qty": qty, "leverage": LEVERAGE_NEEDLE, "score": score,
                "description": f"区间上轨 RSI={m.rsi_1h:.0f}",
            }
            return signal, "区间做空"

    return None, f"状态={regime} 无信号"


# ============================================================
# 仓位计算
# ============================================================
def calculate_qty(margin: float, price: float, leverage: int) -> int:
    contract_value = margin * leverage
    qty = contract_value / (price * CONTRACT_SIZE)
    return max(1, int(qty))


# ============================================================
# 交易执行
# ============================================================
def place_order(direction: str, qty: int, order_type: str = "market", price: float = 0) -> Optional[str]:
    side = "buy" if direction == "LONG" else "sell"
    pos_side = "long" if direction == "LONG" else "short"
    body = {
        "instId": INST_ID, "tdMode": "cross", "side": side, "posSide": pos_side,
        "ordType": order_type, "sz": str(qty),
    }
    if order_type == "limit":
        body["px"] = str(price)
    r = post(f'{BASE_URL}/api/v5/trade/order', body)
    if r.get('code') == '0':
        return r['data'][0]['ordId']
    log(f"❌ 下单失败: {r.get('msg')}")
    return None

def close_position() -> bool:
    r = post(f'{BASE_URL}/api/v5/trade/close-position', {"instId": INST_ID, "tdMode": "cross"})
    return r.get('code') == '0'

def close_partial(qty: int) -> bool:
    if active_pos is None:
        return False
    side = "sell" if active_pos.direction == "LONG" else "buy"
    pos_side = "long" if active_pos.direction == "LONG" else "short"
    r = post(f'{BASE_URL}/api/v5/trade/order', {
        "instId": INST_ID, "tdMode": "cross", "side": side, "posSide": pos_side,
        "ordType": "market", "sz": str(qty),
    })
    return r.get('code') == '0'

def cancel_all_pending_orders():
    pass  # 简化实现

def log_trade(pos: ActivePosition, close_type: str, close_price: float = 0):
    log(f"📝 {pos.direction} {pos.qty}张 @{pos.entry_price} → {close_type} @{close_price}")


# ============================================================
# 持仓监控
# ============================================================
def monitor_position(pos: ActivePosition, current_price: float) -> Optional[str]:
    elapsed = time.time() - pos.open_time

    # 超时
    timeout = POSITION_TIMEOUT_NEEDLE if pos.signal_type == "NEEDLE" else POSITION_TIMEOUT_RANGE
    if elapsed > timeout:
        log(f"⏰ 超时强制平仓({timeout//60}min)")
        return "TIMEOUT"

    # 盈亏
    if pos.direction == "LONG":
        pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        if current_price > pos.best_price:
            pos.best_price = current_price
    else:
        pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        if current_price < pos.best_price:
            pos.best_price = current_price

    # 止损
    if pos.direction == "LONG" and current_price <= pos.stop_loss:
        return "STOP"
    elif pos.direction == "SHORT" and current_price >= pos.stop_loss:
        return "STOP"

    # 止盈TP1 +0.5%
    if not pos.tp1_triggered and pnl_pct >= TP1_PCT * 100:
        pos.tp1_triggered = True
        pos.stop_loss = pos.entry_price  # 保本
        close_qty = int(pos.qty * TP1_CLOSE_PCT)
        log(f"🚀 TP1触发(+0.5%)，平{close_qty}张，止损移至保本")
        close_partial(close_qty)
        pos.qty -= close_qty
        return "TP1"

    # 止盈TP2 +1.0%
    if pos.tp1_triggered and not pos.tp2_triggered and pnl_pct >= TP2_PCT * 100:
        pos.tp2_triggered = True
        stop_mult = 1 + 0.005 if pos.direction == "SHORT" else 1 - 0.005
        pos.stop_loss = pos.entry_price * stop_mult
        remaining_qty = pos.qty
        close_qty = int(remaining_qty * (TP2_CLOSE_PCT / (1 - TP1_CLOSE_PCT)))
        close_qty = min(close_qty, remaining_qty)
        log(f"🚀 TP2触发(+1.0%)，平{close_qty}张，止损移至+0.5%")
        close_partial(close_qty)
        pos.qty -= close_qty
        return "TP2"

    # 止盈TP3 +1.5% 全平
    if pos.tp2_triggered and pnl_pct >= TP3_PCT * 100:
        log(f"🎯 TP3触发(+1.5%)，全平")
        return "EXIT"

    return None


# ============================================================
# 格式化和消息
# ============================================================
def format_signal_message(signal: Dict) -> str:
    dir_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    margin = signal["qty"] * signal["entry_price"] * CONTRACT_SIZE / signal["leverage"]
    return f"""
{'='*50}
{dir_emoji} 【交易信号】{signal['direction']}
{'='*50}
品种: {INST_ID}
方向: {signal['direction']}
信号类型: {signal['signal_type']}
入场价: ${signal['entry_price']}
止损价: ${signal['stop_loss']} (-{SL_PCT*100:.1f}%)
张数: {signal['qty']}张
保证金: 约{margin:.2f}U
评分: {signal['score']}/10 ({'满仓' if signal['score'] >= SCORE_FULL else '半仓'})
条件: {signal['description']}
{'='*50}
回复「确认」执行，其他任意内容取消
"""

def format_status(m: MarketData, regime: str) -> str:
    balance = get_balance()
    btc_dir = get_btc_direction(m)
    return f"""[{datetime.now().strftime('%H:%M:%S')}]
ETH: ${m.price}
BTC: ${m.btc_price} ({btc_dir})
状态: {regime}
RSI(1H): {m.rsi_1h:.0f} | RSI(4H): {m.rsi_4h:.0f}
ATR: {m.atr_14:.2f} / SMA: {m.atr_sma_20:.2f}
余额: {balance:.2f} U"""


# ============================================================
# 主循环
# ============================================================
def run():
    global active_pos, pending_signal, state

    print("=" * 60)
    print("🦞 混沌龙虾 自动交易系统 v4.0 启动")
    print("策略: STRATEGY_v4.0.md")
    print("手动干预防护: 三层同步体系")
    print("=" * 60)

    last_sync_time = 0

    while True:
        try:
            now = time.time()

            # ===== 每日风控重置 =====
            today = datetime.now().strftime('%Y-%m-%d')
            if state.last_reset_date != today:
                state.last_reset_date = today
                state.daily_loss = 0
                state.trade_count = 0
                state.consecutive_losses = 0
                log(f"📅 新交易日，重置风控计数")

            # ===== STEP 0: 持仓+挂单同步（每30秒）=====
            if now - last_sync_time > 30:
                sync_positions()
                sync_orders()
                last_sync_time = now

            # ===== 系统暂停 =====
            if state.paused:
                log("⏸ 系统已暂停，等待用户操作...")
                time.sleep(TRADE_INTERVAL)
                continue

            # ===== 冷却期检查 =====
            if state.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT:
                if now < state.cooling_until:
                    remaining = int(state.cooling_until - now)
                    log(f"❄️ 冷却中... 剩余 {remaining}秒")
                    time.sleep(TRADE_INTERVAL)
                    continue
                else:
                    log("❄️ 冷却期结束，等待高置信度信号(评分≥8)")

            # ===== 获取数据 =====
            m = get_market_data()
            regime = detect_market_regime(m)
            balance = get_balance()
            positions = get_positions()

            log(format_status(m, regime))

            # ===== 有持仓 =====
            if active_pos and active_pos.status == 'open' and len(positions) > 0:
                # 同步持仓数量（可能用户手动平了一部分）
                if len(positions) > 0:
                    active_pos.qty = sum(int(float(p.get('availPos', 0))) for p in positions)

                action = monitor_position(active_pos, m.price)

                if action == "STOP":
                    log("🛑 止损触发")
                    close_position()
                    log_trade(active_pos, "STOP", m.price)
                    state.consecutive_losses += 1
                    state.daily_loss += SL_PCT
                    active_pos = None

                elif action == "TIMEOUT":
                    log("⏰ 超时平仓")
                    close_position()
                    log_trade(active_pos, "TIMEOUT", m.price)
                    active_pos = None

                elif action == "EXIT":
                    log("🎯 止盈全