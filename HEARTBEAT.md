# HEARTBEAT.md

## 定期检查任务

### 1. OKX 定时盯盘（每30分钟，自动推送）
Cron任务 ID: e4f6fc67-2edb-46d1-b92c-38cc51cb6851
当收到 cron:okx-watch 系统事件时，执行以下操作：

**执行流程：**
1. 拉取 BTC-USDT-SWAP 和 ETH-USDT-SWAP 实时数据
2. 拉取 OKX 账户余额和持仓状态
3. 按表格格式整理所有数据
4. 通过飞书推送完整盯盘报告

**汇报格式（强制表格）：**
```
**BTC-USDT-SWAP**
| 项目 | 数值 |
|------|------|
| 最新价 | xxx USDT |
| 标记价 | xxx |
| 合约溢价 | x.xxxx% |
| 资金费率 | x.xxxx% |
| 24h成交 | x,xxx,xxx 张 |
| 24h高/低 | xxx / xxx |
| 盘口 | xxx / xxx |

**ETH-USDT-SWAP**
（同样格式）

**账户状态**
| 项目 | 数值 |
|------|------|
| USDT净值 | x,xxx.xx U |
| 可用保证金 | x,xxx.xx USDT |
| 挂单占用 | x.xx USDT |
| BTC/ETH持仓 | 状态 |

**操作建议**
| 项目 | 数值 |
|------|------|
| 方向 | Long/Short/观望 |
| 压力位 | xxx |
| 支撑位 | xxx |
| 时效 | 15分钟 |
```

### 2. 信号触发检查
当价格触及以下条件时，优先推送预警：
- BTC 进入 $70,000-$70,500 → LONG机会
- BTC 进入 $71,400-$72,000 → SHORT机会
- BTC 资金费率 > 0.03% → 多头拥挤预警
- BTC 跌破24h低点或突破24h高点

### 3. 常规心跳
无上述信号时回复 HEARTBEAT_OK
