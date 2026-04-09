# 📘《ETH永续合约全自动量化交易系统 v4.0》（终版）

> 可直接交付技术团队或导入 OpenClaw 执行

---

## 一、系统目标

| 目标 | 说明 |
|------|------|
| 多空双向 | 不依赖方向，永远有机会 |
| 自动识别行情 | 4种状态自动判定，无需主观 |
| 自动开仓/平仓 | 全流程无人值守 |
| 严格风险控制 | 防爆仓，守护本金 |
| 稳定滚动收益 | 趋势+高频+区间三路并行 |
| **手动干预防护** | **用户手动操作可检测、可同步、不失控** |

---

## 二、交易标的

| 品种 | 角色 | 说明 |
|------|------|------|
| ETH-USDT-SWAP | 主交易 | 波动大，流动性好 |
| BTC-USDT-SWAP | 方向过滤 | BTC是龙头，ETH必须参考BTC方向 |

---

## 三、系统架构（6模块）

```
┌─────────────────────────────────────────────────────────────┐
│                     交易系统主循环 v4.0                       │
├────────────────┬────────────────┬──────────────────────────┤
│ 持仓同步层      │  市场状态识别   │   信号生成              │
│ (Position      │  (Market       │   (Signal               │
│  Sync Layer)   │   Regime)       │    Engine)              │
├────────────────┼────────────────┼──────────────────────────┤
│ 交易执行       │  风险控制       │   持仓监控              │
│ (Execution     │  (Risk         │   (Position             │
│  Module)       │   Control)     │    Monitor)             │
├────────────────┴────────────────┴──────────────────────────┤
│                  手动干预防护层                              │
│            (Manual Intervention Protection)                  │
└─────────────────────────────────────────────────────────────┘

重要：持仓同步层是每次主循环的第一步
重要：手动干预防护层独立运行，持续监控
```

---

## 四、手动干预防护层（v4.0 新增核心模块）

> ⚠️ 这是全自动系统的生死线，必须优先实现

### 4.1 设计原则

```
原则1: 交易所数据是唯一真实来源
        → 系统内部状态可能过时，交易所状态永远最新
        → 每次主循环第一步必须同步

原则2: 检测到手动干预 ≠ 停止系统
        → 手动开仓：告警，询问是否接管
        → 手动平仓：同步清除系统记录，取消相关挂单，继续运行
        → 不允许手动干预破坏系统运行

原则3: 操作前后必须完整性校验
        → 开仓/平仓/止盈/止损 任何操作前后，数量必须对得上
        → 对不上 = 被人动过 = 报警暂停
```

### 4.2 三层防护体系

```
第一层: 持仓实时同步（每次主循环第一步）
        → 交易所真实持仓 vs 系统内部记录
        → 数量不一致 → 立即同步并告警

第二层: 挂单同步检查（每次主循环第一步）
        → 交易所未成交挂单 vs 系统挂单记录
        → 用户手动挂单/撤单 → 同步并告警

第三层: 操作完整性校验（每次操作前后）
        → 操作前记录数量 → 操作后验证数量
        → 数量对不上 → 系统暂停，等用户确认
```

### 4.3 持仓同步逻辑

