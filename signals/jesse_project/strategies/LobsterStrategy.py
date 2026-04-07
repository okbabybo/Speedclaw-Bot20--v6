from jesse import strategies
from jesse import indicators as ta
from jesse.utils import cross_over, cross_under


class LobsterStrategy(strategies.Strategy):
    """
    混沌龙虾·Jesse策略
    基于我们的规则:
    - RSI超买超卖
    - EMA趋势确认
    - MACD方向
    """

    def should_long(self) -> bool:
        """
        做多条件
        """
        return cross_over(self.indicators.rsi, 30) and self.close > self.indicators.ema20

    def should_short(self) -> bool:
        """
        做空条件
        """
        return cross_under(self.indicators.rsi, 70) and self.close < self.indicators.ema20

    def should_cancel_entry(self) -> bool:
        """
        取消订单条件
        """
        return False

    def go_long(self):
        """
        做多
        """
        self.buy = [
            (self.price, self.vars.qty)
        ]

    def go_short(self):
        """
        做空
        """
        self.sell = [
            (self.price, self.vars.qty)
        ]

    def update_position(self):
        """
        更新持仓 - 止损止盈
        """
        # 止损 -5%
        if self.position.pnl_percentage < -5:
            self.liquidate()

        # 止盈 +2%
        if self.position.pnl_percentage > 2:
            self.liquidate()

    def before(self):
        """
        指标计算
        """
        self.indicators.ema20 = ta.ema(self.candles, 20)
        self.indicators.rsi = ta.rsi(self.candles, 14)
