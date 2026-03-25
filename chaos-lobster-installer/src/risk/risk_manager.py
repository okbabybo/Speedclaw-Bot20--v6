import json
from datetime import datetime, timedelta
from loguru import logger

class RiskManager:
    """风险管理器"""
    
    def __init__(self, config_path='config/strategy_config.json'):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        self.risk_config = config.get('risk_management', {})
        self.max_position = self.risk_config.get('max_position_size', 0.05)
        self.max_daily_loss = self.risk_config.get('max_daily_loss', 0.03)
        self.stop_loss_pct = self.risk_config.get('stop_loss_pct', 0.02)
        self.take_profit_pct = self.risk_config.get('take_profit_pct', 0.04)
        
        self.daily_pnl = 0
        self.daily_trades = 0
        self.peak_balance = 0
        self.last_reset = datetime.now()
        self.emergency_stop = False
        
        logger.info("✅ 风险管理器初始化成功")
    
    def check_daily_reset(self):
        """检查每日重置"""
        if datetime.now() - self.last_reset > timedelta(days=1):
            self.daily_pnl = 0
            self.daily_trades = 0
            self.last_reset = datetime.now()
            logger.info("📅 日统计已重置")
    
    def can_trade(self, balance, position_size, unrealized_pnl=0):
        """检查是否可以交易"""
        self.check_daily_reset()
        
        if self.emergency_stop:
            logger.warning("🚨 紧急停止已激活")
            return False
        
        # 检查日亏损
        total_pnl = self.daily_pnl + unrealized_pnl
        if total_pnl < -self.max_daily_loss * balance:
            logger.warning(f"🛑 日亏损超限: {total_pnl:.2%}")
            self.emergency_stop = True
            return False
        
        # 检查仓位
        if position_size > self.max_position * balance:
            logger.warning(f"⚠️ 仓位过大: {position_size:.2%}")
            return False
        
        return True
    
    def calculate_position_size(self, balance, risk_per_trade, entry_price, stop_loss):
        """计算仓位大小"""
        risk_amount = balance * risk_per_trade
        price_diff = abs(entry_price - stop_loss)
        
        if price_diff == 0:
            return 0
        
        position_size = risk_amount / price_diff
        max_position = balance * self.max_position / entry_price
        
        return min(position_size, max_position)
    
    def update_trade_result(self, pnl):
        """更新交易结果"""
        self.daily_pnl += pnl
        self.daily_trades += 1
    
    def activate_emergency_stop(self):
        """激活紧急停止"""
        self.emergency_stop = True
        logger.error("🚨🚨🚨 紧急停止已激活！")
    
    def reset_emergency(self):
        """重置紧急停止"""
        self.emergency_stop = False
        self.daily_pnl = 0
        logger.info("✅ 紧急停止已重置")
    
    def get_status(self):
        """获取风控状态"""
        return {
            'daily_pnl': self.daily_pnl,
            'daily_trades': self.daily_trades,
            'emergency_stop': self.emergency_stop,
            'max_position': self.max_position,
            'max_daily_loss': self.max_daily_loss
        }