```python
def sync_positions():
    """
    从交易所拉取真实持仓，与系统内部状态比对
    任何不一致 → 立即同步 + 告警通知用户
    """
    # Step 1: 获取交易所实时持仓
    exchange_positions = okx_api.get_all_positions()
    # [{instrument_id: "ETH-USDT-SWAP", live_qty: 50, avg_price: 1000}, ...]

    # Step 2: 获取系统内部持仓记录
    system_positions = get_internal_positions()
    # [{instrument_id: "ETH-USDT-SWAP", qty: 50, avg_price: 1000, status: "OPEN"}, ...]

    # Step 3: 比对每一笔
    for ex_pos in exchange_positions:
        sys_pos = find_system_position(ex_pos.instrument_id)

        if sys_pos is None and ex_pos.live_qty > 0:
            # ===== 情况A: 用户手动开仓（系统完全不知道）=====
            log(f"⚠️ 检测到未记录持仓: {ex_pos.instrument_id} {ex_pos.live_qty}张 @ {ex_pos.avg_price}")
            notify_user(
                f"⚠️ 检测到手动开仓\n"
                f"品种: {ex_pos.instrument_id}\n"
                f"数量: {ex_pos.live_qty}张\n"
                f"均价: {ex_pos.avg_price}\n"
                f"系统未记录此笔持仓。\n"
                f"回复「接管」由系统接管，或「忽略」由您自行处理。"
            )
            # 系统行为: 暂停开新仓，等用户回复

        elif sys_pos is not None:
            # ===== 情况B: 系统有记录，对比数量 =====
            if ex_pos.live_qty == 0 and sys_pos.status == "OPEN":
                # ===== 用户手动全平了 =====
                log(f"🚨 手动全平仓: {ex_pos.instrument_id}")
                log_trade(sys_pos, "MANUAL_CLOSE", ex_pos.avg_price or current_price)
                clear_internal_position(sys_pos)
                cancel_all_pending_orders(ex_pos.instrument_id)
                notify_user(
                    f"🚨 持仓已被手动平仓\n"
                    f"品种: {ex_pos.instrument_id}\n"
                    f"系统记录: {sys_pos.qty}张 @ {sys_pos.avg_price}\n"
                    f"系统已同步清除该笔记录。\n"
                    f"相关挂单已全部取消。"
                )
                # 触发连续亏损计数（如果是在亏损状态下手动平）
                record_loss_if_needed(sys_pos)

            elif ex_pos.live_qty != sys_pos.qty:
                # ===== 用户手动部分平仓 =====
                diff = sys_pos.qty - ex_pos.live_qty
                log(f"⚠️ 部分平仓检测: {diff}张")
                original_avg = sys_pos.avg_price
                update_internal_position(sys_pos, ex_pos.live_qty, ex_pos.avg_price)
                notify_user(
                    f"⚠️ 部分平仓\n"
                    f"品种: {ex_pos.instrument_id}\n"
                    f"平掉: {diff}张\n"
                    f"剩余: {ex_pos.live_qty}张 @ {ex_pos.avg_price}\n"
                    f"系统已同步更新。"
                )

    # Step 4: 检查系统有记录但交易所已无持仓（意外清仓）
    for sys_pos in system_positions:
        if sys_pos.status == "OPEN":
            ex_pos = find_exchange_position(sys_pos.instrument_id)
            if ex_pos is None or ex_pos.live_qty == 0:
                # 系统有单但交易所没了（可能是强平/异常）
                log(f"🚨 意外清仓: {sys_pos.instrument_id}")
                handle_unexpected_close(sys_pos)
```

### 4.4 挂单同步逻辑

```python
def sync_orders():
    """
    检查交易所未成交挂单，与系统挂单记录比对
    用户手动挂单/撤单必须被检测
    """
    exchange_orders = okx_api.get_pending_orders()
    system_orders   = get_internal_pending_orders()

    # 检查: 交易所有但系统没有 = 用户手动挂的单
    for ex_order in exchange_orders:
        sys_order = find_by_exchange_id(ex_order.order_id)
        if sys_order is None:
            log(f"⚠️ 手动挂单检测: {ex_order.instrument_id} {ex_order.side} {ex_order.qty}张")
            notify_user(
                f"⚠️ 检测到手动挂单\n"
                f"品种: {ex_order.instrument_id}\n"
                f"方向: {'做多' if ex_order.side == 'BUY' else '做空'}\n"
                f"数量: {ex_order.qty}张\n"
                f"回复「取消」由系统撤单，或「接管」由系统管理。"
            )
            # 默认等待用户指令，不自动处理

    # 检查: 系统有但交易所无 = 用户手动撤的单
    for sys_order in system_orders:
        ex_order = find_exchange_order(sys_order.exchange_order_id)
        if ex_order is None:
            log(f"🚨 挂单被手动撤除: {sys_order.instrument_id}")
            remove_internal_pending_order(sys_order)
            notify_user(
                f"挂单已被手动撤除\n"
                f"品种: {sys_order.instrument_id}\n"
                f"方向: {'做多' if sys_order.side == 'BUY' else '做空'}\n"
                f"数量: {sys_order.qty}张\n"
                f"系统已同步清除。"
            )
```

