#!/usr/bin/env python3
"""币安现货智能网格+趋势机器人 v3.0 Final
混沌龙虾 🦞 — 完整规格实现版（见 SPOT_STRATEGY_SPEC.md）
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

# === 核心参数（与SPEC.md一致）===
GRID_COUNT      = cfg.get('grid_count', 6)
GRID_PROFIT     = 0.006    # 每格0.6%
GRID_VOL_PROFIT = 0.010    # 高波动每格1%
COMPOUND_PCT    = 1.0      # 100%复利
TAKE_PROFIT_PCT = 0.20    # 盈利20%提盈
SL_PCT          = 0.12     # 止损12%
TS_PCT          = 0.03     # 追踪回撤3%
TP_TREND1       = 0.15     # 趋势第一目标+15%
TP_TREND2       = 0.25     # 趋势第二目标+25%
TS_TREND_PCT    = 0.05     # 趋势追踪回撤5%
MAX_EXPOSURE    = 1.50     # 最大仓位150%

# 风险参数
DRAWDOWN_PROTECT  = 0.20   # 回撤20%全部止损
MAX_DAILY_LOSS     = 0.08   # 单日最大亏损8%
CRASH_LIMIT        = 3      # 熔断：连续3亏暂停
CRASH_PAUSE        = 900    # 熔断暂停15分钟
MANUAL_COOLDOWN    = 300    # 手动平仓识别冷却5分钟

# 资金分级
TIER1 = 20    # 余额$20-50：每币$20，最多1个币
TIER2 = 50    # 余额$50-200：每币$50
TIER3 = 150   # 余额$200-1000：每币$150
TIER4 = 300   # 余额>$1000：每币$300

# 运行参数
CHECK_INTERVAL    = 20    # 实时检查（秒）
SCAN_INTERVAL     = 180   # 市场扫描（秒）
SAVE_INTERVAL     = 60    # 状态保存（秒）

# 指标参数
RSI_PERIOD     = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BB_PERIOD, BB_MULT = 20, 2.0
EMA_PERIOD    = 20
ATR_PERIOD    = 14

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
    if len(prices) < slow+signal: return 0, 0, 0
    macd_vals = []
    for i in range(slow, len(prices)+1):
        ef = calc_ema(prices[:i], fast)
        es = calc_ema(prices[:i], slow)
        if ef is not None and es is not None:
            macd_vals.append(ef - es)
    if len(macd_vals) < signal: return 0, 0, 0
    macd_line = macd_vals[-1]
    signal_line = calc_ema(macd_vals, signal)
    if signal_line is None: signal_line = macd_vals[-1]
    return macd_line, signal_line, macd_line - signal_line

def calc_bollinger(prices, period=20, mult=2.0):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:])/period
    std = (sum((p-sma)**2 for p in prices[-period:])/period)**0.5
    return sma + mult*std, sma, sma - mult*std

def calc_atr(klines):
    if len(klines) < ATR_PERIOD+1: return 0
    trs = []
    for i in range(1, len(klines)):
        h, l = float(klines[i][2]), float(klines[i][3])
        pc = float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-ATR_PERIOD:])/ATR_PERIOD

def get_klines(ex, symbol, interval="1h", limit=200):
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

# ===================== 市场模式识别 =====================
def detect_market_mode(symbol, ex):
    """
    识别6种市场模式（见SPEC.md第二章）
    返回: {mode, rsi, ema20, atr, bb_pos, vol_ratio, trend_score, grid_score}
    """
    d1  = get_klines(ex, symbol, "1d",  30)
    h4  = get_klines(ex, symbol, "4h", 100)
    h1  = get_klines(ex, symbol, "1h", 200)
    if not all([d1, h4, h1]): return None

    c1, ch4, cd1 = h1['closes'], h4['closes'], d1['closes']
    cur = c1[-1]

    # === 多周期RSI ===
    rsi_1h = calc_rsi(c1,  RSI_PERIOD)
    rsi_h4 = calc_rsi(ch4, RSI_PERIOD)
    rsi_d1 = calc_rsi(cd1, RSI_PERIOD)

    # === EMA趋势 ===
    ema20_1h  = calc_ema(c1,  EMA_PERIOD)
    ema20_h4  = calc_ema(ch4, EMA_PERIOD)
    ema20_d1  = calc_ema(cd1, EMA_PERIOD)

    ema20_1h_prev  = calc_ema(c1[:-4],  EMA_PERIOD)  if len(c1)  >= 24 else None
    ema20_h4_prev  = calc_ema(ch4[:-4], EMA_PERIOD)  if len(ch4) >= 24 else None
    ema20_d1_prev  = calc_ema(cd1[:-5], EMA_PERIOD)  if len(cd1) >= 25 else None

    trend_up_1h  = bool(ema20_1h  and ema20_1h_prev  and ema20_1h  > ema20_1h_prev)
    trend_up_h4  = bool(ema20_h4  and ema20_h4_prev  and ema20_h4  > ema20_h4_prev)
    trend_up_d1  = bool(ema20_d1  and ema20_d1_prev  and ema20_d1  > ema20_d1_prev)
    trend_down_1h = bool(ema20_1h and ema20_1h_prev and ema20_1h  < ema20_1h_prev)
    trend_down_h4 = bool(ema20_h4 and ema20_h4_prev and ema20_h4  < ema20_h4_prev)

    # === 布林带 ===
    bb_u, bb_m, bb_l = calc_bollinger(c1, BB_PERIOD, BB_MULT)
    bb_range = bb_u - bb_l if bb_u and bb_l else 1
    bb_pos = (cur - bb_l) / bb_range

    # === 成交量 ===
    vol_avg  = sum(h1['vols'][-20:]) / 20 if len(h1['vols']) >= 20 else h1['vols'][-1]
    vol_ratio = h1['vols'][-1] / vol_avg if vol_avg > 0 else 1
    vol_surge = vol_ratio > 1.3

    # === 波动率 ===
    price_range = (max(c1[-50:]) - min(c1[-50:])) / cur if cur > 0 else 0
    is_volatile = price_range > 0.08

    # === ATR ===
    klines_raw = ex.get_klines(symbol, "1h", ATR_PERIOD+5)
    atr = calc_atr(klines_raw) if klines_raw else 0

    # === 模式判断（优先级从高到低）===
    mode = "RANGE_BOUND"  # 默认

    # 1. 极端行情（最高优先级）
    if rsi_d1 > 80 or rsi_d1 < 20:
        mode = "CRISIS"
    # 2. 上升趋势
    elif trend_up_d1 and trend_up_h4 and trend_up_1h:
        mode = "TREND_UP"
    # 3. 下跌趋势
    elif trend_down_h4 and trend_down_1h and rsi_1h < 50:
        mode = "TREND_DOWN"
    # 4. 高波动超卖
    elif is_volatile and rsi_1h < 35 and vol_surge:
        mode = "VOLATILE_OVERSOLD"
    # 5. 高波动超买
    elif is_volatile and rsi_1h > 65 and vol_surge:
        mode = "VOLATILE_OVERBOUGHT"
    # 6. 震荡
    else:
        mode = "RANGE_BOUND"

    # === 模式系数（用于资金分配）===
    mode_params = {
        "TREND_UP":            {"pos_pct": 1.0,  "grids": 2, "grid_profit": GRID_PROFIT,     "trend_bias": 1.0},
        "TREND_DOWN":          {"pos_pct": 0.3,  "grids": 2, "grid_profit": GRID_PROFIT,     "trend_bias": 0.0},
        "RANGE_BOUND":         {"pos_pct": 0.8,  "grids": 6, "grid_profit": GRID_PROFIT,     "trend_bias": 0.3},
        "VOLATILE_OVERSOLD":   {"pos_pct": 1.2,  "grids": 4, "grid_profit": GRID_VOL_PROFIT, "trend_bias": 0.7},
        "VOLATILE_OVERBOUGHT": {"pos_pct": 0.0,  "grids": 0, "grid_profit": GRID_PROFIT,     "trend_bias": 0.0},
        "CRISIS":              {"pos_pct": 0.0,  "grids": 0, "grid_profit": GRID_PROFIT,     "trend_bias": 0.0},
    }[mode]

    # === 评分 ===
    trend_score = 0
    if mode == "TREND_UP":            trend_score = 8
    elif mode == "TREND_DOWN":        trend_score = 1
    elif mode == "VOLATILE_OVERSOLD": trend_score = 6
    elif mode == "RANGE_BOUND":       trend_score = 4

    grid_score = 0
    if rsi_1h < 35:                  grid_score += 3
    elif rsi_1h < 40:                grid_score += 2
    elif rsi_1h < 45:                grid_score += 1
    if bb_pos < 0.2:                 grid_score += 2
    elif bb_pos < 0.3:               grid_score += 1
    if mode == "RANGE_BOUND":        grid_score += 2
    if not trend_down_1h:             grid_score += 1

    return {
        'symbol': symbol, 'price': cur,
        'rsi_1h': rsi_1h, 'rsi_h4': rsi_h4, 'rsi_d1': rsi_d1,
        'ema20': ema20_1h,
        'bb_u': bb_u, 'bb_m': bb_m, 'bb_l': bb_l, 'bb_pos': bb_pos,
        'vol_ratio': vol_ratio, 'vol_surge': vol_surge,
        'atr': atr,
        'trend_up': trend_up_1h, 'trend_down': trend_down_1h,
        'mode': mode,
        'trend_score': trend_score,
        'grid_score': grid_score,
        **mode_params,
    }

# ===================== 资金管理 =====================
def calc_position_size(balance, active_count, mode_params):
    """按资金分级计算每币下单金额（见SPEC.md 2.2）"""
    tier_limit = TIER4
    if balance < TIER2:  tier_limit = TIER1
    elif balance < TIER3: tier_limit = TIER2
    elif balance < TIER4: tier_limit = TIER3

    per_coin = min(balance / max(active_count, 1), tier_limit)
    per_coin = per_coin * mode_params['pos_pct']
    return max(per_coin, 11)  # 最低$11

# ===================== 网格引擎 =====================
class GridEngine:
    def __init__(self, symbol, entry_price, grids, grid_profit, atr, ex, capital):
        self.symbol = symbol
        self.entry_price = entry_price
        self.grids = grids
        self.grid_profit = grid_profit
        self.atr = atr
        self.ex = ex
        self.capital = capital

        grid_range = max(atr * 3, entry_price * 0.12)
        self.upper = entry_price + grid_range / 2
        self.lower = max(entry_price - grid_range / 2, entry_price * 0.94)
        self.grid_width = (self.upper - self.lower) / grids
        self.positions = {}  # {idx: {buy_price, qty, sold, target, sl, ts_triggered, ts_price}}

        log(f"[网格] {symbol} 中心${entry_price:.2f} [{self.lower:.2f}~{self.upper:.2f}] "
            f"{grids}格/{grid_profit*100:.1f}% 资金${capital:.2f}")

    def grid_index(self, price):
        if price >= self.upper: return self.grids
        if price <= self.lower: return -1
        return int((price - self.lower) / self.grid_width)

    def _round_qty(self, qty):
        info = self.ex._get_symbol_info(self.symbol)
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize']); break
        return float(qty) // step * step

    def invest_per_grid(self):
        active = len([p for p in self.positions.values() if not p.get('sold')])
        if active >= self.grids: return 0
        return min(self.capital / (self.grids - active), self.capital * 0.25)

    def buy_grid(self, idx, price):
        invest = self.invest_per_grid()
        if invest < 11: return

        qty = self._round_qty(invest / price)
        if qty <= 0: return
        if qty * price < self.ex.get_min_notional(self.symbol): return

        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                self.positions[idx] = {
                    'buy_price': price, 'qty': qty, 'sold': False,
                    'target': price * (1 + self.grid_profit),
                    'sl': price * (1 - SL_PCT),
                    'ts_triggered': False, 'ts_price': 0,
                    'bought_at': time.time(),
                }
                log(f"[格买入] {self.symbol}格{idx}@{price:.4f} qty={qty:.4f} "
                    f"→{price*(1+self.grid_profit):.4f} SL{price*(1-SL_PCT):.4f}")
        except Exception as e:
            log(f"[格买入失败] {self.symbol}格{idx}: {e}")

    def _sell_grid(self, idx, cur_price, reason):
        pos = self.positions.get(idx)
        if not pos or pos.get('sold'): return
        qty = pos['qty']
        if qty <= 0: return
        try:
            ok = self.ex.market_sell(self.symbol, qty)
            if ok:
                pnl = (cur_price - pos['buy_price']) / pos['buy_price'] * 100
                log(f"[格卖出] {self.symbol}格{idx}@{cur_price:.4f}({pnl:+.2f}%) {reason}")
                pos['sold'] = True
                pos['sold_price'] = cur_price
                pos['sold_at'] = time.time()
        except Exception as e:
            log(f"[格卖出失败] {self.symbol}格{idx}: {e}")

    def check(self, cur_price):
        idx = self.grid_index(cur_price)

        # 检查所有格子
        for g_idx, pos in list(self.positions.items()):
            if pos.get('sold') or pos['qty'] <= 0: continue
            bp = pos['buy_price']

            # 止损
            if cur_price <= pos['sl']:
                self._sell_grid(g_idx, cur_price, "SL")
                continue

            # 止盈
            if cur_price >= pos['target']:
                self._sell_grid(g_idx, cur_price, f"TP+{self.grid_profit*100:.1f}%")
                continue

            # 追踪止损
            profit = (cur_price - bp) / bp
            if profit > 0.06 and not pos.get('ts_triggered'):
                pos['ts_triggered'] = True
                pos['ts_price'] = cur_price * (1 - TS_PCT)
                log(f"[TS激活] {self.symbol}格{g_idx}@{cur_price:.4f}")
            if pos.get('ts_triggered') and cur_price <= pos['ts_price']:
                self._sell_grid(g_idx, cur_price, f"TS")

        # 买入新格子
        if 0 <= idx < self.grids:
            if idx not in self.positions:
                self.buy_grid(idx, cur_price)
            else:
                pos = self.positions[idx]
                if pos.get('sold') and pos.get('released'):
                    self.buy_grid(idx, cur_price)

    def release_funds(self):
        released = 0
        for pos in self.positions.values():
            if pos.get('sold') and not pos.get('released'):
                released += pos['buy_price'] * pos['qty']
                pos['released'] = True
        return released

    def detect_manual_close(self, has_position_api):
        for idx, pos in list(self.positions.items()):
            if pos.get('sold') or pos['qty'] <= 0: continue
            if not has_position_api:
                log(f"[⚠️ 手动平仓] {self.symbol}格{idx}@{pos['buy_price']:.4f}")
                pos['sold'] = True
                pos['sold_at'] = time.time()
                pos['released'] = False

    def pnl(self, cur_price):
        realized = unrealized = invested = 0
        active = 0
        for pos in self.positions.values():
            bp = pos.get('buy_price', 0); q = pos.get('qty', 0)
            if bp <= 0 or q <= 0: continue
            invested += bp * q
            if pos.get('sold'):
                realized += (pos.get('sold_price', bp) - bp) * q
            else:
                active += 1
                unrealized += (cur_price - bp) * q
        return {
            'invested': invested, 'realized': realized,
            'unrealized': unrealized, 'total': realized + unrealized,
            'active': active, 'pnl_pct': (realized+unrealized)/invested*100 if invested > 0 else 0
        }

    def adjust_center(self, new_price):
        drift = abs(new_price - self.entry_price) / self.entry_price
        if drift > 0.08:
            log(f"[格重置] {self.symbol}漂移{drift*100:.1f}% ${self.entry_price:.2f}→${new_price:.2f}")
            self.entry_price = new_price
            r = max(self.atr * 3, new_price * 0.12)
            self.upper = new_price + r / 2
            self.lower = max(new_price - r / 2, new_price * 0.94)
            self.grid_width = (self.upper - self.lower) / self.grids

# ===================== 趋势引擎 =====================
class TrendEngine:
    def __init__(self, symbol, ex):
        self.symbol = symbol
        self.ex = ex
        self.position = None

    def _round_qty(self, qty):
        info = self.ex._get_symbol_info(self.symbol)
        for f in info.get('filters', []):
            if f['filterType'] == 'LOT_SIZE':
                step = float(f['stepSize']); break
        return float(qty) // step * step

    def buy(self, price, qty):
        qty = self._round_qty(qty)
        if qty <= 0 or qty * price < self.ex.get_min_notional(self.symbol): return False
        try:
            ok = self.ex.market_buy(self.symbol, qty)
            if ok:
                self.position = {
                    'qty': qty, 'entry': price, 'peak': price,
                    'sl': price * (1 - SL_PCT),
                    'tp1': price * (1 + TP_TREND1),  # +15%
                    'tp2': price * (1 + TP_TREND2),  # +25%
                    'tp1_done': False,
                    'opened_at': time.time(),
                }
                log(f"[趋买入] {self.symbol}@{price:.4f} qty={qty:.4f} "
                    f"SL{price*(1-SL_PCT):.4f} TP1{price*(1+TP_TREND1):.4f} TP2{price*(1+TP_TREND2):.4f}")
                return True
        except Exception as e:
            log(f"[趋买入失败] {self.symbol}: {e}")
            return False

    def has_position(self):
        if not self.position: return False
        try:
            trades = self.ex.get_my_trades(self.symbol, limit=5)
            for t in trades:
                if t.get('isBuyer'):
                    tp = float(t.get('price', 0))
                    if tp > 0 and abs(tp - self.position['entry']) / self.position['entry'] < 0.1:
                        return True
            return False
        except: return True

    def check(self, cur_price):
        if not self.position: return
        p = self.position

        # 手动平仓检测
        if not self.has_position():
            pnl = (cur_price - p['entry']) / p['entry'] * 100
            log(f"[⚠️ 趋手动平仓] {self.symbol}@{cur_price:.4f}({pnl:+.2f}%)")
            self.position = None
            return

        # 更新峰值
        if cur_price > p['peak']: p['peak'] = cur_price
        pnl = (cur_price - p['entry']) / p['entry'] * 100
        peak_gain = (p['peak'] - p['entry']) / p['entry'] * 100

        # 止损
        if cur_price <= p['sl']:
            self._sell(cur_price, f"SL {pnl:+.1f}%")
            return

        # TP1: +15% 止盈50%
        if cur_price >= p['tp1'] and not p['tp1_done']:
            self._sell_pct(cur_price, 0.5, f"TP1+15%")
            p['tp1_done'] = True

        # TP2: +25% 止盈剩余
        if cur_price >= p['tp2']:
            self._sell_pct(cur_price, 1.0, f"TP2+25%")
            return

        # 追踪止损
        if peak_gain > 20:
            ts = p['peak'] * 0.92
            if cur_price <= ts: self._sell(cur_price, f"TSE20 {pnl:+.1f}%")
        elif peak_gain > 15:
            ts = p['peak'] * 0.95
            if cur_price <= ts: self._sell(cur_price, f"TSE15 {pnl:+.1f}%")
        elif peak_gain > 10:
            ts = p['peak'] * 0.97
            if cur_price <= ts: self._sell(cur_price, f"TSE10 {pnl:+.1f}%")

        # 趋势破坏
        data = get_klines(self.ex, self.symbol)
        if data:
            ema20 = calc_ema(data['closes'], EMA_PERIOD)
            rsi = calc_rsi(data['closes'], RSI_PERIOD)
            if ema20 and cur_price < ema20 * 0.96:
                self._sell(cur_price, f"趋破坏 {pnl:+.1f}%")
            elif rsi > 75:
                self._sell(cur_price, f"RSI过热 {pnl:+.1f}%")

    def _sell_pct(self, cur_price, pct, reason):
        if not self.position: return
        qty = round(self.position['qty'] * pct, 6)
        try:
            ok = self.ex.market_sell(self.symbol, qty)
            if ok:
                pnl = (cur_price - self.position['entry']) / self.position['entry'] * 100
                log(f"[趋分批止盈] {self.symbol}@{cur_price:.4f}卖{pct*100:.0f}%({pnl:+.2f}%) {reason}")
                self.position['qty'] -= qty
        except Exception as e:
            log(f"[趋止盈失败] {self.symbol}: {e}")

    def _sell(self, cur_price, reason):
        if not self.position: return
        try:
            ok = self.ex.market_sell(self.symbol, self.position['qty'])
            if ok:
                pnl = (cur_price - self.position['entry']) / self.position['entry'] * 100
                log(f"[趋卖出] {self.symbol}@{cur_price:.4f}({pnl:+.2f}%) {reason}")
                self.position = None
        except Exception as e:
            log(f"[趋卖出失败] {self.symbol}: {e}")

# ===================== 状态管理器 =====================
class StateManager:
    def __init__(self, ex, state_file):
        self.ex = ex
        self.fpath = state_file
        self.data = self._load()
        self.high_water = self.data.get('high_water', 0)
        self.total_profit_taken = self.data.get('total_profit_taken', 0)
        self.loss_streak = self.data.get('loss_streak', 0)
        self.last_loss_time = self.data.get('last_loss_time', 0)
        self.lock_until = self.data.get('lock_until', 0)
        self.daily_loss = self.data.get('daily_loss', 0)
        self.daily_reset_time = self.data.get('daily_reset_time', 0)
        self.last_manual_check = 0

    def _load(self):
        try:
            with open(self.fpath) as f: return json.load(f)
        except: return {}

    def save(self):
        self.data.update({
            'high_water': self.high_water,
            'total_profit_taken': self.total_profit_taken,
            'loss_streak': self.loss_streak,
            'last_loss_time': self.last_loss_time,
            'lock_until': self.lock_until,
            'daily_loss': self.daily_loss,
            'daily_reset_time': self.daily_reset_time,
            'saved_at': time.time(),
        })
        with open(self.fpath, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def get_balance(self):
        try: return self.ex.get_balance("USDT")
        except: return 0.0

    def record_loss(self):
        self.loss_streak += 1
        self.last_loss_time = time.time()
        self.save()

    def record_win(self):
        if self.loss_streak > 0:
            self.loss_streak = 0
            self.save()

    def check_crash_protection(self):
        """熔断检查：连续3亏暂停15分钟"""
        if self.loss_streak >= CRASH_LIMIT:
            elapsed = time.time() - self.last_loss_time
            if elapsed < CRASH_PAUSE:
                remaining = int(CRASH_PAUSE - elapsed)
                log(f"[熔断] 连亏{CRASH_LIMIT}次，暂停{remaining//60}分钟")
                return False
            else:
                self.loss_streak = 0
        return True

    def check_drawdown_protection(self, balance):
        """回撤保护：余额从高点跌20%全部止损"""
        if self.high_water > 0 and balance < self.high_water * (1 - DRAWDOWN_PROTECT):
            log(f"[⚠️ 回撤保护] ${self.high_water:.2f}→${balance:.2f}，清仓止损")
            self.lock_until = time.time() + 1800  # 锁30分钟
            self.save()
            return False
        return True

    def check_daily_loss(self, balance, high_water):
        """单日最大亏损8%"""
        now = time.time()
        day_start = now - (now % 86400)
        if self.daily_reset_time < day_start:
            self.daily_loss = 0
            self.daily_reset_time = day_start
        if high_water > 0:
            day_loss_pct = (high_water - balance) / high_water
            if day_loss_pct > MAX_DAILY_LOSS:
                log(f"[⚠️ 单日止损] 日内亏{day_loss_pct*100:.1f}%>8%，暂停1小时")
                self.lock_until = time.time() + 3600
                return False
        return True

    def is_locked(self):
        if self.lock_until > time.time():
            return True
        return False

    def update_high_water(self, balance):
        if balance > self.high_water:
            self.high_water = balance

    def check_take_profit(self, balance):
        """提盈：余额>高点×120%时提取50%利润"""
        if self.high_water > 0 and balance >= self.high_water * (1 + TAKE_PROFIT_PCT):
            profit = balance - self.high_water
            if profit >= 5:
                take = profit * 0.5
                log(f"💰 提盈! +${take:.2f} | 余额${balance:.2f} | 高点${self.high_water:.2f}")
                self.high_water = balance * 0.90
                self.total_profit_taken += take
                self.save()
                return take
        if balance > self.high_water:
            self.high_water = balance
        return 0

# ===================== 主程序 =====================
def main():
    global cfg
    cfg = load_config()
    ex = BinanceSpotAdapter(cfg['api_key'], cfg['secret'])
    sm = StateManager(ex, STATE_FILE)

    log("=" * 70)
    log("📊 现货智能网格+趋势机器人 v3.0 Final 🦞")
    log(f"   币种: {COINS}")
    log(f"   网格: {GRID_COUNT}格/{GRID_PROFIT*100:.1f}% | 趋势:TP{TP_TREND2*100:.0f}%/SL{SL_PCT*100:.0f}%")
    log(f"   熔断: 连亏{CRASH_LIMIT}次暂停 | 回撤:>{DRAWDOWN_PROTECT*100:.0f}%清仓 | 日亏:>{MAX_DAILY_LOSS*100:.0f}%暂停")
    log(f"   提盈: ≥{TAKE_PROFIT_PCT*100:.0f}%提取50% | 复利: {COMPOUND_PCT*100:.0f}%再投")
    log("=" * 70)

    balance = sm.get_balance()
    log(f"USDT余额: ${balance:.2f}")

    grid_engines = {}
    trend_engines = {}
    last_scan = last_save = 0
    cycle = 0

    while True:
        try:
            now = time.time()
            cycle += 1
            balance = sm.get_balance()  # 实时余额

            # === 锁定检查 ===
            if sm.is_locked():
                time.sleep(30)
                continue

            if not sm.check_crash_protection():
                time.sleep(30)
                continue

            if not sm.check_drawdown_protection(balance):
                # 全部止损
                for eng in grid_engines.values():
                    try:
                        cur = ex.get_price(eng.symbol)
                        for idx in list(eng.positions.keys()):
                            eng._sell_grid(idx, cur, "回撤保护")
                    except: pass
                for eng in trend_engines.values():
                    try:
                        cur = ex.get_price(eng.symbol)
                        if eng.position: eng._sell(cur, "回撤保护")
                    except: pass
                time.sleep(30)
                continue

            # === 市场扫描（每3分钟）===
            if now - last_scan >= SCAN_INTERVAL:
                last_scan = now
                sm.check_take_profit(balance)
                sm.save()

                log("-" * 60)
                log(f"📈 #{cycle} 余额${balance:.2f} | 高点${sm.high_water:.2f}")
                signals = {}
                for sym in COINS:
                    try:
                        info = detect_market_mode(sym, ex)
                        if info: signals[sym] = info
                    except Exception as e:
                        log(f"[扫描错误] {sym}: {e}")

                mode_emoji = {
                    "TREND_UP": "📈", "TREND_DOWN": "📉",
                    "VOLATILE_OVERSOLD": "🔴", "VOLATILE_OVERBOUGHT": "🟠",
                    "RANGE_BOUND": "📊", "CRISIS": "💥"
                }
                sig_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}

                for sym, info in signals.items():
                    me = mode_emoji.get(info['mode'], "⚪")
                    se = sig_emoji.get("HOLD", "⚪")
                    if info['mode'] == "VOLATILE_OVERBOUGHT" or info['mode'] == "CRISIS":
                        se = sig_emoji["SELL"]
                    elif info['mode'] in ("TREND_UP", "VOLATILE_OVERSOLD") and info['trend_score'] >= 5:
                        se = sig_emoji["BUY"]
                    log(f"  {me}{se} {sym:10s} ${info['price']:>10.4f} | "
                        f"RSI={info['rsi_1h']:>5.1f} | {info['mode']:22s} | "
                        f"G={info['grid_score']:>4.1f} T={info['trend_score']:>4.1f}")

                # === 手动平仓检测 ===
                for sym in list(grid_engines.keys()):
                    try:
                        trades = ex.get_my_trades(sym, limit=10)
                        has_api = any(t.get('isBuyer') for t in trades)
                        grid_engines[sym].detect_manual_close(has_api)
                    except: pass
                for sym, eng in list(trend_engines.items()):
                    if eng.position and not eng.has_position():
                        log(f"[⚠️ 趋势手动平仓] {sym}")
                        eng.position = None

                # === 动态开仓 ===
                active_grids = len(grid_engines)
                active_trend = len([e for e in trend_engines.values() if e.position])
                active_total = active_grids + active_trend

                # 释放网格卖出资金
                released = sum(eng.release_funds() for eng in grid_engines.values())
                investable = balance + released

                # BUY信号排序
                buy_list = sorted(
                    [(s, i) for s, i in signals.items()
                     if s not in grid_engines and s not in trend_engines
                     and (i['mode'] in ("TREND_UP", "VOLATILE_OVERSOLD")
                          or (i['mode'] == "RANGE_BOUND" and i['grid_score'] >= 5))],
                    key=lambda x: -(x[1]['trend_score'] * x[1]['trend_bias']
                                   + x[1]['grid_score'] * (1 - x[1]['trend_bias']))
                )

                for sym, info in buy_list:
                    if active_total >= MAX_POSITIONS: break
                    if investable < 15: break

                    per_coin = calc_position_size(balance, max(active_total, 1), info)
                    if info['trend_bias'] >= 0.7:
                        eng = TrendEngine(sym, ex)
                        if eng.buy(info['price'], per_coin / info['price']):
                            trend_engines[sym] = eng
                            investable -= per_coin
                            active_trend += 1
                            active_total += 1
                            log(f"[趋势开仓] {sym}@{info['price']:.2f} 模式:{info['mode']}")
                    elif info['grids'] > 0:
                        eng = GridEngine(sym, info['price'], info['grids'],
                                        info['grid_profit'], info['atr'], ex, per_coin)
                        grid_engines[sym] = eng
                        investable -= per_coin
                        active_grids += 1
                        active_total += 1
                        log(f"[网格开仓] {sym}@{info['price']:.2f} {info['grids']}格 模式:{info['mode']}")

                # === SELL信号平仓 ===
                for sym, info in signals.items():
                    if info['mode'] in ("VOLATILE_OVERBOUGHT", "CRISIS", "TREND_DOWN"):
                        if sym in grid_engines:
                            cur = info['price']
                            eng = grid_engines[sym]
                            p = eng.pnl(cur)
                            if p['active'] > 0:
                                log(f"[SELL平网] {sym} {p['pnl_pct']:+.1f}%")
                                for idx in list(eng.positions.keys()):
                                    eng._sell_grid(idx, cur, "SELL信号")

                # === 死亡引擎清理 ===
                for sym in list(grid_engines.keys()):
                    try:
                        info = signals.get(sym)
                        if not info: continue
                        cur = info['price']
                        p = grid_engines[sym].pnl(cur)
                        # 浮亏>20%在下跌趋势
                        if p['unrealized'] < -grid_engines[sym].capital * 0.20 and info['trend_down']:
                            log(f"[死亡止损] {sym} {p['pnl_pct']:+.1f}%")
                            for idx in list(grid_engines[sym].positions.keys()):
                                grid_engines[sym]._sell_grid(idx, cur, "死亡止损")
                            sm.record_loss()
                    except: pass
                # 清空已平仓引擎
                grid_engines = {k: v for k, v in grid_engines.items()
                              if any(not pos.get('sold') for pos in v.positions.values())}
                trend_engines = {k: v for k, v in trend_engines.items() if v.position is not None}

            # === 实时检查（每20秒）===
            for sym, eng in list(grid_engines.items()):
                try:
                    cur = ex.get_price(sym)
                    eng.check(cur)
                    eng.adjust_center(cur)
                except: pass
            for sym, eng in list(trend_engines.items()):
                try:
                    cur = ex.get_price(sym)
                    eng.check(cur)
                    if eng.position is None:
                        del trend_engines[sym]
                except: pass

            # === 状态保存 ===
            if now - last_save >= SAVE_INTERVAL:
                last_save = now
                total_inv = total_pnl_val = active_g = 0
                for eng in grid_engines.values():
                    try:
                        cur = ex.get_price(eng.symbol)
                        p = eng.pnl(cur)
                        total_inv += p['invested']
                        total_pnl_val += p['total']
                        active_g += p['active']
                    except: pass
                active_t = sum(1 for e in trend_engines.values() if e.position)
                log(f"[{cycle}] 网格{active_g}格 | 趋势{active_t}仓 | "
                    f"总投入${total_inv:.2f} | 盈亏${total_pnl_val:+.2f} | "
                    f"余额${balance:.2f} | 提取${sm.total_profit_taken:.2f} | "
                    f"连亏{sm.loss_streak}次")
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
