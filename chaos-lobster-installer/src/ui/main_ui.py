import sys
import json
import time
import schedule
import pandas as pd
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QTextEdit, 
                             QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
                             QGroupBox, QGridLayout, QTabWidget, QMessageBox,
                             QSystemTrayIcon, QMenu, QAction)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QIcon, QFont
from loguru import logger

# 导入自定义模块
sys.path.append('C:\\chaos-lobster-trading')
from utils.okx_client import OKXClient
from risk.risk_manager import RiskManager
from strategies.high_win_rate_strategy import HighWinRateStrategy

class TradingThread(QThread):
    """交易线程"""
    signal_log = pyqtSignal(str)
    signal_price = pyqtSignal(dict)
    signal_position = pyqtSignal(list)
    signal_balance = pyqtSignal(float)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = False
        self.okx = None
        self.risk_manager = None
        self.strategy = None
        
    def init_components(self):
        """初始化组件"""
        try:
            self.okx = OKXClient()
            self.risk_manager = RiskManager()
            self.strategy = HighWinRateStrategy()
            self.signal_log.emit("✅ 组件初始化成功")
            return True
        except Exception as e:
            self.signal_log.emit(f"❌ 初始化失败: {e}")
            return False
    
    def run(self):
        """运行交易循环"""
        self.running = True
        self.signal_log.emit("🚀 交易机器人启动...")
        
        # 初始化
        if not self.init_components():
            self.running = False
            return
        
        # 定时任务
        schedule.every(5).minutes.do(self.trading_loop)
        schedule.every(10).seconds.do(self.update_data)
        
        # 立即执行一次
        self.update_data()
        
        while self.running:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                self.signal_log.emit(f"❌ 错误: {e}")
                time.sleep(10)
        
        self.signal_log.emit("🛑 机器人已停止")
    
    def update_data(self):
        """更新数据"""
        try:
            # 更新余额
            balance = self.okx.get_balance()
            if balance:
                self.signal_balance.emit(balance['total'])
            
            # 更新持仓
            positions = self.okx.get_positions()
            self.signal_position.emit(positions)
            
            # 更新价格
            btc_price = self.okx.get_market_price('BTC/USDT:USDT')
            eth_price = self.okx.get_market_price('ETH/USDT:USDT')
            self.signal_price.emit({'BTC': btc_price, 'ETH': eth_price})
            
        except Exception as e:
            pass  # 静默处理
    
    def trading_loop(self):
        """交易循环"""
        try:
            symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT']
            
            for symbol in symbols:
                # 获取数据
                ohlcv = self.okx.fetch_ohlcv(symbol, '5m', 100)
                if not ohlcv:
                    continue
                
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                # 生成信号
                signal = self.strategy.generate_signal(df)
                
                if signal and signal['probability'] >= 80:
                    self.signal_log.emit(f"🎯 {symbol} 发现信号!")
                    self.signal_log.emit(f"   方向: {signal['signal'].upper()}")
                    self.signal_log.emit(f"   概率: {signal['probability']}%")
                    self.signal_log.emit(f"   价格: {signal['price']}")
                    
                    # 风控检查
                    balance = self.okx.get_balance()
                    if balance and self.risk_manager.can_trade(balance['total'], 0):
                        # 计算仓位
                        position_size = self.risk_manager.calculate_position_size(
                            balance['total'], 0.02, signal['price'], signal['stop_loss']
                        )
                        
                        self.signal_log.emit(f"📊 建议仓位: {position_size:.4f}")
                        self.signal_log.emit(f"🛡️ 止损: {signal['stop_loss']}")
                        self.signal_log.emit(f"💰 止盈: {signal['take_profit']}")
                        
        except Exception as e:
            self.signal_log.emit(f"❌ 交易循环错误: {e}")
    
    def stop(self):
        """停止线程"""
        self.running = False