### 4.5 操作完整性校验

```python
class OperationLock:
    """操作锁：记录操作前后持仓数量，操作后校验完整性"""

    def __init__(self, instrument_id, operation_type, expected_qty_before, expected_qty_after):
        self.instrument_id = instrument_id
        self.operation_type = operation_type  # "OPEN" / "CLOSE" / "TP" / "SL"
        self.expected_before = expected_qty_before
        self.expected_after = expected_qty_after
        self.locked_at = time.time()
        self.timeout = 60  # 秒

    def verify(self):
        """
        操作完成后调用，验证数量是否符合预期
        不符合 → 暂停系统，通知用户
        """
        current_qty = get_exchange_qty(self.instrument_id)
        if current_qty != self.expected_after:
            log(f"🚨 完整性校验失败: {self.instrument_id}")
            log(f"   操作: {self.operation_type}")
            log(f"   预期: {self.expected_after}张")
            log(f"   实际: {current_qty}张")
            pause_system()
            notify_user(
                f"🚨 持仓完整性校验失败\n"
                f"操作: {self.operation_type}\n"
                f"品种: {self.instrument_id}\n"
                f"预期数量: {self.expected_after}张\n"
                f"实际数量: {current_qty}张\n"
                f"系统已暂停，请确认账户状态。"
            )
            return False
        return True


# 使用示例
def execute_open_position(signal, qty):
    before = get_exchange_qty("ETH-USDT-SWAP")
    lock = OperationLock("ETH-USDT-SWAP", "OPEN", before, before + qty)

    # 执行开仓
    okx_api.open(signal, qty)

    # 等待交易所确认（异步情况下需要轮询）
    time.sleep(2)
    if not lock.verify():
        return False  # 校验失败，系统已暂停
    return True
```

### 4.6 手动干预处理规则表

| 情况 | 检测方式 | 系统响应 | 是否暂停 |
|------|----------|----------|----------|
| 用户手动全平仓 | 系统有持仓，交易所=0 | 同步清除，取消挂单，记录盈亏 | 否，继续运行 |
| 用户手动部分平 | 数量不一致 | 同步更新系统记录 | 否，继续运行 |
| 用户手动开仓 | 交易所有持仓但系统无记录 | 暂停，发送「接管/忽略」选项 | **是，等用户回复** |
| 用户手动挂单 | 交易所有挂单但系统无记录 | 暂停，发送「取消/接管」选项 | **是，等用户回复** |
| 用户手动撤单 | 系统有挂单但交易所无 | 同步清除系统挂单记录 | 否，继续运行 |
| 意外清仓（强平等） | 系统有持仓，交易所=0 | 按亏损处理，清除记录 | 否（但计入日亏损统计） |

---

## 五、市场状态识别模块

### 5.1 四状态定义（互斥）

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TREND_UP（上涨趋势）— 允许做多
条件（必须同时满足）:
  ① BTC价格 > EMA20(1H) > EMA60(1H)
  ② ETH价格 > EMA20(1H) > EMA60(1H)
  ③ RSI(4H) 在 [40, 75] 区间（非超买）
  ④ ATR(14) > ATR_SMA(20) × 0.9（波动率扩张中）

TREND_DOWN（下跌趋势）— 允许做空
条件（必须同时满足）:
  ① BTC价格 < EMA20(1H) < EMA60(1H)
  ② ETH价格 < EMA20(1H) < EMA60(1H)
  ③ RSI(4H) 在 [25, 60] 区间（非超卖）
  ④ ATR(14) > ATR_SMA(20) × 0.9

RANGE（区间震荡）— 允许区间+插针
条件（必须同时满足）:
  ① ATR(14) < ATR_SMA(20) × 0.8（波动率收缩）
  ② 布林带宽度 < 过去20日均值 × 0.7（带口收紧）
  ③ 不满足TREND_UP/DOWN任一条件

CHAOS（禁止交易）— 禁止开仓，持有可继续
条件（满足任一即触发）:
  ① BTC和ETH趋势方向矛盾且持续>1小时
  ② ATR(14) < ATR_SMA(20) × 0.5（极度低波动）
  ③ 重大新闻窗口：CPI/FOMC/非农前2小时至后30分钟内
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 5.2 状态切换规则

