#!/usr/bin/env python3
"""
混沌龙虾多Agent量化交易系统 v3.0
Multi-Agent Quantitative Trading System

协作流程:
Agent1(技术分析) → Agent2(信号) → Agent3(决策) → Agent4(风控) → Freqtrade执行
"""

import requests
import numpy as np
import json
import time
from datetime import datetime

# ============ 配置 ============
OKX_API = "https://www.okx.com/api/v5"
API_KEY = "be046210-77bd-47e6-8524-dee2f2acebd9"
SIGNAL_FILE = "/tmp/lobster_signal.json"
LOG_FILE = "/tmp/agent_trading.log"

# ============ Agent 1: 技术分析 ============
class TechnicalAgent:
    """技术分析Agent - 分析多周期指标"""
    
    def __init__(self):
        self.name = "Agent1-技术分析"
    
    def analyze(self, symbol="ETH-USDT-SWAP"):
        """获取并分析多周期数据"""
        # 获取各周期K线
        intervals = ["1m", "5m", "15m", "30m", "1H", "4H"]
        data = {}
        
        for interval in intervals:
            try:
                url = f"{OKX_API}/market/candles?instId={symbol}&bar={interval}&limit=100"
                resp = requests.get(url, timeout=10)
                candles = resp.json()['data']
                data[interval] = self._parse_candles(candles)
            except Exception as e:
                print(f"[{self.name}] 获取{interval}数据失败: {e}")
        
        # 计算指标
        analysis = {}
        for interval, candles in data.items():
            if candles:
                analysis[interval] = self._calculate_indicators(candles)
        
        return analysis
    
    def _parse_candles(self, candles):
        """解析K线数据"""
        parsed = []
        for c in candles[:100]:
            parsed.append({
                'time': c[0],
                'open': float(c[1]),
                'high': float(c[2]),
                'low': float(c[3]),
                'close': float(c[4]),
                'volume': float(c[5])
            })
        return parsed
    
    def _calculate_indicators(self, candles):
        """计算技术指标"""
        closes = np.array([c['close'] for c in candles])
        volumes = np.array([c['volume'] for c in candles])
        highs = np.array([c['high'] for c in candles])
        lows = np.array([c['low'] for c in candles])
        
        # RSI
        deltas = np.diff(closes)
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = np.mean(gains[-14:])
        avg_loss = np.mean(losses[-14:])
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        # EMA
        ema20 = np.mean(closes[-20:])
        ema50 = np.mean(closes[-50:]) if len(closes) >= 50 else ema20
        
        # MACD
        ema12 = np.mean(closes[-12:]) if len(closes) >= 12 else closes[-1]
        ema26 = np.mean(closes[-26:]) if len(closes) >= 26 else closes[-1]
        macd = ema12 - ema26
        
        # 布林带
        ma20 = np.mean(closes[-20:])
        std20 = np.std(closes[-20:])
        boll_upper = ma20 + 2 * std20
        boll_lower = ma20 - 2 * std20
        
        # 成交量均线
        vol_ma = np.mean(volumes[-20:])
        
        # ATR
        trs = []
        for i in range(1, min(len(candles), 15)):
            tr = max(highs[-i] - lows[-i], 
                    abs(highs[-i] - closes[-i-1]),
                    abs(lows[-i] - closes[-i-1]))
            trs.append(tr)
        atr = np.mean(trs) if trs else 0
        
        # 趋势判断
        trend = "UP" if closes[-1] > ema20 else "DOWN"
        
        # 成交量放大
        volume_ratio = volumes[-1] / vol_ma if vol_ma > 0 else 1
        
        return {
            'price': closes[-1],
            'rsi': rsi,
            'ema20': ema20,
            'ema50': ema50,
            'macd': macd,
            'boll_upper': boll_upper,
            'boll_middle': ma20,
            'boll_lower': boll_lower,
            'atr': atr,
            'trend': trend,
            'volume_ratio': volume_ratio,
            'change_pct': (closes[-1] - closes[-2]) / closes[-2] * 100 if len(closes) >= 2 else 0
        }

