"""
震荡偏空交易策略 v1.0
配合 okx_trader.py 自动执行
适用：BTC-USDT-SWAP / ETH-USDT-SWAP
原理：下降趋势中的反弹做空，超跌反弹做多（短线）
"""

# ============ 策略参数 ============
SYMBOL = "BTC-USDT-SWAP"
SYMBOL_ETH = "ETH-USDT-SWAP"
CAPITAL = 1000          # 总USDT
MAX_POSITION = 1        # 最大持仓张数（BTC）
MAX_POSITION_ETH = 3    # 最大持仓张数（ETH）

# ============ 入场信号 ============
# 【做空信号】- 优先
SHORT_CONDITIONS = {
    "price_zone": [72000, 72500],    # 反弹到$72,000-$72,500区间
    "rsi_4h": "> 65",                # 4H RSI > 65（超买）
    "funding_rate": "> 0.02",         # 资金费率 > 0.02%（多头拥挤）
    "ma20_4h": "price < ma20",        # 价格在20均线下方
}

# 【做多信号】- 次要（抢反弹）
LONG_CONDITIONS = {
    "price_zone": [68000, 70000],     # 回踩$68,000-$70,000支撑
    "rsi_1h": "< 30",                 # 1H RSI < 30（超卖）
    "volume": "放量",                  # 成交量放大
}

# ============ 止损止盈 ============
STOP_LOSS_PCT = 0.015   # 止损：1.5%（窄止损）
TAKE_PROFIT_PCT = 0.03  # 止盈：3%（2:1盈亏比）
TRAILING_STOP = True     # 移动止盈

# ============ 风控规则（红线） ============
RULES = """
【风控红线 - 禁止违反】
1. 总仓位不超过 CAPTAL 的 100%（即最多1000 USDT）
2. 单笔亏损不超过本金的 2%
3. 每日最大亏损 5%，触及必须停止交易
4. 不持仓过夜（美盘22:00前必须平仓）
5. 趋势向下时不抄底，只做空
6. 消息面敏感期（美联储讲话/关税）不开新仓
"""

# ============ 信号检测函数 ============
def check_short_signal(ticker, funding_rate):
    """检测做空信号"""
    price = float(ticker['last'])
    high24h = float(ticker['high24h'])
    low24h = float(ticker['low24h'])
    
    signals = []
    
    # 信号1：价格进入做空区间
    if 72000 <= price <= 72500:
        signals.append("✅ 价格进入做空区间 $72,000-$72,500")
    
    # 信号2：价格从24h高点回落
    drop_pct = (high24h - price) / high24h * 100
    if drop_pct >= 2:
        signals.append(f"✅ 从24h高点回落 {drop_pct:.1f}%")
    
    # 信号3：反弹未能突破MA20
    # （需要K线数据，这里用价格位置估算）
    
    # 信号4：资金费率偏高
    if funding_rate > 0.0002:
        signals.append(f"⚠️ 资金费率偏高 {funding_rate*100:.3f}%")
    
    return signals

def check_long_signal(ticker):
    """检测做多信号（抢反弹）"""
    price = float(ticker['last'])
    low24h = float(ticker['low24h'])
    
    signals = []
    
    # 信号1：价格接近24h低点
    if price <= low24h * 1.02:
        signals.append(f"✅ 价格接近24h低点 ${low24h}")
    
    # 信号2：超跌反弹（从低点反弹 > 1%）
    bounce = (price - low24h) / low24h * 100
    if bounce >= 1:
        signals.append(f"✅ 已有反弹 {bounce:.1f}%")
    
    return signals

def calc_position_size(price, stop_loss_pct=STOP_LOSS_PCT):
    """计算仓位"""
    risk_amount = CAPITAL * stop_loss_pct
    stop_distance = price * stop_loss_pct
    contracts = max(1, int(risk_amount / stop_distance))
    return min(contracts, MAX_POSITION)

def calc_stop_loss(entry_price, side, pct=STOP_LOSS_PCT):
    """计算止损价"""
    if side == "short":
        return entry_price * (1 + pct)
    else:
        return entry_price * (1 - pct)

def calc_take_profit(entry_price, side, pct=TAKE_PROFIT_PCT):
    """计算止盈价"""
    if side == "short":
        return entry_price * (1 - pct)
    else:
        return entry_price * (1 + pct)

# ============ 交易信号输出 ============
def generate_signal(side, price, stop_loss, take_profit, contracts, reason):
    """生成交易信号"""
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【交易信号】{side.upper()}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
品种：{SYMBOL}
入场价：{price}
止损价：{stop_loss} ({STOP_LOSS_PCT*100}%)
止盈价：{take_profit} ({TAKE_PROFIT_PCT*100}%)
张数：{contracts} 张
逻辑：{reason}

【自动执行命令】
python3 okx_trader.py exec --side {side} --price {price} --sl {stop_loss} --tp {take_profit} --size {contracts}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# ============ 演示：模拟信号 ============
if __name__ == "__main__":
    print("=" * 50)
    print("震荡偏空策略 v1.0 - 信号检测")
    print("=" * 50)
    
    # 模拟当前行情
    ticker_btc = {
        "last": "71453.5",
        "high24h": "72773.9",
        "low24h": "67680.2"
    }
    ticker_eth = {
        "last": "2235.01",
        "high24h": "2273.53",
        "low24h": "2057"
    }
    funding_rate_btc = 0.0001
    funding_rate_eth = 0.000078
    
    print("\n【BTC分析】")
    short_signals = check_short_signal(ticker_btc, funding_rate_btc)
    long_signals = check_long_signal(ticker_btc)
    
    for s in short_signals:
        print(s)
    for s in long_signals:
        print(s)
    
    if not short_signals and not long_signals:
        print("⏸ 无明确信号，等待...")
    
    # 当前推荐
    print("\n" + "=" * 50)
    print("【当前操作建议】")
    print("=" * 50)
    price = 71453.5
    
    if 72000 <= price <= 72500:
        contracts = calc_position_size(price)
        sl = calc_stop_loss(price, "short")
        tp = calc_take_profit(price, "short")
        print(generate_signal("short", price, sl, tp, contracts, "反弹至$72,000-$72,500区间，均线压力"))
    elif price < 70000:
        contracts = calc_position_size(price)
        sl = calc_stop_loss(price, "long")
        tp = calc_take_profit(price, "long")
        print(generate_signal("long", price, sl, tp, contracts, "回踩$68,000-$70,000支撑，超卖反弹"))
    else:
        print("⏸ 价格在$70,000-$72,000之间，等待明确信号")
    
    print("\n" + RULES)