```
TREND_UP → CHAOS:      停止开多仓，持有可继续
TREND_DOWN → CHAOS:    停止开空仓，持有可继续
RANGE → TREND_UP:      允许做多信号
RANGE → TREND_DOWN:    允许做空信号
CHAOS → RANGE:         等待1小时观察期后再认定
```

---

## 六、信号生成模块

### 6.1 信号类型与优先级

| 优先级 | 信号类型 | 适用状态 | 持仓规则 |
|--------|----------|----------|----------|
| 1 | **插针反转** | RANGE回调段 / 趋势回调段 | 15分钟上限 |
| 2 | **趋势顺势** | TREND_UP / TREND_DOWN | 无时间限制 |
| 3 | **区间波段** | RANGE专享 | 30分钟上限 |

> ⚠️ 插针信号只用于回调/反弹段，不用于趋势加速段

---

### 6.2 趋势顺势信号

#### 做多（TREND_UP状态）

```
入场条件（必须同时满足）:
  ① ETH价格 > EMA20(1H) > EMA60(1H)
  ② RSI(1H) ≤ 60，且 RSI(4H) ≤ 70
  ③ RSI(1H) 刚从超卖区(<35)回升，或在 [40, 50] 区间
  ④ 成交量 > 过去20根(1H)均量 × 1.2
  ⑤ 回调不破前低（回踩EMA20不破最佳）

入场执行:
  首仓 50% → 市价立即入场
  次仓 50% → 限价挂单 = 市价 × (1 - 0.15%)，5分钟未成交撤单

杠杆: 30x
```

#### 做空（TREND_DOWN状态）

```
入场条件（必须同时满足）:
  ① ETH价格 < EMA20(1H) < EMA60(1H)
  ② RSI(1H) ≥ 40，且 RSI(4H) ≥ 30
  ③ RSI(1H) 刚从超买区(>65)回落，或在 [50, 60] 区间
  ④ 成交量 > 过去20根(1H)均量 × 1.2
  ⑤ 反弹不过EMA20（滞涨最佳）

入场执行:
  首仓 50% → 市价立即入场
  次仓 50% → 限价挂单 = 市价 × (1 + 0.15%)，5分钟未成交撤单

杠杆: 30x
```

---

### 6.3 插针反转信号

#### 下影线插针（做多）

```
基础条件（必须同时满足）:
  ① K线为阴线（下引线）
  ② 下影线长度 ≥ 实体长度 × 2倍
  ③ 下影线长度 ≥ K线全长 20%
  ④ 成交量 ≥ 过去5根(5分钟)均量 × 1.5倍

确认条件（满足任意2个）:
  A. RSI(5分钟) < 30 或 RSI(15分钟) < 40
  B. 下影线时段成交量明显放大
  C. 价格触及布林下轨 或 日内重要支撑位
  D. 形态：锤子 / 蜻蜓 / 孕育线

杠杆: 50x
持仓上限: 15分钟
```

#### 上影线插针（做空）

```
基础条件（必须同时满足）:
  ① K线为阳线（上引线）
  ② 上影线长度 ≥ 实体长度 × 2倍
  ③ 上影线长度 ≥ K线全长 20%
  ④ 成交量 ≥ 过去5根(5分钟)均量 × 1.5倍

确认条件（满足任意2个）:
  A. RSI(5分钟) > 70 或 RSI(15分钟) > 60
  B. 上影线时段成交量明显放大
  C. 价格触及布林上轨 或 日内重要压力位
  D. 形态：射击之星 / 孕育线

杠杆: 50x
持仓上限: 15分钟
```

---

### 6.4 区间波段信号（RANGE专享）

```
布林带参数: BB(20, 2)

做多（RANGE下轨）:
  ① 价格触及布林下轨
  ② RSI(1H) < 45
  ③ 触及时段成交量放大
  → 止损 = 入场价 × 0.985

做空（RANGE上轨）:
  ① 价格触及布林上轨
  ② RSI(1H) > 55
  ③ 触及时段成交量放大
  → 止损 = 入场价 × 1.015

持仓上限: 30分钟
```

---

## 七、交易执行模块

### 7.1 仓位计算

