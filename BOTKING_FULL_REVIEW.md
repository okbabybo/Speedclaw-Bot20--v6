# SpeedClaw BotKing 完整工作流 & 策略清单
## 系统性Bug审查报告 v1.1

> 日期：2026-06-23 20:47
> 审查者：混沌龙虾 🦞

---

# 一、BotKing 完整工作流

```
┌─────────────────────────────────────────────────────────────────────┐
│                        启动初始化                                    │
│  ① 加载 config_exchange.yaml → 获取Binance API Key                  │
│  ② StateManager 加载状态文件 (bot_king_state.json)                   │
│  ③ 获取USDT余额                                                     │
│  ④ 初始化: grid_engines={}, trend_engines={}                        │
└─────────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────────┐
│                     主循环 (每20秒)                                   │
│                                                                     │
│  [风控前置检查]                                                     │
│  ① balance = sm.get_balance()                                      │
│  ② sm.is_locked() → 被锁定则等待30秒跳过                            │
│  ③ sm.check_crash_protection() → 3连亏熔断15分钟                   │
│  ④ sm.check_loss_cooldown() → 1亏5分/2亏10分/3亏15分冷静            │
│  ⑤ 熊市锁定检查 → 熔断+TREND_DOWN则等待60秒                         │
│  ⑥ sm.check_drawdown_protection() → 从高点跌20%→全仓止损+锁30分    │
│                                                                     │
│  [市场扫描 - 每180秒]                                               │
│  ⑦ detect_market_mode() → 6种模式                                   │
│     TREND_UP / TREND_DOWN / RANGE_BOUND                              │
│     VOLATILE_OVERSOLD / VOLATILE_OVERBOUGHT / CRISIS                 │
│  ⑧ 收集6个币的信号 (price, RSI, mode, grids, atr, trend_bias)        │
│  ⑨ 排序买入信号 (total_score从高到低)                               │
│  ⑩ 分配资金开仓                                                    │
│                                                                     │
│  [实时检查 - 每20秒]                                                │
│  ⑪ eng.check(cur_price) → 检查止盈/止损/TS                          │
│  ⑫ eng.check_phased_open(cur_price) → 分批开仓检查                   │
│  ⑬ eng.adjust_center(cur_price)                                     │
│                                                                     │
│  [手动平仓检测 - 每300秒]                                           │
│  ⑭ ex.get_spot_holdings() vs 状态文件对比                           │
│                                                                     │
│  [状态保存 - 每60秒]                                                │
│  ⑮ sm.save() → 写入 bot_king_state.json                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

# 二、策略完整参数表

## 2.1 核心参数

| 参数 | 当前值 | 说明 |
|------|--------|------|
| GRID_PROFIT | 0.6% | 每格止盈目标 |
| GRID_VOL_PROFIT | 1.0% | 高波动每格止盈 |
| SL_PCT | 12% | 止损比例 |
| TS_PCT | 3% | 追踪回撤比例 |
| TP_TREND1 | 15% | 趋势第一止盈 |
| TP_TREND2 | 25% | 趋势第二止盈 |
| TS_TREND_PCT | 5% | 趋势追踪回撤 |
| MAX_POSITIONS | 3 | 最多同时持仓 |

## 2.2 ATR自适应网格

| ATR波动 | 格数 | 每格利润 |
|---------|------|---------|
| >5% (高) | 2格 | 1.0% |
| 2-5% (中) | 4格 | 0.6% |
| <2% (低) | 6格 | 0.4% |

## 2.3 资金分级

| 余额范围 | 每币分配 | 最大币数 |
|---------|---------|---------|
| $20-50 | $50 | 1 |
| $50-200 | $150 | 2 |
| $200-1000 | $500 | 3 |
| >$1000 | $1500 | 3 |

## 2.4 模式判断

| 模式 | 日线 | 4H | 1H | RSI | 操作 |
|------|------|----|----|-----|------|
| TREND_UP | EMA20上 | EMA20上 | EMA20上 | 任意 | 趋势开仓 |
| TREND_DOWN | EMA20下 | EMA20下 | EMA20下 | <50 | 轻仓/观望 |
| RANGE_BOUND | 震荡 | 震荡 | 震荡 | 40-60 | 网格开仓 |
| VOL_OVERSOLD | 放量大跌 | - | RSI<35 | >30%波幅 | 网格+趋势 |
| VOL_OVERBOUGHT | 放量大涨 | - | RSI>65 | >30%波幅 | 全部止盈 |
| CRISIS | RSI>80 | - | RSI<20 | 极端 | 全部暂停 |

---

# 三、Bug清单 & 严重度

## 🚨 严重Bug（必须修复）

### Bug #1: 网格止损未调用 record_loss()
**位置：** `_sell_grid()` 方法
**问题：** 当网格触发止损(SL)时，只调用了 `market_sell`，但没有调用 `sm.record_loss()`。导致：
- 连亏计数不增加
- 熔断保护失效
- 止损冷静期失效

```python
# 当前代码（Bug）
def _sell_grid(self, idx, cur_price, reason):
    ...
    if self.ex.market_sell(self.symbol, qty):
        ...
        if reason.startswith('TP'):  # 只有TP才record_loss
            locked = profit * PROFIT_LOCK
            ...
            sm.record_loss()  # ← SL时没有调用！
