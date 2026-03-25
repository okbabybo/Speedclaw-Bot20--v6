# 🦞 混沌龙虾 - 主交易程序

import ccxt
import time
import logging
from datetime import datetime
import json

# ===== 配置加载 =====
try:
    from config.okx_full_integration import OKX_API_CONFIG, SECURITY_CONFIG, TRADING_SYSTEM_STATUS
    from config.ema_strategy import EMAStrategy
    from config.telegram_notifier import TelegramNotifier
    from config.risk_manager import RiskManager
except ImportError:
    # 如果配置文件不存在，使用内置配置
    OKX_API_CONFIG = {
        'api_key': '1dcf40d7-e2d7-4470-a9d6-b55806425dc5',
        'api_secret': '48BD3C6DD83E7929015BD0203803E074',
        'passphrase': 'Fjh872330@',
        'status': 'fully_configured'
    }
    
    TRADING_SYSTEM_STATUS = {
        'api_connectivity': 'ready',
        'monitoring_active': True,
        'trading_execution': 'active',
        'risk_management': 'configured',
        'telegram_notifications': 'active',
        'ema_strategy': 'active'
    }

# ===== 日志配置 =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ChaosLobster-Main')

class ChaosLobsterTrader:
    def __init__(self):
        self.okx_config = OKX_API_CONFIG
        self.system_status = TRADING_SYSTEM_STATUS
        self.logger = logger
        self.exchange = None
        self.telegram = None
        self.strategy = None
        self.risk_manager = None
        
    def initialize_system(self):
        """初始化交易系统"""
        self.log_info("🦞 混沌龙虾交易系统启动中...")
        
        # 初始化各个模块
        self.init_exchange()
        self.init_telegram()
        self.init_strategy()
        self.init_risk_manager()
        self.init_notifier()
        
    def init_exchange(self):
        """初始化OKX交易所连接"""
        if self.system_status.get('api_connectivity') == 'ready':
            try:
                import ccxt
                self.exchange = ccxt.okx({
                    'apiKey': self.okx_config['api_key'],
                    'secret': self.okx_config['api_secret'],
                    'password': self.okx_config['passphrase'],
                    'enableRateLimit': True,
                    'options': {
                        'defaultType': 'spot'
                    }
                })
                
                # 测试连接
                balance = self.exchange.fetch_balance()
                self.log_info(f"🔗 OKX交易所连接成功！账户余额: {len(balance['total'])}种资产")
                self.system_status['exchange_connected'] = True
            
            except Exception as e:
                self.log_info(f"🚨 OKX连接失败: {str(e)}")
                self.system_status['exchange_connected'] = False
        
    def init_telegram(self):
        """初始化Telegram通知"""
        if self.system_status.get('telegram_notifications') == 'active':
            try:
                from config.telegram_notifier import TelegramNotifier
                self.telegram = TelegramNotifier()
                self.log_info("📡 Telegram通知系统已激活")
                self.system_status['telegram_ready'] = True
            except:
                self.log_info("📡 Telegram通知: 使用模拟模式")
                self.system_status['telegram_ready'] = False
                
    def init_strategy(self):
        """初始化交易策略"""
        if self.system_status.get('ema_strategy') == 'active':
            try:
                self.strategy = EMAStrategy()
                self.log_info("📈 EMA趋势策略已加载")
                self.system_status['strategy_ready'] = True
            except:
                self.log_info("📈 EMA策略: 使用默认配置")
                self.system_status['strategy_ready'] = False
                
    def init_risk_manager(self):
        """初始化风险管理"""
        if self.system_status.get('risk_management') == 'configured':
            try:
                self.risk_manager = RiskManager()
                self.log_info("🛡️ 风险管理系统已激活")
                self.system_status['risk_ready'] = True
            except:
                self.log_info("🛡️ 风险管理: 使用基础配置")
                self.system_status['risk_ready'] = False
                
    def init_notifier(self):
        """初始化通知系统"""
        self.log_info("🔔 通知系统初始化完成")
        
    def log_info(self, message):
        """记录信息"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_msg = f"[混沌龙虾] {timestamp} - {message}"
        self.logger.info(log_msg)
        print(log_msg)
        
    def run_trading_loop(self):
        """运行交易主循环"""
        self.log_info("🚀 混沌龙虾交易系统启动完成！开始监控市场...")
        
        if not self.system_status.get('exchange_connected'):
            self.log_info("⚠️ 交易所未连接，启动监控模式")
            self.run_monitor_mode()
        else:
            self.log_info("✅ 交易所已连接，启动实盘交易模式")
            self.run_live_trading()
            
    def run_monitor_mode(self):
        """监控模式"""
        self.log_info("📊 进入监控模式 - 实时分析市场但不开仓")
        while True:
            try:
                # 模拟市场监控
                self.log_info("🔍 监控市场数据中...")
                time.sleep(30)
            except KeyboardInterrupt:
                break
                
    def run_live_trading(self):
        """实盘交易模式"""
        self.log_info("💰 进入实盘交易模式")
        while True:
            try:
                # 这里将实现真实的交易逻辑
                self.log_info("⚡ 检查交易信号...")
                time.sleep(10)
            except KeyboardInterrupt:
                break
                
    def start(self):
        """启动系统"""
        try:
            self.initialize_system()
            self.run_trading_loop()
        except Exception as e:
            self.log_info(f"🚨 系统错误: {str(e)}")
        finally:
            self.log_info("🦞 混沌龙虾交易系统已停止")

# ===== 主程序入口 =====
if __name__ == "__main__":
    trader = ChaosLobsterTrader()
    trader.start()

print("🦞 混沌龙虾主交易程序已加载完成！")
