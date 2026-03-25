#!/usr/bin/env python3
"""
混沌龙虾 - 基础交易工具包
Boss的数字分身 - 高级策略交易员
"""

import json
import time
import hmac
import hashlib
import requests
from datetime import datetime
from typing import Dict, List, Optional

class TradingBase:
    """交易基础类 - 提供通用功能"""
    
    def __init__(self):
        self.config = self._load_config()
        
    def _load_config(self):
        """加载配置"""
        try:
            with open('/root/.openclaw/workspace/skills/crypto-trading/config/trading_config.json', 'r') as f:
                return json.load(f)
        except:
            return {}
    
    def generate_signature(self, params: dict, secret: str) -> str:
        """生成API签名"""
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        return hmac.new(secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    
    def get_timestamp(self) -> int:
        """获取毫秒时间戳"""
        return int(time.time() * 1000)
    
    def calculate_position_size(self, capital: float, risk_percent: float, entry: float, stop_loss: float) -> float:
        """
        计算仓位大小（基于风险）
        
        Args:
            capital: 总资金
            risk_percent: 每单风险百分比（如0.01=1%）
            entry: 入场价格
            stop_loss: 止损价格
            
        Returns:
            仓位大小（币种数量）
        """
        risk_amount = capital * risk_percent
        price_diff = abs(entry - stop_loss)
        position_size = risk_amount / price_diff if price_diff > 0 else 0
        return position_size
    
    def log_trade(self, trade_data: dict):
        """记录交易日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {"timestamp": timestamp, **trade_data}
        
        with open('/root/.openclaw/workspace/skills/crypto-trading/logs/trades.jsonl', 'a') as f:
            f.write(json.dumps(log_entry) + '\n')
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """计算RSI指标"""
        if len(prices) < period + 1:
            return None
            
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            return 100
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

if __name__ == "__main__":
    print("混沌龙虾交易基础工具包已加载 🦞")
    print("可用功能：仓位计算、RSI指标、签名生成、交易日志")