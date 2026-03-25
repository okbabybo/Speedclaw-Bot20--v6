# HEARTBEAT.md

## 定期检查任务

### 1. OKX 市场盯盘（每30分钟）
每次心跳检查以下关键信号，有触发则汇报用户：
- BTC 进入 $70,000-$70,500（LONG 机会）
- BTC 进入 $71,400-$72,000（SHORT 机会）
- BTC 资金费率 > 0.03%（多头拥挤预警）
- BTC 跌破 24h 低点或突破 24h 高点

运行脚本：
```bash
python3 /root/.openclaw/workspace/monitor/market_monitor.py
```

上次检查状态记录在 `monitor/last_alert.json`，检查前对比避免重复发送同样信号。

### 2. 常规心跳
无上述信号时回复 HEARTBEAT_OK
