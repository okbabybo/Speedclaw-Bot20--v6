#!/usr/bin/env python3
"""币安现货智能网格+趋势机器人 v2.0
Dual引擎：熊市网格套利 + 牛市趋势追踪 + 复利滚仓
作者: 混沌龙虾 🦞
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

# 币种列表
COINS = cfg.get('coins', ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'AVAXUSDT'])

# === 核心参数 ===
INIT_CAPITAL    = cfg.get('init_capital', 100)   # 起始本金（用于计算复利基准）
MAX_POSITIONS   = cfg.get('max_positions', 3)    # 最多同时持有几个币
GRID_COUNT      = cfg.get('grid_count', 6)       # 网格格数
GRID_PROFIT     = cfg.get('grid_profit', 0.006)  # 每格利润目标 0.6%
COMPOUND_PCT    = cfg.get('compound_pct', 1.0)   # 利润复利比例 100%
TAKE_PROFIT_PCT = cfg.get('take_profit_pct', 0.20) # 盈利20%时提取利润
SL_PCT          = cfg.get('sl_pct', 0.12)        # 止损12%
TS_PCT          = cfg.get('ts_pct', 0.08)        # 追踪止损8%

# === 趋势参数 ===
RSI_PERIOD      = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_MULT = 20, 2.0
EMA_TREND_PERIOD = 20

# === 模式判断 ===
RSI_NEUTRAL_LOW  = 40
RSI_NEUTRAL_HIGH = 60
VOL_THRESHOLD    = 1.3  # 成交量放大倍数

# === 运行参数 ===
CHECK_INTERVAL   = 30   # 检查间隔（秒）
REBALANCE_EVERY = 300  # 每5分钟检查一次强弱
STATE_SAVE_EVERY = 120 # 每2分钟保存状态
CANDLE_INTERVAL = "1h" # 主K线周期

# ===================== 工具函数 =====================
def log(msg):
    ts = datetime.now().strftime('%m/%d %H:%M:%S')
    print(f"[{ts}] {msg}")
    with open(LOG_FILE, "a") as f:
        f.write(f"[{ts}] {msg}\n")

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
    if len(prices) < slow+1: return 0, 0, 0
    ef = calc_ema(prices, fast); es = calc_ema(prices, slow)
    if ef is None or es is None: return 0, 0, 0
    macd = ef - es
    hist = macd * 0.3  # 简化signal
    return macd, 0, hist

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

def get_candle_data(ex, symbol, interval=CANDLE_INTERVAL, limit=200):
    """获取K线数据"""
    klines = ex.get_klines(symbol, interval, limit)
    if len(klines) < BB_PERIOD + 5:
        return None
    closes = [float(k[4]) for k in klines]
    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    vols   = [float(k[5]) for k in klines]
    return {'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols}

# ===================== 信号分析 =====================
def analyze_coin(ex, symbol):
    """全面分析一个币种，返回市场状态和信号"""
    data = get_candle_data(ex, symbol)
    if data is None: return None
    
    c = data['closes']
    cur = c[-1]
    
    # === 基础指标 ===
    rsi   = calc_rsi(c, RSI_PERIOD)
    macd, sig, hist = calc_macd(c, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    bb_u, bb_m, bb_l = calc_bollinger(c, BB_PERIOD, BB_MULT)
    ema20 = calc_ema(c, EMA_TREND_PERIOD)
    ema20_prev = calc_ema(c[:-4], EMA_TREND_PERIOD) if len(c) >= 24 else None
    
    atr = calc_atr(ex.get_klines(symbol, CANDLE_INTERVAL, 50))
    
    # === 趋势判断 ===
    trend_up = bool(ema20 and ema20_prev and ema20 > ema20_prev)
    trend_down = bool(ema20 and ema20_prev and ema20 < ema20_prev)
    
    # === 成交量 ===
    vol_avg = sum(data['vols'][-20:]) / 20 if len(data['vols']) >= 20 else data['vols'][-1]
    vol_ratio = data['vols'][-1] / vol_avg if vol_avg > 0 else 1
    vol_surge = vol_ratio > VOL_THRESHOLD
    
    # === BB位置 ===
    bb_range = bb_u - bb_l if bb_u and bb_l else 1
    bb_pos = (cur - bb_l) / bb_range if bb_l else 0.5
    
    # === 市场宽度动量 ===
    # 多 timeframe 确认
    h4_data = get_candle_data(ex, symbol, "4h", 100)
    rsi_h4 = calc_rsi(h4_data['closes'], RSI_PERIOD) if h4_data else 50
    
    # === 模式判断 ===
    mode = "NEUTRAL"  # 默认中性震荡
    
    if rsi < RSI_NEUTRAL_LOW and not trend_down:
        mode = "OVERSOLD_ACCUMULATE"  # 超卖积累
    elif rsi > RSI_NEUTRAL_HIGH and not trend_up:
        mode = "OVERBOUGHT_DISTRIBUTE"  # 超买派发
    elif trend_up and (rsi > 50 or rsi_h4 > 55):
        mode = "TREND_LONG"  # 上升趋势
    elif trend_down and (rsi < 50 or rsi_h4 < 45):
        mode = "TREND_SHORT"  # 下降趋势
    
    # === 网格套利评分 (0-10) ===
    grid_score = 0
    if rsi < 35: grid_score += 3  # 深度超卖
    elif rsi < 45: grid_score += 2
    if bb_pos < 0.2: grid_score += 2  # BB下轨
    elif bb_pos < 0.3: grid_score += 1
    if not trend_down: grid_score += 1  # 下跌趋势不强
    if rsi > 55: grid_score -= 1  # 不适合网格
    if vol_surge: grid_score += 1
    
    # === 趋势追踪评分 (0-10) ===
    trend_score = 0
    if trend_up: trend_score += 2
    if rsi > 50 and rsi < 70: trend_score += 2  # 健康多头
    if bb_pos > 0.4 and bb_pos < 0.7: trend_score += 1  # BB中轨健康
    if vol_surge: trend_score += 2
    if macd > 0 and hist > 0: trend_score += 2  # MACD多头
    if rsi > 65: trend_score -= 1  # 过热
    if bb_pos > 0.85: trend_score -= 2  # BB上轨过热
    
    # === 买入/卖出信号 ===
    signal = None
    confidence = 0
    
    # 买入信号
    if mode in ("OVERSOLD_ACCUMULATE", "TREND_LONG"):
        buy_conf = 0
        reasons = []
        if rsi < 40: buy_conf += 3; reasons.append(f"RSI={rsi:.0f}<40")
        elif rsi < 50: buy_conf += 2; reasons.append(f"RSI={rsi:.0f}<50")
        if bb_pos < 0.25: buy_conf += 2; reasons.append(f"BB={bb_pos:.0%}")
        if trend_up: buy_conf += 2; reasons.append("EMA20↑")
        if mode == "OVERSOLD_ACCUMULATE" and vol_surge: buy_conf += 2; reasons.append(f"V={vol_ratio:.1f}x")
        if macd > 0: buy_conf += 1; reasons.append("MACD多头")
        if buy_conf >= 5:
            signal = "BUY"
            confidence = buy_conf
    
    # 卖出/做空信号
    elif mode in ("OVERBOUGHT_DISTRIBUTE", "TREND_SHORT"):
        sell_conf = 0
        reasons = []
        if rsi > 60: sell_conf += 3; reasons.append(f"RSI={rsi:.0f}>60")
        elif rsi > 55: sell_conf += 2; reasons.append(f"RSI={rsi:.0f}>55")
        if bb_pos > 0.75: sell_conf += 2; reasons.append(f"BB={bb_pos:.0%}")
        if trend_down: sell_conf += 2; reasons.append("EMA20↓")
        if sell_conf >= 5:
            signal = "SELL"
            confidence = sell_conf
    
    return {
        'symbol': symbol,
        'price': cur,
        'rsi': rsi,
        'rsi_h4': rsi_h4,
        'macd': macd,
        'hist': hist,
        'bb_u': bb_u, 'bb_m': bb_m, 'bb_l': bb_l,
        'bb_pos': bb_pos,
        'ema20': ema20,
        'trend_up': trend_up,
        'trend_down': trend_down,
        'vol_ratio': vol_ratio,
        'vol_surge': vol_surge,
        'atr': atr,
        'mode': mode,
        'grid_score': grid_score,
        'trend_score': trend_score,
        'signal': signal,
        'confidence': confidence,
        'reasons': locals().get('reasons', [])
    }

# ===================== 网格引擎 =====================
class GridEngine:
    """
    现货网格：价格区间划分，低买高卖
    特点：
    - 以入场价为中心，上下划分N格
    - 每格独立止损（最多跌12%全出）
    - 每格达到目标利润自动卖出
    - 复利：卖出的资金自动分配到下一格
    """
    
    def __init__(self, symbol, entry_price, grids, grid_profit, atr, ex, capital):
        self.symbol = symbol
        self.entry_price = entry_price  # 网格中心价
        self.grids = grids               # 网格数量
        self.grid_profit = grid_profit   # 每格目标利润
        self.atr = atr
        self.ex = ex
        self.capital = capital           # 当前分配到的资金
        
        # 网格宽度（基于ATR动态）
        grid_range = max(atr * 2, entry_price * 0.10)  # 至少±5%范围
        self.upper = entry_price + grid_range
        self.lower = entry_price - grid_range
        if self.lower <= 0: self.lower = entry_price * 0.95
        
        self.grid_width = (self.upper - self.lower) / grids
        
        # 网格状态: {idx: {buy_price, qty, sold, sold_price, profit_locked}}
        self.positions = {}  # 已买入的格子
        self.grid_states = {}  # 每个格子的状态
        
        log(f"[网格启动] {symbol} 中心=${entry_price:.2f} "
            f"范围[${self.lower:.2f}~${self.upper:.2f}] "
            f"{grids}格 每格{grid_profit*100:.1f}% 资金${capital:.2f}")
    
    def grid_index(self, price):
        if price >= self.upper: return self.grids
        if price <= self.lower: return -1
        return int((price - self.lower) / self.grid_width)
    
    def invest_per_grid(self):
        """每格分配资金"""
        active = len([p for p in self.positions.values() if not p.get('sold')])
        if active >= self.grids:
            return 0
        available = self.capital / (self.grids - active)
        return min(available, self.capital * 0.2)  # 不超过总资金的20%单格
    
    def buy_grid(self, idx, price):
        invest = self.invest_per_grid()
        if invest <= 0: return
        
        info = self.ex._get_symbol_info(self.symbol)
        step = 0.001
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize']); break
        qty = float(invest / price) // step * step
        
        min_notional = self.ex.get_min_notional(self.symbol)
        if qty * price < min_notional:
            log(f"[跳过] {self.symbol} 格{idx} ${qty*price:.1f}<${min_notional}")
            return
        
        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                target_price = price * (1 + self.grid_profit)  # 止盈价
                sl_price = price * (1 - SL_PCT)                 # 止损价
                self.positions[idx] = {
                    'buy_price': price,
                    'qty': qty,
                    'sold': False,
                    'target': target_price,
                    'sl': sl_price,
                    'profit_locked': False,
                    'bought_at': time.time()
                }
                log(f"[网格买入] {self.symbol} 格{idx} @ ${price:.4f} "
                    f"qty={qty:.4f} 目标${target_price:.4f} SL${sl_price:.4f}")
        except Exception as e:
            log(f"[网格买入失败] {self.symbol}: {e}")
    
    def check_grids(self, cur_price):
        """检查所有格子，触发买卖"""
        idx = self.grid_index(cur_price)
        
        # 遍历所有持仓格子
        for g_idx, pos in list(self.positions.items()):
            if pos.get('sold'): continue
            buy_price = pos['buy_price']
            qty = pos['qty']
            
            if qty <= 0 or buy_price <= 0: continue
            
            # === 止损检查 ===
            if cur_price <= pos['sl']:
                self._sell_grid(g_idx, cur_price, "SL触发")
                continue
            
            # === 止盈检查 ===
            if cur_price >= pos['target']:
                self._sell_grid(g_idx, cur_price, f"TP+{self.grid_profit*100:.1f}%")
                continue
            
            # === 追踪止损（锁定利润）===
            profit_pct = (cur_price - buy_price) / buy_price
            if profit_pct > 0.08 and not pos.get('ts_activated'):
                # 利润>8%，激活追踪止损
                pos['ts_activated'] = True
                pos['ts_price'] = cur_price * 0.97  # 从最高点回撤3%出
            
            if pos.get('ts_activated') and cur_price <= pos['ts_price']:
                self._sell_grid(g_idx, cur_price, f"TS追踪止损")
                continue
        
        # === 买入新格子 ===
        if 0 <= idx < self.grids:
            if idx not in self.positions:
                self.buy_grid(idx, cur_price)
    
    def _sell_grid(self, idx, cur_price, reason):
        pos = self.positions[idx]
        if pos.get('sold'): return
        qty = pos['qty']
        if qty <= 0: return
        
        try:
            ok = self.ex.market_sell(self.symbol, qty)
            if ok:
                buy_price = pos['buy_price']
                pnl = (cur_price - buy_price) / buy_price * 100
                log(f"[网格卖出] {self.symbol} 格{idx} @ ${cur_price:.4f} ({pnl:+.2f}%) {reason}")
                pos['sold'] = True
                pos['sold_price'] = cur_price
                pos['pnl_pct'] = pnl
        except Exception as e:
            log(f"[网格卖出失败] {self.symbol}: {e}")
    
    def total_pnl(self, cur_price):
        realized = 0; unrealized = 0; invested = 0; active = 0
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
            'active_grids': active,
            'total_pnl_pct': (realized + unrealized) / invested * 100 if invested > 0 else 0
        }
    
    def adjust_grid_center(self, new_price):
        """根据新趋势调整网格中心"""
        if abs(new_price - self.entry_price) / self.entry_price > 0.05:
            log(f"[网格重置] {self.symbol} 中心从${self.entry_price:.2f}→${new_price:.2f}")
            self.entry_price = new_price
            range_half = max(self.atr * 2, new_price * 0.10)
            self.upper = new_price + range_half
            self.lower = new_price - range_half
            if self.lower <= 0: self.lower = new_price * 0.95
            self.grid_width = (self.upper - self.lower) / self.grids
            # 清空已触发的格子，重新开始
            self.positions = {}

# ===================== 趋势引擎 =====================
class TrendEngine:
    """
    趋势追踪引擎：
    - 抓住大趋势（EMA确认）
    - RSI健康时持有（40-70）
    - BB上轨过热时止盈
    - 追踪止损保护利润
    """
    
    def __init__(self, symbol, ex):
        self.symbol = symbol
        self.ex = ex
        self.position = None  # {'qty', 'entry', 'entry_time', 'peak_price', 'sl'}
    
    def buy(self, price, qty):
        info = self.ex._get_symbol_info(self.symbol)
        step = 0.001
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize']); break
        qty = float(qty) // step * step
        
        min_notional = self.ex.get_min_notional(self.symbol)
        if qty * price < min_notional: return False
        
        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                self.position = {
                    'qty': qty,
                    'entry': price,
                    'entry_time': time.time(),
                    'peak_price': price,
                    'sl': price * (1 - SL_PCT),
                    'tp': price * (1 + 0.25),  # 目标+25%
                }
                log(f"[趋势买入] {self.symbol} @ ${price:.4f} qty={qty:.4f} SL=${self.position['sl']:.4f}")
                return True
        except Exception as e:
            log(f"[趋势买入失败] {self.symbol}: {e}")
            return False
    
    def check(self, cur_price):
        if not self.position: return
        p = self.position
        entry = p['entry']
        qty = p['qty']
        
        # 更新峰值
        if cur_price > p['peak_price']:
            p['peak_price'] = cur_price
        
        pnl = (cur_price - entry) / entry * 100
        
        # === 止损 ===
        if cur_price <= p['sl']:
            self._sell(cur_price, f"SL {pnl:+.1f}%")
            return
        
        # === 止盈：目标25% 或 BB上轨 ===
        data = get_candle_data(self.ex, self.symbol)
        if data:
            bb_u = data['closes'][-1]  # 简化
            if cur_price >= p['tp']:
                self._sell(cur_price, f"TP目标 {pnl:+.1f}%")
                return
        
        # === 追踪止损 ===
        # 利润>15%后，锁定50%；>25%后，锁定75%
        peak_gain = (p['peak_price'] - entry) / entry * 100
        if peak_gain > 25:
            ts_trigger = p['peak_price'] * 0.92  # 回撤8%出
            if cur_price <= ts_trigger:
                self._sell(cur_price, f"TSE回撤 {pnl:+.1f}%")
        elif peak_gain > 15:
            ts_trigger = p['peak_price'] * 0.95  # 回撤5%出
            if cur_price <= ts_trigger:
                self._sell(cur_price, f"TS15 {pnl:+.1f}%")
        
        # === 趋势破坏止损 ===
        rsi = calc_rsi(data['closes'], RSI_PERIOD) if data else 50
        ema20 = calc_ema(data['closes'], EMA_TREND_PERIOD) if data else None
        if ema20 and cur_price < ema20 * 0.96:
            self._sell(cur_price, f"趋势破坏 {pnl:+.1f}%")
    
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

# ===================== 主程序 =====================
def main():
    global cfg
    cfg = load_config()
    
    ex = BinanceSpotAdapter(cfg['api_key'], cfg['secret'])
    
    log("=" * 70)
    log("📊 现货智能网格+趋势机器人 v2.0 启动")
    log(f"   币种: {COINS} | 最多持有:{MAX_POSITIONS}个")
    log(f"   网格:{GRID_COUNT}格/{GRID_PROFIT*100:.1f}% | 趋势TP:25% SL:12%")
    log(f"   复利:利润{COMPOUND_PCT*100:.0f}%再投资 | 提盈门槛:{TAKE_PROFIT_PCT*100:.0f}%")
    log("=" * 70)
    
    # 获取余额
    try:
        balance = ex.get_balance("USDT")
        log(f"USDT余额: ${balance:.2f}")
    except Exception as e:
        log(f"余额获取失败: {e}")
        balance = 0
    
    # 初始化引擎
    grid_engines = {}   # 网格引擎
    trend_engines = {}  # 趋势引擎
    
    # 复利记录
    high_water = balance  # 历史最高
    last_rebalance = time.time()
    last_state_save = time.time()
    cycle_count = 0
    
    # 提取利润记录
    total_profit_taken = 0.0
    
    while True:
        try:
            now = time.time()
            
            # === 定期重新平衡各币种 ===
            if now - last_rebalance >= REBALANCE_EVERY:
                last_rebalance = now
                log("-" * 60)
                log(f"📈 市场扫描 | 余额: ${balance:.2f} | 历史最高: ${high_water:.2f}")
                
                # 获取所有币信号
                signals = {}
                for sym in COINS:
                    try:
                        info = analyze_coin(ex, sym)
                        if info:
                            signals[sym] = info
                            emoji = "🟢" if info['signal'] == "BUY" else ("🔴" if info['signal'] == "SELL" else "⚪")
                            log(f"  {emoji} {sym:12s} ${info['price']:>10.4f} | "
                                f"RSI={info['rsi']:>5.1f} | "
                                f"Mode={info['mode']:20s} | "
                                f"Grid={info['grid_score']:>4.1f} Trend={info['trend_score']:>4.1f} | "
                                f"{' '.join(info['reasons'][:2])}")
                    except Exception as e:
                        log(f"[扫描错误] {sym}: {e}")
                
                # === 资金再分配 ===
                # 计算当前网格引擎总资金
                grid_capital_total = 0
                for eng in grid_engines.values():
                    pnl_info = eng.total_pnl(ex.get_price(eng.symbol))
                    grid_capital_total += pnl_info['invested']
                
                # 可用于新投资的资金 = 余额 - 已分配的
                investable = balance - grid_capital_total
                
                # 分配资金：优先给强势币开新网格
                by_trend = sorted(
                    [(sym, info) for sym, info in signals.items() 
                     if info['signal'] == 'BUY' and sym not in grid_engines],
                    key=lambda x: -x[1]['trend_score']
                )
                
                by_grid = sorted(
                    [(sym, info) for sym, info in signals.items() 
                     if info['signal'] == 'BUY' and info['grid_score'] > 4 and sym not in grid_engines],
                    key=lambda x: -x[1]['grid_score']
                )
                
                # 优先开趋势引擎（潜力更大）
                for sym, info in by_trend[:1]:
                    if investable >= 30:
                        per_coin = min(investable * 0.3, 100)
                        eng = TrendEngine(sym, ex)
                        price = info['price']
                        qty = per_coin / price
                        if eng.buy(price, qty):
                            trend_engines[sym] = eng
                            investable -= per_coin
                            log(f"[趋势开仓] {sym} @ ${price:.2f} 分配${per_coin:.2f}")
                
                # 剩余资金开网格
                for sym, info in by_grid[:2]:
                    if investable >= 20 and len(grid_engines) < MAX_POSITIONS:
                        per_coin = min(investable / (MAX_POSITIONS - len(grid_engines)), 50)
                        eng = GridEngine(
                            sym, info['price'],
                            GRID_COUNT, GRID_PROFIT,
                            info['atr'], ex, per_coin
                        )
                        grid_engines[sym] = eng
                        investable -= per_coin
                
                # === 提盈检查 ===
                if balance > high_water * (1 + TAKE_PROFIT_PCT):
                    profit = balance - high_water
                    if profit >= 5:  # 至少赚5U才提取
                        log(f"💰 提盈! ${profit:.2f} (余额${balance:.2f} > 高点${high_water:.2f})")
                        # 提盈：不重新投入，只保留
                        # 高水位更新为当前余额的90%
                        high_water = balance * 0.95
                        total_profit_taken += profit
                        log(f"   已提取总利润: ${total_profit_taken:.2f}")
                
                # === 弱势币减仓 ===
                for sym, info in signals.items():
                    if info['signal'] == 'SELL' and sym in grid_engines:
                        eng = grid_engines[sym]
                        cur = info['price']
                        pnl_info = eng.total_pnl(cur)
                        if pnl_info['active_grids'] > 0:
                            log(f"🔴 {sym} SELL信号触发，卖出所有活跃格子 pnl={pnl_info['total_pnl_pct']:+.1f}%")
                            for pos in list(eng.positions.values()):
                                if not pos.get('sold'):
                                    eng._sell_grid(list(eng.positions.keys())[list(eng.positions.values()).index(pos)], cur)
            
            # === 检查网格引擎 ===
            for sym, eng in list(grid_engines.items()):
                try:
                    cur_price = ex.get_price(sym)
                    eng.check_grids(cur_price)
                    
                    # 动态调整网格中心
                    data = get_candle_data(ex, sym)
                    if data:
                        rsi = calc_rsi(data['closes'])
                        # 如果趋势变强，重置网格
                        if rsi > 60 and eng.entry_price < cur_price * 0.95:
                            eng.adjust_grid_center(cur_price)
                        elif rsi < 40 and eng.entry_price > cur_price * 1.05:
                            eng.adjust_grid_center(cur_price)
                except: pass
            
            # === 检查趋势引擎 ===
            for sym, eng in list(trend_engines.items()):
                try:
                    cur_price = ex.get_price(sym)
                    eng.check(cur_price)
                    if eng.position is None:
                        del trend_engines[sym]
                except: pass
            
            # === 状态保存 ===
            if now - last_state_save >= STATE_SAVE_EVERY:
                last_state_save = now
                cycle_count += 1
                
                # 统计
                total_invested = 0
                total_pnl = 0
                active = 0
                for eng in grid_engines.values():
                    try:
                        cur = ex.get_price(eng.symbol)
                        p = eng.total_pnl(cur)
                        total_invested += p['invested']
                        total_pnl += p['total']
                        active += p['active_grids']
                    except: pass
                
                log(f"[周期{cycle_count}] "
                    f"网格{active}格活跃 | "
                    f"总投入${total_invested:.2f} | "
                    f"总盈亏${total_pnl:+.2f} | "
                    f"余额${balance:.2f} | "
                    f"提取${total_profit_taken:.2f}")
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            log("STOPPED")
            # 保存最终状态
            state = {
                'grid_engines': {},
                'trend_engines': {},
                'balance': balance,
                'high_water': high_water,
                'total_profit_taken': total_profit_taken,
                'saved_at': time.time()
            }
            for sym, eng in grid_engines.items():
                try:
                    cur = ex.get_price(sym)
                    state['grid_engines'][sym] = {
                        'entry_price': eng.entry_price,
                        'positions': eng.positions,
                        'pnl': eng.total_pnl(cur)
                    }
                except: pass
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2, default=str)
            log("[状态已保存]")
            break
        except Exception as e:
            log(f"ERROR: {e}")
            import traceback; traceback.print_exc()
            time.sleep(15)

if __name__ == "__main__":
    main()
