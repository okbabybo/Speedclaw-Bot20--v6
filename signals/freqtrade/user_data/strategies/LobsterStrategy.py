# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from pandas import DataFrame
from typing import Optional, Union

from freqtrade.strategy import (
    IStrategy,
    Trade,
    Order,
    PairLocks,
    informative,
    BooleanParameter,
    CategoricalParameter,
    DecimalParameter,
    IntParameter,
    RealParameter,
    timeframe_to_minutes,
    timeframe_to_next_date,
    timeframe_to_prev_date,
    merge_informative_pair,
    stoploss_from_absolute,
    stoploss_from_open,
)

import talib.abstract as ta
from technical import qtpylib


class LobsterStrategy(IStrategy):
    """
    混沌龙虾进化版 v3.0
    - 多周期趋势过滤 (1H + 4H)
    - ATR动态止损
    - ADX趋势强度过滤
    - BTC联动确认
    - 分批止盈
    - 量价确认
    """

    INTERFACE_VERSION = 3

    # ========== 多空设置 ==========
    can_short: bool = True

    # ========== 止盈优化 - 分批出场 ==========
    # 0: 0.003 (0.3%), 30: 0.008 (0.8%), 60: 0.015 (1.5%), 120: 0.025 (2.5%)
    minimal_roi = {
        "0": 0.003,
        "30": 0.008,
        "60": 0.015,
        "120": 0.025,
    }

    # ========== ATR动态止损 ==========
    use_custom_stoploss = True
    trailing_stop = True
    trailing_only_offset_is_reached = True
    trailing_stop_positive = 0.003  # 追踪止盈0.3%
    trailing_stop_positive_offset = 0.008  # 启动位置0.8%
    stoploss = -0.03  # 止损-3%（更紧）

    # ========== 其他设置 ==========
    timeframe = "5m"
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    startup_candle_count = 200

    # ========== Hyperopt参数 ==========
    buy_rsi = IntParameter(low=20, high=45, default=35, space="buy", optimize=True, load=True)
    sell_rsi = IntParameter(low=55, high=85, default=70, space="sell", optimize=True, load=True)
    short_rsi = IntParameter(low=55, high=85, default=65, space="sell", optimize=True, load=True)
    exit_short_rsi = IntParameter(low=15, high=45, default=30, space="exit", optimize=True, load=True)
    
    # ADX趋势强度过滤
    adx_threshold = IntParameter(low=15, high=30, default=20, space="buy", optimize=True, load=True)
    
    # 成交量放大倍数
    min_volume_ratio = DecimalParameter(low=1.0, high=2.0, default=1.2, decimals=1, space="buy", optimize=True, load=True)

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # ========== 多周期信息对 ==========
    def informative_pairs(self):
        return [
            ("ETH/USDT:USDT", "5m"),
            ("ETH/USDT:USDT", "1h"),
            ("ETH/USDT:USDT", "4h"),
            ("BTC/USDT:USDT", "1h"),   # BTC联动
        ]

    # ========== ATR计算（用于动态止损） ==========
    def custom_stoploss(self, pair: str, trade: Trade, current_time, current_rate, current_profit: float, **kwargs) -> float:
        # 使用ATR计算动态止损
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss
        
        atr = ta.ATR(dataframe, timeperiod=14)
        atr_value = atr.iloc[-1]
        
        # 动态止损：基于ATR的倍数
        # 3倍ATR作为止损距离
        atr_multiplier = 3
        
        # 基础止损
        base_stoploss = -0.03
        
        # ATR止损
        atr_distance = (atr_value / current_rate) * atr_multiplier
        
        # 返回两者中较严格的那个
        return min(base_stoploss, -atr_distance)

    # ========== 指标计算 ==========
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ----- 5分钟基础指标 -----
        dataframe["rsi"] = ta.RSI(dataframe)
        dataframe["tema"] = ta.TEMA(dataframe, timeperiod=9)
        
        # Bollinger Bands
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]
        dataframe["bb_percent"] = (dataframe["close"] - dataframe["bb_lowerband"]) / (
            dataframe["bb_upperband"] - dataframe["bb_lowerband"]
        )
        
        # ATR（用于动态止损）
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        
        # ADX趋势强度
        dataframe["adx"] = ta.ADX(dataframe)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe)
        
        # 成交量
        dataframe["volume"] = dataframe["volume"]
        dataframe["volume_ma"] = dataframe["volume"].rolling(20).mean()
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume_ma"]
        
        # MACD
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]
        
        # K线形态 - 连续阳线/阴线
        dataframe["连续阳线"] = (dataframe["close"] > dataframe["open"]).rolling(3).sum()
        dataframe["连续阴线"] = (dataframe["close"] < dataframe["open"]).rolling(3).sum()

        # ===== 1小时趋势 =====
        dataframe_1h = self.dp.get_pair_dataframe(pair="ETH/USDT:USDT", timeframe="1h")
        dataframe_1h["rsi_1h"] = ta.RSI(dataframe_1h)
        dataframe_1h["ema20_1h"] = ta.EMA(dataframe_1h, timeperiod=20)
        dataframe_1h["ema50_1h"] = ta.EMA(dataframe_1h, timeperiod=50)
        dataframe_1h["close_1h"] = dataframe_1h["close"]
        dataframe_1h["atr_1h"] = ta.ATR(dataframe_1h, timeperiod=14)
        dataframe_1h["adx_1h"] = ta.ADX(dataframe_1h)
        dataframe = merge_informative_pair(dataframe, dataframe_1h, self.timeframe, "1h", fill_na=True)

        # ===== 4小时趋势 =====
        dataframe_4h = self.dp.get_pair_dataframe(pair="ETH/USDT:USDT", timeframe="4h")
        dataframe_4h["rsi_4h"] = ta.RSI(dataframe_4h)
        dataframe_4h["ema20_4h"] = ta.EMA(dataframe_4h, timeperiod=20)
        dataframe_4h["ema50_4h"] = ta.EMA(dataframe_4h, timeperiod=50)
        dataframe_4h["close_4h"] = dataframe_4h["close"]
        dataframe_4h["adx_4h"] = ta.ADX(dataframe_4h)
        dataframe = merge_informative_pair(dataframe, dataframe_4h, self.timeframe, "4h", fill_na=True)

        # ===== BTC 1小时联动 =====
        dataframe_btc = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe="1h")
        dataframe_btc["btc_ema20_1h"] = ta.EMA(dataframe_btc, timeperiod=20)
        dataframe_btc["btc_close_1h"] = dataframe_btc["close"]
        dataframe_btc["btc_trend_1h"] = dataframe_btc["close"] > dataframe_btc["btc_ema20_1h"]
        dataframe = merge_informative_pair(dataframe, dataframe_btc, self.timeframe, "1h", fill_na=True)

        return dataframe

    # ========== 入场信号 ==========
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ----- 趋势方向 -----
        uptrend_4h = dataframe["close_4h"] > dataframe["ema20_4h"]
        downtrend_4h = dataframe["close_4h"] < dataframe["ema20_4h"]
        uptrend_1h = dataframe["close_1h"] > dataframe["ema20_1h"]
        downtrend_1h = dataframe["close_1h"] < dataframe["ema20_1h"]
        
        # BTC联动
        btc_up = dataframe["btc_trend_1h"] == True
        btc_down = dataframe["btc_trend_1h"] == False
        
        # ADX趋势强度（需要足够强的趋势才入场）
        strong_trend = dataframe["adx_4h"] > self.adx_threshold.value
        
        # 成交量确认
        volume_confirm = dataframe["volume_ratio"] > self.min_volume_ratio.value
        
        # 连续K线确认
        bullish_candle = dataframe["连续阳线"] >= 2
        bearish_candle = dataframe["连续阴线"] >= 2

        # ===== 做多信号 =====
        dataframe.loc[
            (
                # 1. 趋势向上
                uptrend_4h & uptrend_1h
                # 2. BTC也向上（联动确认）
                & btc_up
                # 3. 趋势强度足够
                & strong_trend
                # 4. RSI超卖但不极值
                & (dataframe["rsi"] < self.buy_rsi.value)
                & (dataframe["rsi"] > 20)
                # 5. TEMA在布林中轨附近
                & (dataframe["tema"] <= dataframe["bb_middleband"])
                & (dataframe["tema"] > dataframe["tema"].shift(1))
                # 6. MACD柱状体为正
                & (dataframe["macdhist"] > 0)
                # 7. 成交量放大
                & volume_confirm
                # 8. 连续阳线确认
                & bullish_candle
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== 做空信号 =====
        dataframe.loc[
            (
                # 1. 趋势向下
                downtrend_4h & downtrend_1h
                # 2. BTC也向下（联动确认）
                & btc_down
                # 3. 趋势强度足够
                & strong_trend
                # 4. RSI超买但不极值
                & (dataframe["rsi"] > self.short_rsi.value)
                & (dataframe["rsi"] < 80)
                # 5. TEMA在布林中轨附近
                & (dataframe["tema"] >= dataframe["bb_middleband"])
                & (dataframe["tema"] < dataframe["tema"].shift(1))
                # 6. MACD柱状体为负
                & (dataframe["macdhist"] < 0)
                # 7. 成交量放大
                & volume_confirm
                # 8. 连续阴线确认
                & bearish_candle
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    # ========== 出场信号 ==========
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # ===== 平多信号 =====
        dataframe.loc[
            (
                # RSI超买
                (dataframe["rsi"] > self.sell_rsi.value)
                & (dataframe["tema"] > dataframe["bb_middleband"])
                & (dataframe["tema"] < dataframe["tema"].shift(1))
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1

        # ===== 平空信号 =====
        dataframe.loc[
            (
                # RSI超卖
                (dataframe["rsi"] < self.exit_short_rsi.value)
                & (dataframe["tema"] <= dataframe["bb_middleband"])
                & (dataframe["tema"] > dataframe["tema"].shift(1))
                & (dataframe["volume"] > 0)
            ),
            "exit_short",
        ] = 1

        return dataframe
