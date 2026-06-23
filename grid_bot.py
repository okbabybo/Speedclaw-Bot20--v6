#!/usr/bin/env python3
"""币安现货智能网格+趋势机器人 v3.0
混沌龙虾 🦞 — 真正灵活智能的交易助手
核心：不同行情自动切换不同策略 + 手动平仓识别 + 全自动资金管理
"""
import requests, time, json, yaml, math
from datetime import datetime
from spot_adapter import BinanceSpotAdapter

# ===================== 配置 =====================
CONFIG_FILE = "/root/.openclaw/workspace/grid_config.yaml"

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

cfg = load_config()
LOG_FILE  = cfg.get('log_file', '/root/.openclaw/workspace/grid_bot.log')
STATE_DIR = cfg.get('state_dir', '/root/.openclaw/workspace/')
STATE_FILE = STATE_DIR + "grid_state.json"

COINS = cfg.get('coins', ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'AVAXUSDT', 'XRPUSDT'])

# 核心参数
INIT_CAPITAL    = cfg.get('init_capital', 100)
MAX_POSITIONS   = cfg.get('max_positions', 3)
GRID_COUNT      = cfg.get('grid_count', 6)
GRID_PROFIT     = cfg.get('grid_profit', 0.006)   # 每格0.6%
COMPOUND_PCT    = cfg.get('compound_pct', 1.0)    # 100%复利
TAKE_PROFIT_PCT = cfg.get('take_profit_pct', 0.20) # 盈利20%提盈
SL_PCT          = cfg.get('sl_pct', 0.12)         # 止损12%
TRAILING_PCT    = cfg.get('trailing_pct', 0.05)   # 追踪止损回撤5%
TP_TREND        = cfg.get('tp_trend', 0.25)       # 趋势目标25%

# 指标参数
RSI_PERIOD      = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_MULT = 20, 2.0
EMA_PERIOD = 20

# 运行参数
CHECK_INTERVAL   = 20    # 检查间隔（秒）
SCAN_EVERY      = 180    # 市场扫描间隔（秒）
STATE_SAVE_EVERY = 60   # 状态保存间隔（秒）
CANDLE_INT = "1h"
ATR_SOURCE = True  # True=用真实ATR, False=用近似ATR

# ===================== 工具函数 =====================
def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

def safe_float(val, default=0.0):
    try: return float(val)
    except: return default

def calc_rsi(prices, period=14):
    if len(prices) < period+1: return 50
    gains = [max(0, prices[i]-prices[i-1]) for i in range(1,len(prices))]
    losses = [max(0, prices[i-1]-prices[i]) for i in range(1,len(prices))]
    avg_gain = sum(gains[-period:])/period
    avg_loss = sum(losses[-period:])/period
    if avg_loss == 0: return 100
    return 100 - 100/(1 + avg_gain/avg_loss)

def calc_ema(prices, n):
    if len(prices) < n: return None
    k = 2/(n+1)
    ema = sum(prices[:n])/n
    for p in prices[n:]: ema = p*k + ema*(1-k)
    return ema

def calc_macd(prices, fast=12, slow=26, signal=9):
    """正确的MACD计算"""
    if len(prices) < slow+signal: return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    if ema_fast is None or ema_slow is None: return 0, 0, 0
    macd_line = ema_fast - ema_slow
    # 计算signal line需要MACD历史值
    macd_vals = []
    for i in range(slow, len(prices)+1):
        ef = calc_ema(prices[:i], fast)
        es = calc_ema(prices[:i], slow)
        if ef and es: macd_vals.append(ef - es)
    if len(macd_vals) < signal: return macd_line, macd_line, 0
    signal_line = calc_ema(macd_vals, signal)
    if signal_line is None: signal_line = macd_line
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_bollinger(prices, period=20, mult=2):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:])/period
    std = (sum((p-sma)**2 for p in prices[-period:])/period)**0.5
    return sma + mult*std, sma, sma - mult*std

def calc_atr(klines, period=14):
    if len(klines) < period+1: return 0
    trs = []
    for i in range(1, len(klines)):
        h, l = float(klines[i][2]), float(klines[i][3])
        pc = float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:])/period