# ============ Agent 2: 信号Agent ============
class SignalAgent:
    """信号Agent - 生成多空信号"""
    
    def __init__(self):
        self.name = "Agent2-信号"
    
    def generate(self, analysis):
        """基于技术分析生成信号"""
        signals = {}
        
        for interval, data in analysis.items():
            signal = self._single_signal(data)
            signals[interval] = signal
        
        return signals
    
    def _single_signal(self, data):
        """单个周期信号"""
        score = 0
        reasons = []
        
        # RSI
        if data['rsi'] < 30:
            score += 2
            reasons.append(f"RSI超卖({data['rsi']:.1f})")
        elif data['rsi'] > 70:
            score -= 2
            reasons.append(f"RSI超买({data['rsi']:.1f})")
        
        # 趋势
        if data['trend'] == "UP":
            score += 2
            reasons.append("趋势向上")
        else:
            score -= 2
            reasons.append("趋势向下")
        
        # MACD
        if data['macd'] > 0:
            score += 1
            reasons.append("MACD正")
        else:
            score -= 1
            reasons.append("MACD负")
        
        # 布林带
        if data['price'] < data['boll_lower']:
            score += 1
            reasons.append("价格<布林下轨")
        elif data['price'] > data['boll_upper']:
            score -= 1
            reasons.append("价格>布林上轨")
        
        # 成交量
        if data['volume_ratio'] > 1.5:
            score += 1
            reasons.append(f"放量({data['volume_ratio']:.1f}x)")
        
        # 方向判断
        if score >= 3:
            direction = "LONG"
        elif score <= -3:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"
        
        return {
            'direction': direction,
            'score': score,
            'reasons': reasons
        }

# ============ Agent 3: 决策Agent ============
class DecisionAgent:
    """决策Agent - 综合决策"""
    
    def __init__(self):
        self.name = "Agent3-决策"
    
    def decide(self, signals, btc_analysis=None):
        """综合多周期信号做决策"""
        # 统计各周期信号
        long_count = sum(1 for s in signals.values() if s['direction'] == "LONG")
        short_count = sum(1 for s in signals.values() if s['direction'] == "SHORT")
        total = len(signals)
        
        # 权重计算
        weights = {'1m': 0.1, '5m': 0.15, '15m': 0.2, '30m': 0.2, '1H': 0.25, '4H': 0.1}
        
        long_score = 0
        short_score = 0
        for interval, weight in weights.items():
            if interval in signals:
                s = signals[interval]
                if s['direction'] == "LONG":
                    long_score += weight * s['score']
                elif s['direction'] == "SHORT":
                    short_score += weight * abs(s['score'])
        
        # BTC加成
        if btc_analysis:
            if btc_analysis['trend'] == "UP":
                long_score += 0.5
            else:
                short_score += 0.5
        
        # 最终决策
        if long_score > short_score + 1:
            decision = "BUY"
            confidence = min(long_score / 5 * 100, 95)
        elif short_score > long_score + 1:
            decision = "SELL"
            confidence = min(short_score / 5 * 100, 95)
        else:
            decision = "HOLD"
            confidence = 50
        
        return {
            'decision': decision,
            'confidence': confidence,
            'long_score': long_score,
            'short_score': short_score,
            'long_count': long_count,
            'short_count': short_count
        }

# ============ Agent 4: 风控Agent ============
class RiskAgent:
    """风控Agent - 计算仓位和止损"""
    
    def __init__(self):
        self.name = "Agent4-风控"
        self.max_position = 0.1  # 最大仓位10%
        self.stop_loss_pct = 0.03  # 3%止损
    
    def calculate(self, decision, data, account_balance):
        """计算仓位和止损"""
        if decision['decision'] == "HOLD":
            return None
        
        # 计算仓位
        position_size = account_balance * self.max_position  # 5U
        
        # 获取当前价格
        price = data.get('5m', {}).get('price', 0) if isinstance(data, dict) else 0
        
        if price == 0:
            return None
        
        # 计算止损止盈
        if decision['decision'] == "BUY":
            entry_price = price
            stop_loss = price * (1 - self.stop_loss_pct)
            take_profit_1 = price * 1.005  # 0.5%
            take_profit_2 = price * 1.01   # 1%
            take_profit_3 = price * 1.02    # 2%
        else:  # SELL
            entry_price = price
            stop_loss = price * (1 + self.stop_loss_pct)
            take_profit_1 = price * 0.995
            take_profit_2 = price * 0.99
            take_profit_3 = price * 0.98
        
        return {
            'direction': "LONG" if decision['decision'] == "BUY" else "SHORT",
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit_1': take_profit_1,
            'take_profit_2': take_profit_2,
            'take_profit_3': take_profit_3,
            'position_size': position_size,
            'leverage': 100,
            'confidence': decision['confidence']
        }

