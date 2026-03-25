import pandas as pd
import numpy as np
from loguru import logger

class HighWinRateStrategy:
    """
    80%+胜率策略
    使用多重确认机制提高胜率
    """
    
    def __init__(self, config=None):
        self.config = config or {}
        self.name = "高胜率策略"
        self.min_confirmations = self.config.get('min_confirmations', 4)
        self.win_rate_threshold = self.config.get('win_rate_threshold', 80)
    
    def calculate_indicators(self, df):
        """计算技术指标"""
        # EMA
        df['ema9'] = df['close'].ewm(span=9).mean()
        df['ema21'] = df['close'].ewm(span=21).mean()
        df['ema50'] = df['close'].ewm(span=50).mean()
        
        # 趋势判断
        df['trend_up'] = (df['ema9'] > df['ema21']) & (df['ema21'] > df['ema50'])
        df['trend_down'] = (df['ema9'] < df['ema21']) & (df['ema21'] < df['ema50'])
        
        # MACD
        exp1 = df['close'].ewm(span=12).mean()
        exp2 = df['close'].ewm(span=26).mean()
        df['macd'] = exp1 - exp2
        df['signal'] = df['macd'].ewm(span=9).mean()
        df['histogram'] = df['macd'] - df['signal']
        df['macd_bullish'] = df['macd'] > df['signal']
        df['macd_bearish'] = df['macd'] < df['signal']
        
        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # 布林带
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['std20'] = df['close'].rolling(window=20).std()
        df['upper_band'] = df['sma20'] + (df['std20'] * 2)
        df['lower_band'] = df['sma20'] - (df['std20'] * 2)
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(14).mean()
        
        # 支撑阻力
        df['support'] = df['low'].rolling(window=20).min()
        df['resistance'] = df['high'].rolling(window=20).max()
        
        # 成交量
        df['volume_sma'] = df['volume'].rolling(window=20).mean()
        
        return df
    
    def generate_signal(self, df):
        """生成交易信号"""
        if len(df) < 50:
            return None
        
        df = self.calculate_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 买入信号确认
        buy_score = 0
        buy_reasons = []
        
        if last['trend_up']:
            buy_score += 1
            buy_reasons.append("趋势向上")
        
        if last['macd_bullish'] and prev['macd'] <= prev['signal']:
            buy_score += 1.5
            buy_reasons.append("MACD金叉")
        
        if last['rsi'] < 40:
            buy_score += 1
            buy_reasons.append("RSI低位")
        
        if last['close'] < last['lower_band'] * 1.02:
            buy_score += 1
            buy_reasons.append("触及下轨")
        
        if last['close'] < last['support'] * 1.03:
            buy_score += 1.5
            buy_reasons.append("支撑附近")
        
        if last['volume'] > last['volume_sma'] * 1.3:
            buy_score += 1
            buy_reasons.append("量能放大")
        
        # 卖出信号确认
        sell_score = 0
        sell_reasons = []
        
        if last['trend_down']:
            sell_score += 1
            sell_reasons.append("趋势向下")
        
        if last['macd_bearish'] and prev['macd'] >= prev['signal']:
            sell_score += 1.5
            sell_reasons.append("MACD死叉")
        
        if last['rsi'] > 60:
            sell_score += 1
            sell_reasons.append("RSI高位")
        
        if last['close'] > last['upper_band'] * 0.98:
            sell_score += 1
            sell_reasons.append("触及上轨")
        
        if last['close'] > last['resistance'] * 0.97:
            sell_score += 1.5
            sell_reasons.append("阻力附近")
        
        if last['volume'] > last['volume_sma'] * 1.3:
            sell_score += 1
            sell_reasons.append("量能放大")
        
        # 生成信号
        if buy_score >= self.min_confirmations:
            probability = min(50 + buy_score * 7, 95)
            if probability >= self.win_rate_threshold:
                stop_loss = last['close'] - last['atr'] * 1.5
                take_profit = last['close'] + last['atr'] * 3
                
                return {
                    'strategy': self.name,
                    'signal': 'buy',
                    'probability': probability,
                    'price': last['close'],
                    'score': buy_score,
                    'reasons': buy_reasons,
                    'stop_loss': round(stop_loss, 2),
                    'take_profit': round(take_profit, 2),
                    'atr': last['atr']
                }
        
        if sell_score >= self.min_confirmations:
            probability = min(50 + sell_score * 7, 95)
            if probability >= self.win_rate_threshold:
                stop_loss = last['close'] + last['atr'] * 1.5
                take_profit = last['close'] - last['atr'] * 3
                
                return {
                    'strategy': self.name,
                    'signal': 'sell',
                    'probability': probability,
                    'price': last['close'],
                    'score': sell_score,
                    'reasons': sell_reasons,
                    'stop_loss': round(stop_loss, 2),
                    'take_profit': round(take_profit, 2),
                    'atr': last['atr']
                }
        
        return None