```
总资金 = 实时净值 USDT
单笔仓位 = 总资金 × 2%
最大总仓位 = 总资金 × 10%（5笔）
合约面值 = 0.01 ETH/张

张数 = (总资金 × 2%) ÷ (入场价 × 合约面值 ÷ 杠杆)
```

### 7.2 分批入场

```
第1批: 50%仓位，市价立即入场
第2批: 50%仓位，限价挂单

  做多挂单价 = 市价 × (1 - 0.15%)
  做空挂单价 = 市价 × (1 + 0.15%)
  有效期: 5分钟，超时撤单

总仓位: 同向最多2笔（4%），任意时刻不超过10%
```

### 7.3 加权平均入场价

```
加权平均价 = (第1批数量×价格1 + 第2批数量×价格2) ÷ 总数量

例: 首仓50张@1000 + 次仓50张@998
  = (50×1000 + 50×998) ÷ 100 = 999 U
  止损 = 999 × 0.985 = 984.015 U
```

---

## 八、止损规则

### 8.1 固定止损

```
止损幅度: 1.5%（固定）

做多止损 = 入场均价 × (1 - 0.015)
做空止损 = 入场均价 × (1 + 0.015)

执行: 触及止损价，市价全出，不等待
```

### 8.2 移动止损（保本规则）

```
盈利 ≥ +1.0% → 止损移动到入场均价（保本）
盈利 ≥ +1.5% → 止损移动到入场均价 × (1 ± 0.5%)
```

---

## 九、止盈规则

### 9.1 标准止盈（以"当前剩余持仓"为分母）

```
假设: 开仓 N 张，入场均价 P

【第一止盈：盈利 +0.5%】
  平仓: N × 30%
  剩余: N × 70%
  止损移动到: P（保本）

【第二止盈：盈利 +1.0%】
  平仓: (N × 70%) × 40% = N × 28%
  累计: N × 58%
  剩余: N × 42%
  止损移动到: P × 1.005

【第三止盈：盈利 +1.5%】
  平仓: 剩余全部 = N × 42%
  累计: N × 100%（全平）
```

### 9.2 趋势延续（评分≥8分启用）

```
+0.5% 时只平 20%，持有 80%
+1.0% 时平 50%，持有 30%
+1.5% 时平剩余 30%，全平
```

### 9.3 回调止盈

```
A. +1.5% 后出现 0.5% 回调 → 全平
B. +2.0% 后出现 0.75% 回调 → 全平
```

---

## 十、持仓时间管理

| 信号类型 | 持仓上限 | 超时处理 |
|----------|----------|----------|
| 插针反转 | 15分钟 | 强制市价平仓 |
| 区间波段 | 30分钟 | 强制市价平仓 |
| 趋势顺势 | 无限制 | 持有至止盈/止损/趋势反转 |

```
趋势反转判定:
  持仓中，价格反向突破 EMA20(1H) 且 RSI(1H) 穿越 50
  → 平仓，结束
```

---

## 十一、风控模块

### 11.1 风险参数总览

| 参数 | 数值 |
|------|------|
| 单笔仓位 | 总资金 × 2% |
| 最大总仓位 | 总资金 × 10% |
| 止损幅度 | 1.5% |
| 保本激活 | 盈利 ≥ +1.0% |
| 单日最大亏损 | 5% |
| 连续亏损冷却 | ≥3次 → 冷却1小时 |
| 高频持仓上限 | 15分钟 |
| 区间持仓上限 | 30分钟 |

### 11.2 风控铁律

```
① 触及止损 → 立即市价全出，不等待
② 单日亏损 ≥ 5% → 当日停止交易
③ 连续亏损 ≥ 3次 → 冷却1小时
④ 禁止扛单 → 浮亏时不补仓
⑤ 禁止满仓 → 总仓位 ≤ 10%
⑥ 禁止逆势 → 只顺趋势，不抄底不摸顶
⑦ 新闻窗口 → 前2h/后30min不开仓
⑧ 保本优先 → 盈利1%即保本
```

### 11.3 连续亏损处置

```
连续3次止损/超时平仓 → 停止开仓 → 冷却1小时
冷却后 → 等待评分≥8分才可开第4次
冷却期间检测到手动平仓 → 同步清除，继续计时
```

