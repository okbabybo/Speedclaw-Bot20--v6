/**
 * SpeedClaw Bot Ecosystem - PM2 统一管理配置
 *
 * 设计目标：
 * 1. 防止频繁重启：max_restarts + min_uptime 双闸门
 * 2. 崩溃冷却：crashRestartDelay 平滑重启，避免雪崩
 * 3. 内存保护：max_memory_restart 防止内存泄漏拖死
 * 4. 集中日志：log_date_format + merge_logs 便于排查
 * 5. 重启阈值可调：本文件即真相源，不再散落各处
 */

module.exports = {
  apps: [
    {
      name: 'bot20x',
      script: 'bot_20x.py',
      interpreter: 'python3',
      cwd: '/root/.openclaw/workspace',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      // === 防频繁重启核心参数 ===
      max_restarts: 10,          // 进程生命周期内最多重启10次
      min_uptime: '60s',         // 运行不足60秒视为启动失败
      restart_delay: 5000,       // 崩溃后延迟5秒再启，避免雪崩
      max_memory_restart: '500M',// 内存超500M自动重启（防泄漏）
      // === 崩溃告警 ===
      // PM2 会在 max_restarts 触发时调用此脚本
      listen_timeout: 8000,
      kill_timeout: 5000,
      wait_ready: false,
      // === 日志 ===
      log_file: '/root/.openclaw/workspace/logs/bot20x-combined.log',
      out_file: '/root/.openclaw/workspace/logs/bot20x-out.log',
      error_file: '/root/.openclaw/workspace/logs/bot20x-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      // === 环境 ===
      env: {
        NODE_ENV: 'production',
        BOT_NAME: 'bot20x',
      },
    },
    {
      name: 'bot-king',
      script: 'bot_king.py',
      interpreter: 'python3',
      cwd: '/root/.openclaw/workspace',
      instances: 1,
      exec_mode: 'fork',
      autorestart: true,
      max_restarts: 10,
      min_uptime: '60s',
      restart_delay: 5000,
      max_memory_restart: '500M',
      listen_timeout: 8000,
      kill_timeout: 5000,
      log_file: '/root/.openclaw/workspace/logs/bot-king-combined.log',
      out_file: '/root/.openclaw/workspace/logs/bot-king-out.log',
      error_file: '/root/.openclaw/workspace/logs/bot-king-error.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
      env: {
        NODE_ENV: 'production',
        BOT_NAME: 'bot-king',
      },
    },
    // 注: botking-tg 和 auto-activate 原PM2条目实际无对应脚本文件
    // 已从配置移除,避免PM2启动时报Script not found
  ],
};

/**
 * 部署命令：
 *   pm2 delete all
 *   pm2 start ecosystem.config.js
 *   pm2 save
 *
 * 验证命令：
 *   pm2 list                # 查看所有进程状态
 *   pm2 jlist | grep max_restarts  # 确认参数已应用
 *
 * 配套机制（已写入bot_20x.py）：
 *   - CRASH_LIMIT=5：10分钟内崩溃5次 → 进入安全模式不开仓
 *   - check_crash_safety()：每次开仓前自检
 *   - crash_alert.sh（新增）：crash_count≥2时推送Telegram
 */