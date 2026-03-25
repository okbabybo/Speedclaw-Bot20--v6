#!/usr/bin/env python3
"""
混沌龙虾 - EMA趋势跟踪策略
Boss的双均线交易系统
支持BTC/ETH，4小时级别
"""

import sys
sys.path.append('/root/.openclaw/workspace/skills/crypto-trading/scripts')
from trading_base import TradingBase
from typing import Dict, List, Optional, Tuple
import time

class EMATrendStrategy(TradingBase):
    """
    EMA双均线趋势策略
    - 快线：12 EMA
    - 慢线：26 EMA
    - 过滤：200 EMA
    - 确认：价格突破和成交量
    """
    
    def __init__(self, symbol: str = "BTCUSDT", timeframe: str = "4h"):
        super().__init__()
        self.symbol = symbol
        self.timeframe = timeframe
        self.fast_period = 12
        self.slow_period = 26
        self.filter_period = 200
        self.risk_percent = 0.02  # Boss偏好2%
        
    def calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """计算EMA"""
        if len(prices) < period:
            return None
            
        # 初始SMA
        sma = sum(prices[:period]) / period
        multiplier = 2 / (period + 1)
        
        # 计算EMA
        ema = sma
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
            
        return round(ema, 2)
    
    def calculate_macd(self, prices: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """计算MACD - 用于信号确认"""
        if len(prices) < 26:
            return None, None, None
            
        ema12 = self.calculate_ema(prices, 12)
        ema26 = self.calculate_ema(prices, 26)
        
        if ema12 is None or ema26 is None:
            return None, None, None
            
        macd_line = ema12 - ema26
        signal_line = self.calculate_ema([macd_line] * 9, 9)  # 简化计算
        histogram = macd_line - signal_line if signal_line else None
        
        return macd_line, signal_line, histogram
    
    def generate_signal(self, prices: List[float], volumes: List[float]) -> Dict:
        """
        生成交易信号
        
        Args:
            prices: 收盘价列表，按时间顺序 [最早 -> 最新]
            volumes: 成交量列表
            
        Returns:
            信号字典
        """
        if len(prices) < self.filter_period:
            return {
                "symbol": self.symbol,
                "signal": "NEUTRAL",
                "reason": "数据不足",
                "fast_ema": None,
                "slow_ema": None,
                "filter_ema": None
            }
        
        # 计算EMA
        fast_ema = self.calculate_ema(prices, self.fast_period)
        slow_ema = self.calculate_ema(prices, self.slow_period)
        filter_ema = self.calculate_ema(prices, self.filter_period)
        
        # 计算MACD确认
        macd_line, signal_line, histogram = self.calculate_macd(prices)
        
        # 当前价格
        current_price = prices[-1]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else current_volume
        
        # 信号生成逻辑
        signal = "NEUTRAL"
        reason = "无信号"
        
        if fast_ema and slow_ema and filter_ema:
            # 过滤条件：价格在200 EMA上方/下方
            above_filter = current_price > filter_ema
            
            # 金叉条件（做多）
            if fast_ema > slow_ema and prices[-2] <= self.calculate_ema(prices[:-1], self.slow_period):
                if above_filter:
                    # 成交量确认
                    if current_volume > avg_volume * 1.2:
                        signal = "LONG"
                        reason = f"EMA金叉 + 量能确认 + 价格在200EMA上方 | Fast: {fast_ema}, Slow: {slow_ema}"
                    else:
                        signal = "LONG_WEAK"
                        reason = f"EMA金叉但量能不足 | Fast: {fast_ema}, Slow: {slow_ema}"
            
            # 死叉条件（做空）
            elif fast_ema < slow_ema and prices[-2] >= self.calculate_ema(prices[:-1], self.slow_period):
                if not above_filter:
                    if current_volume > avg_volume * 1.2:
                        signal = "SHORT"
                        reason = f"EMA死叉 + 量能确认 + 价格在200EMA下方 | Fast: {fast_ema}, Slow: {slow_ema}"
                    else:
                        signal = "SHORT_WEAK"
                        reason = f"EMA死叉但量能不足 | Fast: {fast_ema}, Slow: {slow_ema}"
            
            # 持有判断
            else:
                if fast_ema > slow_ema and above_filter:
                    signal = "HOLD_LONG"
                    reason = "多头趋势中"
                elif fast_ema < slow_ema and not above_filter:
                    signal = "HOLD_SHORT"
                    reason = "空头趋势中"
                else:
                    signal = "NEUTRAL"
                    reason = "趋势方向不明确"
        
        return {
            "symbol": self.symbol,
            "signal": signal,
            "reason": reason,
            "fast_ema": fast_ema,
            "slow_ema": slow_ema,
            "filter_ema": filter_ema,
            "macd_line": macd_line,
            "signal_line": signal_line,
            "histogram": histogram,
            "current_price": current_price,
            "volume_ratio": current_volume / avg_volume if avg_volume > 0 else 1,
            "timestamp": int(time.time() * 1000)
        }
    
    def calculate_position(self, current_price: float, stop_loss: float, account_balance: float = 10000) -> Dict:
        """基于Boss的风险偏好计算仓位"""
        if current_price <= 0:
            return {"error": "价格无效"}
        
        risk_amount = account_balance * self.risk_percent
        price_diff = abs(current_price - stop_loss)
        
        if price_diff == 0:
            return {"error": "入场价与止损价相同"}
        
        # 计算币种数量
        position_size = risk_amount / price_diff
        position_value = position_size * current_price
        
        return {
            "risk_amount": risk_amount,
            "position_size": round(position_size, 8),
            "position_value": round(position_value, 2),
            "risk_percent": self.risk_percent * 100,
            "stop_loss": stop_loss,
            "account_balance_used": position_value / account_balance
        }
    
    def backtest_simple(self, prices: List[float], volumes: List[float]) -> Dict:
        """简单回测功能"""
        signals = []
        returns = []
        
        # 模拟逐日交易
        for i in range(self.filter_period, len(prices)):
            signal = self.generate_signal(prices[:i+1], volumes[:i+1])
            signals.append(signal)
            
            # 简单的收益计算（基于信号）
            if i > self.filter_period + 1:
                prev_signal = signals[-2]["signal"]
                
                if prev_signal == "LONG":
                    returns.append((prices[i] - prices[i-1]) / prices[i-1])
                elif prev_signal == "SHORT":
                    returns.append(-(prices[i] - prices[i-1]) / prices[i-1])
        
        if not returns:
            return {"error": "数据不足"}
        
        # 计算回测指标
        total_return = sum(returns)
        win_rate = len([r for r in returns if r > 0]) / len(returns)
        avg_win = sum([r for r in returns if r > 0]) / len([r for r in returns if r > 0]) if any(r > 0 for r in returns) else 0
        avg_loss = sum([abs(r) for r in returns if r < 0]) / len([r for r in returns if r < 0]) if any(r < 0 for r in returns) else 0
        
        return {
            "total_trades": len(returns),
            "win_rate": round(win_rate * 100, 2),
            "total_return": round(total_return * 100, 2),
            "avg_win": round(avg_win * 100, 2),
            "avg_loss": round(avg_loss * 100, 2),
            "profit_factor": avg_win / avg_loss if avg_loss > 0 else float("inf")
        }

# 策略测试
if __name__ == "__main__":
    print("="*60)
    print("🦞 混沌龙虾 - EMA趋势策略测试")
    print("="*60)
    
    # 创建策略实例
    strategy = EMATrendStrategy(symbol="BTCUSDT", timeframe="4h")
    
    # 模拟K线数据（实际交易中从API获取）
    # 格式：BTC价格从40000上涨到45000的趋势
    prices = [
        40000, 40200, 39800, 40500, 40800, 41000, 41500, 41800,
        42000, 42200, 42500, 42800, 43000, 43200, 43500, 43800,
        44000, 44200, 44500, 44800, 45000, 45200, 45500, 45800
    ] * 10  # 重复生成足够的数据
    
    volumes = [1000] * len(prices)  # 模拟成交量
    
    # 生成信号
    signal = strategy.generate_signal(prices, volumes)
    
    print(f"\n交易对: {signal['symbol']}")
    print(f"当前价格: ${signal['current_price']:.2f}")
    print(f"交易信号: {signal['signal']}")
    print(f"信号说明: {signal['reason']}")
    print(f"\n技术指标:")
    print(f"  Fast EMA ({strategy.fast_period}): {signal['fast_ema']}")
    print(f"  Slow EMA ({strategy.slow_period}): {signal['slow_ema']}")
    print(f"  Filter EMA ({strategy.filter_period}): {signal['filter_ema']}")
    print(f"  MACD: {signal['macd_line']}")
    print(f"  Volume Ratio: {signal['volume_ratio']:.2f}x")
    
    # 计算示例仓位
    if signal['signal'] in ['LONG', 'SHORT']:
        stop_loss = prices[-1] * 0.95 if signal['signal'] == 'LONG' else prices[-1] * 1.05
        position = strategy.calculate_position(prices[-1], stop_loss, account_balance=10000)
        
        print(f"\n仓位详情（2%风险）:")
        print(f"  风险金额: ${position['risk_amount']:.2f}")
        print(f"  仓位大小: {position['position_size']} BTC")
        print(f"  仓位价值: ${position['position_value']:.2f}")
        print(f"  止损位置: ${position['stop_loss']:.2f}")
    
    # 回测
    print(f"\n" + "="*60)
    print("📊 简易回测结果（模拟数据）")
    print("="*60)
    backtest = strategy.backtest_simple(prices, volumes)
    if "error" not in backtest:
        print(f"总交易次数: {backtest['total_trades']}")
        print(f"胜率: {backtest['win_rate']}%")
        print(f"总收益率: {backtest['total_return']}%")
        print(f"平均盈利: {backtest['avg_win']}%")
        print(f"平均亏损: {backtest['avg_loss']}%")
        print(f"盈亏比: {backtest['profit_factor']:.2f}")
    
    print(f"\n✅ EMA趋势策略已准备就绪！")