class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🦞 混沌龙虾自动交易机器人 v2.0")
        self.setGeometry(100, 100, 1200, 800)
        
        self.trading_thread = None
        self.init_ui()
        self.init_tray()
    
    def init_ui(self):
        """初始化UI"""
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QHBoxLayout(central_widget)
        
        # 左侧控制面板
        left_panel = self.create_left_panel()
        main_layout.addWidget(left_panel, 1)
        
        # 右侧日志面板
        right_panel = self.create_right_panel()
        main_layout.addWidget(right_panel, 2)
    
    def create_left_panel(self):
        """创建左侧面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # 账户信息组
        account_group = QGroupBox("💰 账户信息")
        account_layout = QGridLayout()
        
        self.lbl_balance = QLabel("USDT余额: --")
        self.lbl_balance.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        account_layout.addWidget(self.lbl_balance, 0, 0)
        
        self.lbl_btc_price = QLabel("BTC: --")
        account_layout.addWidget(self.lbl_btc_price, 1, 0)
        
        self.lbl_eth_price = QLabel("ETH: --")
        account_layout.addWidget(self.lbl_eth_price, 2, 0)
        
        account_group.setLayout(account_layout)
        layout.addWidget(account_group)
        
        # 控制按钮组
        control_group = QGroupBox("🎮 控制面板")
        control_layout = QVBoxLayout()
        
        self.btn_start = QPushButton("🚀 启动机器人")
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-size: 16px; padding: 10px;")
        self.btn_start.clicked.connect(self.start_trading)
        control_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("🛑 停止机器人")
        self.btn_stop.setStyleSheet("background-color: #f44336; color: white; font-size: 16px; padding: 10px;")
        self.btn_stop.clicked.connect(self.stop_trading)
        self.btn_stop.setEnabled(False)
        control_layout.addWidget(self.btn_stop)
        
        self.btn_test = QPushButton("🧪 测试连接")
        self.btn_test.clicked.connect(self.test_connection)
        control_layout.addWidget(self.btn_test)
        
        control_group.setLayout(control_layout)
        layout.addWidget(control_group)
        
        # 持仓信息组
        position_group = QGroupBox("📊 持仓信息")
        position_layout = QVBoxLayout()
        
        self.txt_positions = QTextEdit()
        self.txt_positions.setReadOnly(True)
        self.txt_positions.setMaximumHeight(150)
        position_layout.addWidget(self.txt_positions)
        
        position_group.setLayout(position_layout)
        layout.addWidget(position_group)
        
        # 风控状态组
        risk_group = QGroupBox("🛡️ 风控状态")
        risk_layout = QGridLayout()
        
        self.lbl_daily_pnl = QLabel("日盈亏: --")
        risk_layout.addWidget(self.lbl_daily_pnl, 0, 0)
        
        self.lbl_trade_count = QLabel("今日交易: 0")
        risk_layout.addWidget(self.lbl_trade_count, 1, 0)
        
        self.lbl_status = QLabel("状态: 待机")
        self.lbl_status.setStyleSheet("color: orange;")
        risk_layout.addWidget(self.lbl_status, 2, 0)
        
        risk_group.setLayout(risk_layout)
        layout.addWidget(risk_group)
        
        return panel
    
    def create_right_panel(self):
        """创建右侧面板"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # 日志显示
        log_group = QGroupBox("📝 运行日志")
        log_layout = QVBoxLayout()
        
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas;")
        log_layout.addWidget(self.txt_log)
        
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
        
        return panel
    
    def init_tray(self):
        """初始化系统托盘"""
        self.tray_icon = QSystemTrayIcon(self)
        # self.tray_icon.setIcon(QIcon("ui/lobster.ico"))
        
        tray_menu = QMenu()
        show_action = QAction("显示", self)
        show_action.triggered.connect(self.show)
        
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
    
    def start_trading(self):
        """启动交易"""
        self.trading_thread = TradingThread()
        self.trading_thread.signal_log.connect(self.append_log)
        self.trading_thread.signal_price.connect(self.update_price)
        self.trading_thread.signal_position.connect(self.update_positions)
        self.trading_thread.signal_balance.connect(self.update_balance)
        
        self.trading_thread.start()
        
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_status.setText("状态: 🟢 运行中")
        self.lbl_status.setStyleSheet("color: green;")
    
    def stop_trading(self):
        """停止交易"""
        if self.trading_thread:
            self.trading_thread.stop()
            self.trading_thread.wait()
        
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("状态: 🔴 已停止")
        self.lbl_status.setStyleSheet("color: red;")
    
    def test_connection(self):
        """测试连接"""
        try:
            okx = OKXClient()
            balance = okx.get_balance()
            if balance:
                QMessageBox.information(self, "连接成功", 
                    f"✅ API连接正常\nUSDT余额: {balance['total']:.2f}")
            else:
                QMessageBox.warning(self, "连接失败", "❌ 无法获取余额，请检查API配置")
        except Exception as e:
            QMessageBox.critical(self, "连接错误", f"❌ {e}")
    
    def append_log(self, message):
        """添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.txt_log.append(f"[{timestamp}] {message}")
    
    def update_price(self, prices):
        """更新价格"""
        self.lbl_btc_price.setText(f"BTC: ${prices.get('BTC', 0):,.2f}")
        self.lbl_eth_price.setText(f"ETH: ${prices.get('ETH', 0):,.2f}")
    
    def update_positions(self, positions):
        """更新持仓"""
        if not positions:
            self.txt_positions.setText("暂无持仓")
            return
        
        text = ""
        for pos in positions:
            text += f"{pos['symbol']}: {pos['side']} {pos['contracts']}\n"
            text += f"  盈亏: {pos['unrealizedPnl']:.2f} USDT\n\n"
        self.txt_positions.setText(text)
    
    def update_balance(self, balance):
        """更新余额"""
        self.lbl_balance.setText(f"USDT余额: {balance:,.2f}")
    
    def quit_app(self):
        """退出应用"""
        self.stop_trading()
        QApplication.quit()
    
    def closeEvent(self, event):
        """关闭事件"""
        event.ignore()
        self.hide()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())