```

**修复：** SL时也要调用 `sm.record_loss()`，TrendEngine同理

---

### Bug #2: 已关闭的网格引擎残留在内存中
**位置：** 主循环
**问题：** 所有网格仓位触发止盈/止损后，`grid_engines[sym]` 永远不会被删除。下次扫描时因为 symbol 已在 `grid_engines` 中，即使所有仓位都平了，也不会重新建仓。

```python
# 当前代码
elif info['grids'] > 0:
    eng = GridEngine(...)  # 如果sym已在grid_engines就跳过
    grid_engines[sym] = eng  # 但如果eng所有格都平了，eng还在字典里！
```

**修复：** 当GridEngine所有position都sold时，从grid_engines中删除

---

### Bug #3: 部分手动平仓无法检测
**位置：** `detect_manual_close()`
**问题：** 只检测 `api_qty <= 0`（完全平仓），无法检测部分平仓（如持有0.01 BTC卖了0.005）

```python
# 当前代码
if api_qty <= 0:  # 只检测全部清仓
    eng.detect_manual_close(api_qty)
```

**修复：** 比较"状态文件记录的持仓数量" vs "API实际持仓"，只要不一致就标记

---

### Bug #4: 趋势引擎止损未调用 record_loss()
**位置：** `TrendEngine._sell()`
**问题：** 同Bug #1，趋势止损 `_sell(reason="SL")` 没有调用 `sm.record_loss()`

---

### Bug #5: 分批开仓第二批永远无法触发
**位置：** `check_phased_open()`
**问题：** `_open_count >= phase1_limit` 后，所有后续调用直接 return。但 `pending_profit` 只有在 `reason.startswith('TP')` 时才会增加。SL时不增加 pending_profit，所以第二批永远没资金开。

```python
# 当前代码
if self._open_count >= self.phase1_limit: return  # 第二批永远不开
```

**修复：** Phase2的开仓资金来源应该是"已实现利润的30%复利"，而不是"只有TP才增加pending_profit"

---

## 🟡 中等Bug（建议修复）

### Bug #6: 日亏保护未实现
**位置：** `StateManager`
**问题：** `MAX_DAILY_LOSS = 0.08` 定义了但从未使用，没有每日亏损统计和触发逻辑

```python
# 定义了但没用
MAX_DAILY_LOSS = 0.08   # 单日最大亏损8%
```

**修复：** 每天UTC0点重置，记录当日亏损，触发后暂停1小时

---

### Bug #7: 止损后资金未归还
**位置：** `_sell_grid()`
**问题：** 止损后 `investable` 没有减少（因为 `self.capital` 是固定分配），但也没有任何机制将止损亏损反映到余额中。止损后余额应该减少，但代码中没有体现。

---

### Bug #8: 网格引擎的 grid_width 计算在价格大幅波动后失效
**位置：** `GridEngine.__init__()`
**问题：** 网格区间在开仓时固定，如果价格短期内大幅波动超出upper/lower范围，网格会失效

```python
# 固定网格区间
grid_range = max(atr * 3, entry_price * 0.12)
self.upper = entry_price + grid_range / 2
self.lower = entry_price - grid_range / 2
# 如果价格涨了20%，网格区间就完全失效了
```

**修复：** 加入 `adjust_center()` 动态调整网格区间（目前是空函数）

---

### Bug #9: 回撤保护用的是余额而非已实现利润
**位置：** `check_drawdown_protection()`
**问题：** 网格在浮盈状态时，余额>实际本金，用余额判断回撤会误判

```python
# 当前
balance < self.high_water * (1 - DRAWDOWN_PROTECT)
# 浮盈100时，高点100，现价90，但实际亏损是0（还没卖）
```

**修复：** 应该用"已实现损益"或"已实现+浮动"综合判断

---

## 🔸 轻微问题（可接受/设计如此）

| # | 问题 | 说明 |
|---|------|------|
| 10 | 趋势TP踏空 | 设计如此，趋势不贪心 |
| 11 | SCAN_INTERVAL=180秒 | 20秒实时检查足够了 |
| 12 | BTC模式代表全局 | 单一信号简化逻辑 |

---

# 四、修复优先级

| 优先级 | Bug | 修复工作量 |
|--------|-----|----------|
| P0 | #1 网格SL未record_loss | 1行 |
| P0 | #4 趋势SL未record_loss | 1行 |
| P0 | #2 死引擎残留 | 5行 |
| P0 | #3 部分平仓漏检 | 3行 |
| P1 | #5 Phase2无法开仓 | 重构逻辑 |
| P1 | #6 日亏未实现 | 15行 |
| P2 | #7 止损资金未归还 | 10行 |
| P2 | #8 网格区间固定 | 20行 |
| P2 | #9 回撤保护误判 | 10行 |

---

# 五、策略优化建议

### 建议1: 加入"RSI回归"信号
当 RSI < 30 且价格触及网格下限时，这是比普通网格更强的买入信号。可以给 VOLATILE_OVERSOLD 模式额外加分。

### 建议2: 趋势引擎加入移动止损
当利润 > 20% 后，将止损线上移到成本价，实现"无论如何不亏"。

### 建议3: 多个交易所支持
当前只支持Binance现货，可考虑加入OKX。

### 建议4: 资金共享机制
当一个币止损后释放的资金，应该立即可以被另一个币使用。当前 `investable` 只在扫描开始时计算一次。