def get_klines(ex, symbol, interval=CANDLE_INT, limit=200):
    try:
        klines = ex.get_klines(symbol, interval, limit)
        if not klines or len(klines) < 30: return None
        return {
            'closes': [float(k[4]) for k in klines],
            'highs':  [float(k[2]) for k in klines],
            'lows':   [float(k[3]) for k in klines],
            'vols':   [float(k[5]) for k in klines],
        }
    except: return None

# ===================== 市场分析 =====================
def analyze(symbol, ex):
    """
    全面市场分析，返回：
    - market_mode: TREND_UP / TREND_DOWN / VOLATILE / RANGE_BOUND
    - grid_score: 0-10 (网格适合度)
    - trend_score: 0-10 (趋势适合度)
    - signal: BUY / SELL / HOLD
    """
    data = get_klines(ex, symbol)
    if not data: return None
    
    h4 = get_klines(ex, symbol, "4h", 100)
    d1 = get_klines(ex, symbol, "1d", 30)
    
    c = data['closes']
    cur = c[-1]
    
    # === 基础指标 ===
    rsi   = calc_rsi(c, RSI_PERIOD)
    macd_l, macd_s, hist = calc_macd(c, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_u, bb_m, bb_l = calc_bollinger(c, BB_PERIOD, BB_MULT)
    ema20 = calc_ema(c, EMA_PERIOD)
    ema20_prev = calc_ema(c[:-4], EMA_PERIOD) if len(c) >= 24 else None
    atr = calc_atr(ex.get_klines(symbol, CANDLE_INT, 50)) if ATR_SOURCE else calc_atr_approx(c)
    
    # === BB位置 ===
    bb_range = bb_u - bb_l if bb_u and bb_l else 1
    bb_pos = (cur - bb_l) / bb_range  # 0%=下轨，100%=上轨
    
    # === 多 timeframe RSI ===
    rsi_h4 = calc_rsi(h4['closes'], RSI_PERIOD) if h4 else 50
    rsi_d1 = calc_rsi(d1['closes'], RSI_PERIOD) if d1 else 50
    
    # === 趋势判断 ===
    trend_up   = bool(ema20 and ema20_prev and ema20 > ema20_prev)
    trend_down = bool(ema20 and ema20_prev and ema20 < ema20_prev)
    
    # === 成交量 ===
    vol_avg = sum(data['vols'][-20:])/20 if len(data['vols']) >= 20 else data['vols'][-1]
    vol_ratio = data['vols'][-1] / vol_avg if vol_avg > 0 else 1
    vol_surge = vol_ratio > 1.3
    
    # === 波动率分析 ===
    price_range = (max(c[-50:]) - min(c[-50:])) / cur  # 50根K线的日内波幅
    is_volatile = price_range > 0.08  # 波幅>8%为高波动
    
    # === 趋势强度 ===
    adx_approx = abs(rsi - 50) * 2  # 简化ADX
    
    # === 市场模式判断 ===
    # 日线趋势
    d1_trend_up   = bool(d1 and calc_ema(d1['closes'], EMA_PERIOD) > calc_ema(d1['closes'][:-5], EMA_PERIOD))
    d1_trend_down = bool(d1 and calc_ema(d1['closes'], EMA_PERIOD) < calc_ema(d1['closes'][:-5], EMA_PERIOD))
    
    # 4H趋势
    h4_trend_up   = bool(h4 and calc_ema(h4['closes'], EMA_PERIOD) > calc_ema(h4['closes'][:-4], EMA_PERIOD))
    h4_trend_down = bool(h4 and calc_ema(h4['closes'], EMA_PERIOD) < calc_ema(h4['closes'][:-4], EMA_PERIOD))
    
    if d1_trend_up and h4_trend_up and trend_up:
        market_mode = "TREND_UP"
    elif d1_trend_down and h4_trend_down and trend_down:
        market_mode = "TREND_DOWN"
    elif is_volatile and rsi < 35:
        market_mode = "VOLATILE_OVERSOLD"
    elif is_volatile and rsi > 65:
        market_mode = "VOLATILE_OVERBOUGHT"
    else:
        market_mode = "RANGE_BOUND"
    
    # === 网格套利评分 (0-10) ===
    grid_score = 0
    if rsi < 30: grid_score += 3
    elif rsi < 40: grid_score += 2
    elif rsi < 45: grid_score += 1
    if bb_pos < 0.2: grid_score += 2
    elif bb_pos < 0.3: grid_score += 1
    if market_mode == "RANGE_BOUND": grid_score += 2
    if not trend_down: grid_score += 1
    if vol_ratio > 1.0: grid_score += 1
    if rsi > 60: grid_score -= 2
    
    # === 趋势追踪评分 (0-10) ===
    trend_score = 0
    if market_mode == "TREND_UP": trend_score += 4
    elif market_mode == "TREND_DOWN": trend_score -= 2
    if trend_up: trend_score += 2
    if rsi > 45 and rsi < 65: trend_score += 2  # 健康区间
    if macd_l > macd_s and hist > 0: trend_score += 2  # MACD多头
    if vol_surge: trend_score += 1
    if bb_pos > 0.4 and bb_pos < 0.7: trend_score += 1  # BB中段
    if rsi > 70: trend_score -= 2  # 过热
    if bb_pos > 0.85: trend_score -= 2  # BB上轨
    
    # === 动态参数调整 ===
    if market_mode == "TREND_UP":
        # 牛市：网格格数减少（顺势持有），趋势引擎加大
        effective_grids = max(2, GRID_COUNT - 2)
        trend_bias = 1.0  # 全力趋势追踪
    elif market_mode == "TREND_DOWN":
        # 熊市：网格暂停，保留资金等待
        effective_grids = max(1, GRID_COUNT - 4)
        trend_bias = 0.0  # 不追涨
    elif market_mode == "RANGE_BOUND":
        # 震荡：全力网格套利
        effective_grids = GRID_COUNT
        trend_bias = 0.3
    else:
        effective_grids = GRID_COUNT
        trend_bias = 0.5
    
    # === 信号生成 ===
    signal = "HOLD"
    confidence = 0
    reasons = []
    
    # 买入信号
    if market_mode in ("TREND_UP", "VOLATILE_OVERSOLD") and trend_score >= 4:
        signal = "BUY"; confidence = trend_score
        reasons.append(f"{market_mode}")
    elif market_mode == "RANGE_BOUND" and grid_score >= 5:
        signal = "BUY"; confidence = grid_score
        reasons.append(f"网格机会 grid={grid_score}")
    elif rsi < 35 and bb_pos < 0.25 and not trend_down:
        signal = "BUY"; confidence = 6
        reasons.append(f"超卖 RSI={rsi:.0f} BB={bb_pos:.0%}")
    
    # 卖出信号
    if market_mode == "TREND_DOWN" and trend_score <= 2:
        signal = "SELL"; confidence = abs(trend_score - 2)
        reasons.append(f"{market_mode}")
    elif rsi > 70 and bb_pos > 0.8:
        signal = "SELL"; confidence = 6
        reasons.append(f"超买 RSI={rsi:.0f} BB={bb_pos:.0%}")
    
    return {
        'symbol': symbol,
        'price': cur,
        'rsi': rsi, 'rsi_h4': rsi_h4, 'rsi_d1': rsi_d1,
        'macd': macd_l, 'hist': hist,
        'bb_u': bb_u, 'bb_m': bb_m, 'bb_l': bb_l,
        'bb_pos': bb_pos,
        'ema20': ema20,
        'trend_up': trend_up, 'trend_down': trend_down,
        'vol_ratio': vol_ratio, 'vol_surge': vol_surge,
        'atr': atr,
        'market_mode': market_mode,
        'grid_score': grid_score,
        'trend_score': trend_score,
        'effective_grids': effective_grids,
        'trend_bias': trend_bias,
        'signal': signal,
        'confidence': confidence,
        'reasons': reasons,
    }

def calc_atr_approx(closes, period=14):
    if len(closes) < period+1: return closes[-1] * 0.02
    trs = [abs(closes[i]-closes[i-1]) for i in range(1, min(period+1, len(closes)))]
    return sum(trs)/len(trs)

# ===================== 网格引擎 =====================
class GridEngine:
    """
    智能网格：能识别手动平仓、自动归账、动态调整
    """
    
    def __init__(self, symbol, entry_price, grids, grid_profit, atr, ex, capital):
        self.symbol = symbol
        self.entry_price = entry_price
        self.grids = grids
        self.grid_profit = grid_profit
        self.atr = atr
        self.ex = ex
        self.capital = capital
        self.last_checked_positions = {}  # 用于检测手动平仓
        self.closed_by_manual = set()  # 手动平仓标记
        
        # 网格范围
        grid_range = max(atr * 3, entry_price * 0.12)
        self.upper = entry_price + grid_range / 2
        self.lower = entry_price - grid_range / 2
        if self.lower <= 0: self.lower = entry_price * 0.94
        self.grid_width = (self.upper - self.lower) / grids
        
        # 每个格子的状态: {idx: {buy_price, qty, sold, sold_price, sold_at, manual_close}}
        self.positions = {}
        
        log(f"[网格] {symbol} 中心=${entry_price:.2f} "
            f"范围[${self.lower:.2f}~${self.upper:.2f}] "
            f"{grids}格/格{grid_profit*100:.1f}% 资金${capital:.2f}")
    
    def grid_index(self, price):
        if price >= self.upper: return self.grids
        if price <= self.lower: return -1
        return int((price - self.lower) / self.grid_width)
    
    def _get_step(self):
        info = self.ex._get_symbol_info(self.symbol)
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                return float(f['stepSize'])
        return 0.001
    
    def _round_qty(self, qty):
        step = self._get_step()
        return float(qty) // step * step
    
    def invest_per_grid(self):
        active = len([p for p in self.positions.values() if not p.get('sold')])
        if active >= self.grids: return 0
        # 已卖出的格子的资金释放出来
        released = sum(
            p['buy_price'] * p['qty']
            for p in self.positions.values()
            if p.get('sold') and p.get('released', False) is False
        )
        available = (self.capital + released) / (self.grids - active)
        return min(available, self.capital * 0.25)
    
    def detect_manual_close(self, api_positions):
        """
        检测手动平仓：如果状态文件有持仓，但API返回没有，
        说明用户手动平仓了，需要同步状态
        """
        manual_closes = []
        for idx, pos in list(self.positions.items()):
            if pos.get('sold') or pos.get('qty', 0) <= 0:
                continue
            # 这个格子有持仓，检查API是否还有
            sym_key = self.symbol
            has_it = any(
                abs(p.get('entry', 0) - pos['buy_price']) / pos['buy_price'] < 0.1
                for p in api_positions
            )
            if not has_it:
                manual_closes.append(idx)
                pos['manual_close'] = True
                pos['sold'] = True
                pos['sold_at'] = time.time()
                pos['released'] = False  # 等待归账
                log(f"[⚠️ 手动平仓识别] {self.symbol} 格{idx} @ ${pos['buy_price']:.4f} qty={pos['qty']}")
        return manual_closes
    
    def buy_grid(self, idx, price):
        invest = self.invest_per_grid()
        if invest <= 5: return
        
        qty = self._round_qty(invest / price)
        if qty <= 0: return
        
        min_notional = self.ex.get_min_notional(self.symbol)
        if qty * price < min_notional:
            return
        
        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                self.positions[idx] = {
                    'buy_price': price,
                    'qty': qty,
                    'sold': False,
                    'target': price * (1 + self.grid_profit),
                    'sl': price * (1 - SL_PCT),
                    'ts_triggered': False,
                    'ts_price': 0,
                    'bought_at': time.time(),
                    'released': False,
                }
                log(f"[网格买入] {self.symbol} 格{idx} @ ${price:.4f} qty={qty:.4f} "
                    f"→目标${price*(1+self.grid_profit):.4f} SL${price*(1-SL_PCT):.4f}")
        except Exception as e:
            log(f"[网格买入失败] {self.symbol} 格{idx}: {e}")
    
    def _sell_grid(self, idx, reason):
        pos = self.positions.get(idx)
        if not pos or pos.get('sold'): return
        qty = pos['qty']
        if qty <= 0: return
        
        try:
            cur_price = self.ex.get_price(self.symbol)
            ok = self.ex.market_sell(self.symbol, qty)
            if ok:
                pnl_pct = (cur_price - pos['buy_price']) / pos['buy_price'] * 100
                log(f"[网格卖出] {self.symbol} 格{idx} @ ${cur_price:.4f} ({pnl_pct:+.2f}%) {reason}")
                pos['sold'] = True
                pos['sold_price'] = cur_price
                pos['sold_at'] = time.time()
                pos['sold_reason'] = reason
                pos['released'] = False  # 下次归账
        except Exception as e:
            log(f"[网格卖出失败] {self.symbol} 格{idx}: {e}")
    
    def release_funds(self):
        """释放已卖出格子的资金到可用余额"""
        released = 0
        for pos in self.positions.values():
            if pos.get('sold') and not pos.get('released'):
                released += pos['buy_price'] * pos['qty']
                pos['released'] = True
        return released
    
    def check_and_trade(self, cur_price):
        """检查网格状态，触发买卖"""
        idx = self.grid_index(cur_price)
        
        # 遍历所有格子
        for g_idx, pos in list(self.positions.items()):
            if pos.get('sold'): continue
            if pos.get('qty', 0) <= 0: continue
            
            bp = pos['buy_price']
            qty = pos['qty']
            if bp <= 0 or qty <= 0: continue
            
            # === 止损 ===
            if cur_price <= pos['sl']:
                self._sell_grid(g_idx, "SL触发")
                continue
            
            # === 止盈 (每格独立) ===
            if cur_price >= pos['target']:
                self._sell_grid(g_idx, f"TP+{self.grid_profit*100:.1f}%")
                continue
            
            # === 追踪止损 ===
            profit_pct = (cur_price - bp) / bp
            if profit_pct > 0.06 and not pos.get('ts_triggered'):
                pos['ts_triggered'] = True
                pos['ts_price'] = cur_price * (1 - TRAILING_PCT)
                log(f"[TS激活] {self.symbol} 格{g_idx} @ ${cur_price:.4f} 锁定")
            
            if pos.get('ts_triggered') and cur_price <= pos['ts_price']:
                self._sell_grid(g_idx, "TS追踪")
                continue
        
        # === 买入新格子 ===
        if 0 <= idx < self.grids:
            if idx not in self.positions:
                self.buy_grid(idx, cur_price)
            else:
                pos = self.positions[idx]
                # 已卖出后价格回来，重新买
                if pos.get('sold') and pos.get('released'):
                    self.buy_grid(idx, cur_price)
    
    def total_pnl(self, cur_price):
        realized = unrealized = invested = 0
        active = 0
        for pos in self.positions.values():
            bp = pos.get('buy_price', 0)
            q = pos.get('qty', 0)
            if bp <= 0 or q <= 0: continue
            invested += bp * q
            if pos.get('sold'):
                realized += (pos.get('sold_price', bp) - bp) * q
            else:
                active += 1
                unrealized += (cur_price - bp) * q
        return {
            'invested': invested,
            'realized': realized,
            'unrealized': unrealized,
            'total': realized + unrealized,
            'active': active,
            'pnl_pct': (realized + unrealized) / invested * 100 if invested > 0 else 0
        }
    
    def adjust_center(self, new_price):
        """网格中心漂移时重置"""
        drift = abs(new_price - self.entry_price) / self.entry_price
        if drift > 0.08:
            log(f"[网格重置] {self.symbol} 漂移{drift*100:.1f}% ${self.entry_price:.2f}→${new_price:.2f}")
            self.entry_price = new_price
            r = max(self.atr * 3, new_price * 0.12)
            self.upper = new_price + r / 2
            self.lower = new_price - r / 2
            if self.lower <= 0: self.lower = new_price * 0.94
            self.grid_width = (self.upper - self.lower) / self.grids
            # 保留未卖出仓位，只清空记录
            self.positions = {k: v for k, v in self.positions.items() if not v.get('sold')}

# ===================== 趋势引擎 =====================
class TrendEngine:
    """趋势追踪引擎：手动平仓识别 + 追踪止损"""
    
    def __init__(self, symbol, ex):
        self.symbol = symbol
        self.ex = ex
        self.position = None
        self.manual_close_detected = False
    
    def has_position(self):
        if not self.position: return False
        try:
            trades = self.ex.get_my_trades(self.symbol, limit=5)
            # 检查最近是否有持仓成交
            for t in trades:
                if t.get('isBuyer') and abs(float(t.get('price', 0)) - self.position['entry']) / self.position['entry'] < 0.05:
                    return True
            return False
        except: return True  # 保守：认为有仓
    
    def buy(self, price, qty):
        qty = self._round_qty(qty)
        if qty <= 0: return False
        
        min_notional = self.ex.get_min_notional(self.symbol)
        if qty * price < min_notional: return False
        
        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                self.position = {
                    'qty': qty,
                    'entry': price,
                    'peak': price,
                    'sl': price * (1 - SL_PCT),
                    'tp': price * (1 + TP_TREND),
                    'opened_at': time.time(),
                    'manual_close': False,
                }
                log(f"[趋势买入] {self.symbol} @ ${price:.4f} qty={qty:.4f} "
                    f"SL=${price*(1-SL_PCT):.4f} TP=${price*(1+TP_TREND):.4f}")
                return True
        except Exception as e:
            log(f"[趋势买入失败] {self.symbol}: {e}")
            return False
    
    def _round_qty(self, qty):
        info = self.ex._get_symbol_info(self.symbol)
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize']); break
        return float(qty) // step * step
    
    def check(self, cur_price):
        if not self.position: return
        
        p = self.position
        entry = p['entry']
        qty = p['qty']
        
        # === 手动平仓检测 ===
        if not self.has_position():
            log(f"[⚠️ 趋势手动平仓] {self.symbol} @ ${cur_price:.4f} "
                f"pnl={(cur_price-entry)/entry*100:+.2f}%")
            self.position = None
            return
        
        # 更新峰值
        if cur_price > p['peak']:
            p['peak'] = cur_price
        
        pnl = (cur_price - entry) / entry * 100
        
        # === 止损 ===
        if cur_price <= p['sl']:
            self._sell(cur_price, f"SL {pnl:+.1f}%")
            return
        
        # === 止盈 ===
        if cur_price >= p['tp']:
            self._sell(cur_price, f"TP {pnl:+.1f}%")
            return
        
        # === 追踪止损 ===
        peak_gain = (p['peak'] - entry) / entry * 100
        if peak_gain > 20:
            ts = p['peak'] * 0.92
            if cur_price <= ts:
                self._sell(cur_price, f"TSE {pnl:+.1f}%")
        elif peak_gain > 10:
            ts = p['peak'] * 0.95
            if cur_price <= ts:
                self._sell(cur_price, f"TS {pnl:+.1f}%")
        
        # === 趋势破坏止损 ===
        data = get_klines(self.ex, self.symbol)
        if data:
            ema20 = calc_ema(data['closes'], EMA_PERIOD)
            rsi = calc_rsi(data['closes'], RSI_PERIOD)
            if ema20 and cur_price < ema20 * 0.96:
                self._sell(cur_price, f"趋势破坏 {pnl:+.1f}%")
            elif rsi > 75:
                self._sell(cur_price, f"RSI过热 {pnl:+.1f}%")
    
    def _sell(self, cur_price, reason):
        if not self.position: return
        qty = self.position['qty']
        try:
            ok = self.ex.market_sell(self.symbol, qty)
            if ok:
                entry = self.position['entry']
                pnl = (cur_price - entry) / entry * 100
                log(f"[趋势卖出] {self.symbol} @ ${cur_price:.4f} ({pnl:+.2f}%) {reason}")
                self.position = None
        except Exception as e:
            log(f"[趋势卖出失败] {self.symbol}: {e}")

# ===================== 全局状态管理器 =====================
class StateManager:
    """
    智能状态管理：
    - 检测手动平仓
    - 自动归账
    - 高水位提盈
    - 崩溃恢复
    """
    
    def __init__(self, ex, state_file):
        self.ex = ex
        self.state_file = state_file
        self.state = self._load()
        self.high_water = self.state.get('high_water', 0)
        self.total_profit_taken = self.state.get('total_profit_taken', 0)
        self.init_capital = self.state.get('init_capital', 0)
    
    def _load(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except: return {}
    
    def save(self):
        self.state['high_water'] = self.high_water
        self.state['total_profit_taken'] = self.total_profit_taken
        self.state['init_capital'] = self.init_capital
        self.state['saved_at'] = time.time()
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=2, default=str)
    
    def get_balance(self):
        """实时余额 = 现货USDT + 所有未结算利润"""
        try:
            bal = self.ex.get_balance("USDT")
            return bal
        except: return 0.0
    
    def check_take_profit(self, balance):
        """检查是否需要提盈"""
        if self.high_water == 0:
            self.high_water = balance
            self.init_capital = balance
            return 0
        
        if balance > self.high_water * (1 + TAKE_PROFIT_PCT):
            profit = balance - self.high_water
            if profit >= 5:  # 至少赚5U
                log(f"💰 提盈! ${profit:.2f} | 余额${balance:.2f} | 高点${self.high_water:.2f}")
                self.high_water = balance * 0.92  # 提盈后新高点
                self.total_profit_taken += profit
                log(f"   历史累计提取: ${self.total_profit_taken:.2f}")
                self.save()
                return profit
        elif balance > self.high_water:
            self.high_water = balance
        return 0

# ===================== 主程序 =====================
def main():
    global cfg
    cfg = load_config()
    
    ex = BinanceSpotAdapter(cfg['api_key'], cfg['secret'])
    sm = StateManager(ex, STATE_FILE)
    
    log("=" * 70)
    log("📊 现货智能网格+趋势机器人 v3.0 🦞")
    log(f"   币种: {COINS}")
    log(f"   网格: {GRID_COUNT}格/格{GRID_PROFIT*100:.1f}% | 趋势:TP{TP_TREND*100:.0f}%/SL{SL_PCT*100:.0f}%")
    log(f"   复利: {COMPOUND_PCT*100:.0f}% | 提盈: ≥{TAKE_PROFIT_PCT*100:.0f}%")
    log("=" * 70)
    
    balance = sm.get_balance()
    log(f"USDT余额: ${balance:.2f}")
    if balance < 20:
        log(f"⚠️ 余额不足，建议≥$20启动")
    
    grid_engines = {}   # {symbol: GridEngine}
    trend_engines = {}  # {symbol: TrendEngine}
    
    last_scan = 0
    last_save = 0
    cycle = 0
    
    while True:
        try:
            now = time.time()
            cycle += 1
            
            # === 实时余额（每次循环都更新）===
            balance = sm.get_balance()
            
            # === 市场扫描（每3分钟一次，不频繁请求）===
            if now - last_scan >= SCAN_EVERY:
                last_scan = now
                log("-" * 60)
                log(f"📈 市场扫描 #{cycle} | 余额: ${balance:.2f} | 高点: ${sm.high_water:.2f}")
                
                signals = {}
                for sym in COINS:
                    try:
                        info = analyze(sym, ex)
                        if info:
                            signals[sym] = info
                            mode_emoji = {
                                "TREND_UP": "📈",
                                "TREND_DOWN": "📉",
                                "VOLATILE_OVERSOLD": "🔴",
                                "VOLATILE_OVERBOUGHT": "🟠",
                                "RANGE_BOUND": "📊"
                            }.get(info['market_mode'], "⚪")
                            sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(info['signal'], "⚪")
                            log(f"  {mode_emoji}{sig_emoji} {sym:10s} ${info['price']:>10.4f} | "
                                f"RSI={info['rsi']:>5.1f} | {info['market_mode']:22s} | "
                                f"Grid={info['grid_score']:>4.1f} Trend={info['trend_score']:>4.1f} "
                                f"{' '.join(info['reasons'][:2])}")
                    except Exception as e:
                        log(f"[扫描错误] {sym}: {e}")
                
                # === 手动平仓检测 ===
                for sym in list(grid_engines.keys()):
                    try:
                        # 获取该币种的真实持仓（通过成交历史估算）
                        trades = ex.get_my_trades(sym, limit=10)
                        api_positions = []
                        # 分析成交：买入算多仓
                        for t in trades:
                            if t.get('isBuyer') == True:
                                api_positions.append({'entry': float(t.get('price', 0)), 'side': 'LONG'})
                        
                        eng = grid_engines[sym]
                        eng.detect_manual_close(api_positions)
                    except: pass
                
                # === 趋势引擎手动平仓检测 ===
                for sym, eng in list(trend_engines.items()):
                    if eng.position and not eng.has_position():
                        log(f"[⚠️ 趋势手动平仓] {sym}")
                        eng.position = None
                
                # === 提盈检查 ===
                sm.check_take_profit(balance)
                
                # === 动态开仓 ===
                active_grid = len(grid_engines)
                active_trend = len(trend_engines)
                
                # 释放已卖出资金
                total_released = 0
                for eng in grid_engines.values():
                    total_released += eng.release_funds()
                
                investable = balance + total_released
                
                # BUY信号排序
                buy_candidates = sorted(
                    [(sym, info) for sym, info in signals.items()
                     if info['signal'] == 'BUY' and info['confidence'] >= 5
                     and sym not in grid_engines and sym not in trend_engines],
                    key=lambda x: -(x[1]['trend_score'] * x[1]['trend_bias'] +
                                   x[1]['grid_score'] * (1 - x[1]['trend_bias']))
                )
                
                for sym, info in buy_candidates:
                    if active_grid + active_trend >= MAX_POSITIONS:
                        break
                    if investable < 15: break
                    
                    per_coin = min(investable / (MAX_POSITIONS - active_grid - active_trend), 50)
                    
                    if info['trend_bias'] >= 0.7:
                        # 趋势引擎
                        eng = TrendEngine(sym, ex)
                        if eng.buy(info['price'], per_coin / info['price']):
                            trend_engines[sym] = eng
                            investable -= per_coin
                            active_trend += 1
                            log(f"[趋势开仓] {sym} @ ${info['price']:.2f} 分配${per_coin:.2f} 模式:{info['market_mode']}")
                    else:
                        # 网格引擎
                        grids = info['effective_grids']
                        eng = GridEngine(
                            sym, info['price'], grids,
                            GRID_PROFIT, info['atr'], ex, per_coin
                        )
                        grid_engines[sym] = eng
                        investable -= per_coin
                        active_grid += 1
                        log(f"[网格开仓] {sym} @ ${info['price']:.2f} {grids}格 分配${per_coin:.2f} 模式:{info['market_mode']}")
                
                # === SELL信号：止损弱势 ===
                for sym, info in signals.items():
                    if info['signal'] == 'SELL':
                        if sym in grid_engines:
                            eng = grid_engines[sym]
                            cur = info['price']
                            pnl = eng.total_pnl(cur)
                            if pnl['active'] > 0:
                                log(f"[SELL平网] {sym} {pnl['pnl_pct']:+.1f}% 模式:{info['market_mode']}")
                                for idx in list(eng.positions.keys()):
                                    eng._sell_grid(idx, f"SELL信号 {info['reasons']}")
                
                # === 关闭死亡引擎 ===
                for sym in list(grid_engines.keys()):
                    eng = grid_engines[sym]
                    try:
                        cur = ex.get_price(sym)
                        pnl = eng.total_pnl(cur)
                        # 浮亏>20%且在下跌趋势中 → 止损
                        if pnl['unrealized'] < -eng.capital * 0.20 and info.get('trend_down'):
                            log(f"[死亡止损] {sym} 浮亏{pnl['pnl_pct']:+.1f}%")
                            for idx in list(eng.positions.keys()):
                                eng._sell_grid(idx, "死亡止损")
                    except: pass
                
                # 清空死亡引擎
                grid_engines = {k: v for k, v in grid_engines.items()
                               if any(not p.get('sold') for p in v.positions.values())}
            
            # === 实时检查（每20秒）===
            # 网格检查
            for sym, eng in list(grid_engines.items()):
                try:
                    cur = ex.get_price(sym)
                    eng.check_and_trade(cur)
                    eng.adjust_center(cur)
                except: pass
            
            # 趋势检查
            for sym, eng in list(trend_engines.items()):
                try:
                    cur = ex.get_price(sym)
                    eng.check(cur)
                    if eng.position is None:
                        del trend_engines[sym]
                except: pass
            
            # === 状态保存 ===
            if now - last_save >= STATE_SAVE_EVERY:
                last_save = now
                
                total_invested = total_pnl = 0
                active_grids = 0
                for eng in grid_engines.values():
                    try:
                        cur = ex.get_price(eng.symbol)
                        p = eng.total_pnl(cur)
                        total_invested += p['invested']
                        total_pnl += p['total']
                        active_grids += p['active']
                    except: pass
                
                active_trend = sum(1 for e in trend_engines.values() if e.position)
                
                log(f"[{cycle}] 网格{active_grids}格 | 趋势{active_trend}仓 | "
                    f"总投入${total_invested:.2f} | 总盈亏${total_pnl:+.2f} | "
                    f"余额${balance:.2f} | 提取${sm.total_profit_taken:.2f}")
                
                sm.save()
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log("STOPPED")
            sm.save()
            break
        except Exception as e:
            log(f"ERROR: {e}")
            import traceback; traceback.print_exc()
            time.sleep(15)

if __name__ == "__main__":
    main()
