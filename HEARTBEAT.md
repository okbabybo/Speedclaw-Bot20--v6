# HEARTBEAT.md

## 定期检查任务

### 1. OKX 定时盯盘（每30分钟，自动推送）
Cron任务 ID: e4f6fc67-2edb-46d1-b92c-38cc51cb6851
当收到 cron:okx-watch 系统事件时，执行以下操作：

**执行流程：**
1. 运行多Agent分析系统：`cd /root/.openclaw/workspace/signals && python3 multi_agent_trading.py`
2. 拉取 BTC + ETH 实时数据
3. 通过飞书推送完整多Agent分析报告

**汇报格式（多Agent协作）：**
```
【BTC】🟢 BUY | 置信度 88%
当前价: xxxxx

🍖 做多理由:
  • 趋势向上
  • 连续X根阳线
  ...

入场: xxxxx | 止损: xxxxx | 目标: xxxxx

【ETH】🔴 SELL | 置信度 75%
...
```

### 2. 实时盯盘（后台进程）
后台脚本每15秒扫描一次，触发以下条件时立即推送：
- BTC突破70,000 → 🟢 多头信号
- BTC跌破68,500 → 🔴 空头信号  
- BTC回调到69,000-69,200 → 🟢 入场做多
- ETH突破2,150 → 🟢 多头信号
- ETH回调到2,100-2,110 → 🟢 入场做多

### 3. 信号触发检查
当价格触及以下条件时，优先推送预警：
- BTC 进入 $70,000-$70,500 → LONG机会
- BTC 进入 $71,400-$72,000 → SHORT机会
- BTC 资金费率 > 0.03% → 多头拥挤预警
- BTC 跌破24h低点或突破24h高点

### 4. 常规心跳
无上述信号时回复 HEARTBEAT_OK