---

## 十二、信号评分系统

```
评分规则（满分10分，≥5分执行）

  +2分  BTC方向明确（与ETH信号同向）
  +2分  ETH趋势符合信号方向
  +2分  RSI处于有利区间
          做多: RSI(1H) 在 [35, 55]
          做空: RSI(1H) 在 [45, 65]
  +2分  成交量放大确认
  +2分  多周期共振（≥3周期同向）

执行:
  8-10分 → 满仓 2%
  5-7分  → 半仓 1%
  <5分   → 不执行
```

---

## 十三、交易过滤器（逐项检查）

```
□ 1. BTC方向明确
□ 2. 非 CHAOS 状态
□ 3. 非新闻窗口（前2h/后30min）
□ 4. ATR > SMA × 0.6
□ 5. 评分 ≥ 5分
□ 6. 可用资金 ≥ 保证金 × 2
□ 7. 总仓位 < 10%
□ 8. 未在冷却期
□ 9. 同向未满2笔
```

---

## 十四、主交易循环 v4.0（完整伪代码）

```python
WHILE True:
    # ═══════════════════════════════════════
    # STEP 0: 持仓+挂单同步（每次循环第一步）
    # ═══════════════════════════════════════
    sync_positions()    # 第一层：真实持仓 vs 系统记录
    sync_orders()        # 第二层：真实挂单 vs 系统记录

    # 检查系统是否被用户暂停
    if system_paused:
        wait_for_user_resume()
        continue

    # ═══════════════════════════════════════
    # STEP 1: 获取数据
    # ═══════════════════════════════════════
    btc_data   = get_btc_ohlcv()      # EMA20/60(1H), RSI(1H/4H), ATR
    eth_data   = get_eth_ohlcv()      # 价格, EMA/RSI/ATR, 成交量
    account    = get_account()        # 余额, 持仓, 可用保证金

    # ═══════════════════════════════════════
    # STEP 2: 判定市场状态
    # ═══════════════════════════════════════
    regime = detect_market_regime(btc_data, eth_data)
    # TREND_UP / TREND_DOWN / RANGE / CHAOS

    # ═══════════════════════════════════════
    # STEP 3: CHAOS 过滤
    # ═══════════════════════════════════════
    if regime == CHAOS:
        monitor_positions()   # 继续监控现有持仓
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 4: 生成信号
    # ═══════════════════════════════════════
    signal, score = generate_signal(regime, eth_data)
    # LONG / SHORT / NONE,  score: 0-10

    if signal == NONE:
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 5: BTC 方向过滤
    # ═══════════════════════════════════════
    btc_direction = get_btc_direction(btc_data)
    if btc_direction != signal:
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 6: 交易过滤器
    # ═══════════════════════════════════════
    if not all_filters_pass(btc_data, eth_data, account, regime, score):
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 7: 确认仓位
    # ═══════════════════════════════════════
    if score >= 8:
        position_size = TOTAL_CAPITAL * 0.02    # 满仓 2%
    elif score >= 5:
        position_size = TOTAL_CAPITAL * 0.01    # 半仓 1%
    else:
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 8: 用户确认（必须步骤）
    # ═══════════════════════════════════════
    # 生成带完整参数的建议消息
    msg = format_confirmation_message(signal, position_size, score, regime)
    send_to_user(msg)  # 飞书推送

    # 等待用户回复"确认"后才执行
    # 超时（60秒）未回复 → 跳过本次信号
    user_reply = wait_for_confirmation(timeout=60)
    if user_reply != "CONFIRM":
        log(f"用户未确认，跳过信号: {signal}")
        sleep(30)
        continue

    # ═══════════════════════════════════════
    # STEP 9: 操作前完整性锁定
    # ═══════════════════════════════════════
    before_qty = get_exchange_qty("ETH-USDT-SWAP")
    lock = OperationLock("ETH-USDT-SWAP", "OPEN", before_qty, before_qty + position_size)

    # ═══════════════════════════════════════
    # STEP 10: 执行交易
    # ═══════════════════════════════════════
    execute_order(signal, position_size)   # 开仓
    set_stop_loss()                          # 设置止损
    set_take_profit()                        # 设置止盈
    log_order()

    # ═══════════════════════════════════════
    # STEP 11: 操作后完整性校验
    # ═══════════════════════════════════════
    time.sleep(2)  # 等待交易所确认
    if not lock.verify():
        # 数量对不上 → 系统已暂停，等用户确认
        continue

    # ═══════════════════════════════════════
    # STEP 12: 持仓监控（每30秒）
    # ═══════════════════════════════════════
    while has_open_positions():
        # 每次检查前先同步（防止手动干预漏检）
        sync_positions()
        sync_orders()

        check_stop_loss()       # 触及止损 → 市价全出
        check_take_profit()     # 触及止盈 → 分批平仓
        check_timeout()         # 超时 → 强制平仓
        check_trend_reverse()   # 趋势反转 → 平仓

        # 操作后完整性校验
        for lock in active_locks:
            if not lock.verify():
                pause_system()
                notify_user("🚨 系统暂停，持仓完整性校验失败")
                wait_for_user_resume()

        sleep(30)

    # ═══════════════════════════════════════
    # STEP 13: 每日风控检查
    # ═══════════════════════════════════════
    if is_new_day():
        if daily_loss >= TOTAL_CAPITAL * 0.05:
            disable_trading()   # 当日停止交易
        reset_consecutive_losses()
        notify_user(f"📊 每日报告\n总亏损: {daily_loss/U:.2%}\n交易次数: {trade_count}")

    sleep(30)
END WHILE
```