# ============ 主控Agent ============
class TradingSystem:
    """多Agent交易系统主控"""
    
    def __init__(self):
        self.tech_agent = TechnicalAgent()
        self.signal_agent = SignalAgent()
        self.decision_agent = DecisionAgent()
        self.risk_agent = RiskAgent()
        self.last_signal_time = 0
        self.signal_cooldown = 300  # 5分钟信号间隔
    
    def run_cycle(self):
        """运行一个完整的交易周期"""
        try:
            # ===== Agent 1: 技术分析 =====
            print("=" * 50)
            print(f"[{datetime.now()}] Agent1 技术分析...")
            eth_analysis = self.tech_agent.analyze("ETH-USDT-SWAP")
            btc_analysis = self.tech_agent.analyze("BTC-USDT-SWAP")
            
            # 获取BTC数据用于联动
            btc_data = btc_analysis.get('1H', {})
            
            print(f"  ETH价格: {eth_analysis.get('5m', {}).get('price', 0):.2f}")
            print(f"  BTC趋势: {btc_data.get('trend', 'N/A')}")
            
            # ===== Agent 2: 信号生成 =====
            print(f"[{datetime.now()}] Agent2 信号生成...")
            signals = self.signal_agent.generate(eth_analysis)
            for interval, sig in signals.items():
                print(f"  {interval}: {sig['direction']} (score:{sig['score']})")
            
            # ===== Agent 3: 决策 =====
            print(f"[{datetime.now()}] Agent3 综合决策...")
            decision = self.decision_agent.decide(signals, btc_data)
            print(f"  决策: {decision['decision']} (置信度:{decision['confidence']:.1f}%)")
            print(f"  多头评分: {decision['long_score']:.2f}, 空头评分: {decision['short_score']:.2f}")
            
            # ===== Agent 4: 风控计算 =====
            # 获取账户余额
            account_balance = self._get_balance()
            print(f"[{datetime.now()}] Agent4 风控计算 (余额:{account_balance:.2f}U)...")
            
            trade_plan = self.risk_agent.calculate(decision, eth_analysis, account_balance)
            
            if trade_plan:
                print(f"  交易计划:")
                print(f"    方向: {trade_plan['direction']}")
                print(f"    入场价: {trade_plan['entry_price']:.2f}")
                print(f"    止损: {trade_plan['stop_loss']:.2f}")
                print(f"    止盈1: {trade_plan['take_profit_1']:.2f}")
                print(f"    置信度: {trade_plan['confidence']:.1f}%")
                
                # 保存信号
                self._save_signal(trade_plan)
                
                # 推送飞书
                self._send_feishu(trade_plan, decision)
            else:
                print(f"  观望，不开仓")
                # 清除旧信号
                self._clear_signal()
            
            # 记录日志
            self._log(decision, trade_plan)
            
            return trade_plan
            
        except Exception as e:
            print(f"[ERROR] 交易周期异常: {e}")
            return None
    
    def _get_balance(self):
        """获取账户余额"""
        try:
            import hmac
            import base64
            from datetime import datetime
            
            timestamp = datetime.utcnow().isoformat() + 'Z'
            method = "GET"
            path = "/api/v5/account/balance"
            body = ""
            
            message = timestamp + method + path + body
            signature = base64.b64encode(hmac.new(
                "508989F295B579CA787D85F500B9C02E".encode(),
                message.encode(),
                hmac.new
            ).digest()).decode()
            
            headers = {
                "OK-ACCESS-KEY": API_KEY,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": "Fjh872330@"
            }
            
            resp = requests.get(f"{OKX_API}{path}", headers=headers, timeout=10)
            data = resp.json()
            
            if data.get('code') == 0:
                for item in data['data'][0]['details']:
                    if item['ccy'] == 'USDT':
                        return float(item['eq'])
            return 100  # 默认100U
        except:
            return 100
    
    def _save_signal(self, plan):
        """保存信号到文件"""
        with open(SIGNAL_FILE, 'w') as f:
            json.dump({
                'timestamp': time.time(),
                'plan': plan
            }, f, indent=2)
    
    def _clear_signal(self):
        """清除信号文件"""
        try:
            import os
            if os.path.exists(SIGNAL_FILE):
                os.remove(SIGNAL_FILE)
        except:
            pass
    
    def _send_feishu(self, plan, decision):
        """发送飞书通知"""
        try:
            # 这里调用飞书webhook
            pass
        except:
            pass
    
    def _log(self, decision, plan):
        """记录日志"""
        log_entry = {
            'time': datetime.now().isoformat(),
            'decision': decision,
            'plan': plan
        }
        with open(LOG_FILE, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')


# ============ 主程序 ============
if __name__ == "__main__":
    system = TradingSystem()
    
    print("=" * 60)
    print("🦞 混沌龙虾多Agent量化交易系统 v3.0")
    print("=" * 60)
    
    # 运行一次
    result = system.run_cycle()
    
    print("=" * 60)
    if result:
        print(f"✅ 信号已生成并保存")
    else:
        print(f"⏸️  当前观望，等待信号")
    print("=" * 60)
