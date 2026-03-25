import json
import ccxt
import pandas as pd
import numpy as np
from loguru import logger
from datetime import datetime

class OKXClient:
    """OKX API客户端"""
    
    def __init__(self, config_path='config/okx_config.json'):
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        self.exchange = ccxt.okx({
            'apiKey': self.config['apiKey'],
            'secret': self.config['secret'],
            'password': self.config['passphrase'],
            'enableRateLimit': True,
            'options': self.config.get('options', {})
        })
        
        logger.info("✅ OKX客户端初始化成功")
    
    def get_balance(self):
        """获取账户余额"""
        try:
            balance = self.exchange.fetch_balance()
            usdt = balance.get('USDT', {})
            return {
                'free': usdt.get('free', 0),
                'used': usdt.get('used', 0),
                'total': usdt.get('total', 0)
            }
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return None
    
    def get_positions(self, symbol=None):
        """获取持仓信息"""
        try:
            positions = self.exchange.fetch_positions([symbol] if symbol else None)
            active_positions = []
            for pos in positions:
                if pos.get('contracts', 0) != 0:
                    active_positions.append({
                        'symbol': pos['symbol'],
                        'side': pos['side'],
                        'contracts': pos['contracts'],
                        'entryPrice': pos.get('entryPrice', 0),
                        'unrealizedPnl': pos.get('unrealizedPnl', 0),
                        'liquidationPrice': pos.get('liquidationPrice', 0)
                    })
            return active_positions
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    def fetch_ohlcv(self, symbol, timeframe='5m', limit=100):
        """获取K线数据"""
        try:
            return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as e:
            logger.error(f"获取K线失败: {e}")
            return None
    
    def create_order(self, symbol, side, amount, order_type='market', 
                     stop_loss=None, take_profit=None):
        """创建订单"""
        try:
            # 创建市价单
            order = self.exchange.create_market_order(symbol, side, amount)
            logger.info(f"✅ 订单创建成功: {order['id']}")
            
            # 设置止损
            if stop_loss:
                try:
                    sl_side = 'sell' if side == 'buy' else 'buy'
                    self.exchange.create_order(
                        symbol, 'STOP_MARKET', sl_side, amount, None,
                        {'stopPrice': stop_loss, 'reduceOnly': True}
                    )
                    logger.info(f"🛡️ 止损设置: {stop_loss}")
                except Exception as e:
                    logger.warning(f"止损设置失败: {e}")
            
            # 设置止盈
            if take_profit:
                try:
                    tp_side = 'sell' if side == 'buy' else 'buy'
                    self.exchange.create_order(
                        symbol, 'TAKE_PROFIT_MARKET', tp_side, amount, None,
                        {'stopPrice': take_profit, 'reduceOnly': True}
                    )
                    logger.info(f"💰 止盈设置: {take_profit}")
                except Exception as e:
                    logger.warning(f"止盈设置失败: {e}")
            
            return order
            
        except Exception as e:
            logger.error(f"创建订单失败: {e}")
            return None
    
    def close_position(self, symbol):
        """平仓"""
        try:
            positions = self.get_positions(symbol)
            for pos in positions:
                if pos['symbol'] == symbol:
                    side = 'sell' if pos['side'] == 'long' else 'buy'
                    amount = abs(pos['contracts'])
                    order = self.exchange.create_market_order(
                        symbol, side, amount, {'reduceOnly': True}
                    )
                    logger.info(f"✅ 平仓成功: {symbol}")
                    return order
        except Exception as e:
            logger.error(f"平仓失败: {e}")
            return None
    
    def get_market_price(self, symbol):
        """获取市场价格"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get('last', 0)
        except Exception as e:
            logger.error(f"获取价格失败: {e}")
            return 0