---

## 十五、手动干预处理流程图

```
┌──────────────────────────────────────────────┐
│              每次主循环第一步                  │
│         sync_positions() + sync_orders()       │
└────────────────┬─────────────────────────────┘
                 │
       ┌─────────▼─────────┐
       │  交易所有持仓       │
       │  系统无记录         │ ──→ 用户手动开仓
       │  (live_qty > 0)   │     → 暂停 + 通知用户
       └─────────┬─────────┘     → 「接管」或「忽略」
                 │
       ┌─────────▼─────────┐
       │  系统有持仓        │
       │  交易所=0         │ ──→ 用户手动全平
       │  (status=OPEN)    │     → 同步清除 + 取消挂单
       └─────────┬─────────┘     → 继续运行
                 │
       ┌─────────▼─────────┐
       │  数量不一致        │
       │  (系统≠交易所)    │ ──→ 用户手动部分平
       │                   │     → 同步更新 + 通知
       └─────────┬─────────┘     → 继续运行
                 │
       ┌─────────▼─────────┐
       │  交易所有挂单      │
       │  系统无记录        │ ──→ 用户手动挂单
       │                   │     → 暂停 + 通知用户
       └─────────┬─────────┘     → 「取消」或「接管」
                 │
       ┌─────────▼─────────┐
       │  系统有挂单        │
       │  交易所无          │ ──→ 用户手动撤单
       │                   │     → 同步清除 + 通知
       └───────────────────┘     → 继续运行
```

---

## 十六、完整参数速查表

| 参数 | 数值 |
|------|------|
| 单笔仓位 | 总资金 × 2% |
| 最大总仓位 | 总资金 × 10% |
| 止损幅度 | **1.5%** |
| 保本激活 | 盈利 ≥ +1.0% |
| 止盈① | +0.5%，平当前持仓 × 30% |
| 止盈② | +1.0%，平当前持仓 × 40% |
| 止盈③ | +1.5%，平剩余全部 |
| 高频持仓上限 | **15分钟** |
| 趋势持仓 | 无上限 |
| 区间持仓上限 | 30分钟 |
| 单日最大亏损 | 5% |
| 连续亏损冷却 | ≥3次 → 冷却1小时 |
| 合约面值(ETH) | 0.01 ETH/张 |
| 趋势信号杠杆 | 30x |
| 高频信号杠杆 | 50x |
| 信号评分门槛 | ≥5分执行，≥8分满仓 |
| 手动干预响应 | 暂停/同步/继续运行 |

---

**文档版本：v4.0（终版）**
**新增模块：手动干预防护层（三层防护）**
**审查问题数：14个（全部修复+堵死）**
**下一步：可交付技术团队 / 导入 OpenClaw 执行**
