"""
定时任务引擎
  - 每日收盘后自动全市场扫描
  - 扫描完成后执行自动交易
  - 推送高评分结果
使用 APScheduler BackgroundScheduler 在 Flask 进程内后台运行。
alwaysdata 部署时随 gunicorn worker 启动。
"""
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import scanner
import auto_trade
import notifier
import trading

# 北京时间（服务器可能在UTC时区，统一用北京时间判断交易日）
_BJ_TZ = timezone(timedelta(hours=8))

_logger = logging.getLogger("scheduler")
_scheduler = None


def is_trading_day():
    """
    判断今天是否为A股交易日（排除周末）。
    注：法定节假日需要接入交易日历，这里先排除周末；
    扫描任务设在16:00，闭市后执行不影响数据。
    使用北京时间，避免服务器时区差异导致判断错误。
    """
    today = datetime.now(_BJ_TZ)
    # weekday(): 周一0...周日6
    return today.weekday() < 5


def daily_scan_job(force=False):
    """
    每日收盘后执行：扫描 → 自动交易 → 推送
    Args:
        force: True=手动触发时使用，跳过非交易日判断（周末也能验证功能）
    """
    # 闭市日（周末）不操作：仅Cron自动触发时拦截；force=True手动触发放行
    if not force and not is_trading_day():
        _logger.info("[定时任务] 今天非交易日(周末)，跳过")
        return
    _logger.info(f"[{'手动' if force else '定时'}任务] 开始全市场扫描")
    ok, msg = scanner.start_scan()
    if not ok:
        _logger.info(f"[定时任务] 扫描未启动: {msg}")
        return
    # 等待扫描完成（scanner 是后台线程，这里轮询状态）
    import time
    while scanner.is_scanning():
        time.sleep(10)
    status = scanner.get_status()
    _logger.info(f"[定时任务] 扫描完成: {status['msg']}")
    # 推送扫描结果
    hits = scanner.get_hits()
    if hits:
        notifier.notify_scan_results(hits)
    # 执行自动交易
    _logger.info("[定时任务] 执行自动交易")
    summary = auto_trade.run_auto_trade()
    _logger.info(f"[定时任务] 交易完成: 买{len(summary['bought'])} 卖{len(summary['sold'])} 错{len(summary['errors'])}")


def init_scheduler():
    """初始化并启动定时任务（Flask 启动时调用）"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    # 每个交易日15:30收盘，16:00开始扫描（确保数据已更新）
    _scheduler.add_job(
        daily_scan_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        id="daily_scan",
        replace_existing=True,
    )
    _scheduler.start()
    _logger.info("[定时任务] 已启动：每日16:00(周一至五)自动扫描+交易")
    return _scheduler


def get_jobs():
    """获取定时任务列表（前端展示）"""
    if not _scheduler:
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else "",
            "trigger": str(job.trigger),
        })
    return jobs


def trigger_now():
    """
    手动触发一次扫描+自动交易（前端按钮调用）。
    传force=True，跳过交易日拦截，周末/节假日也能验证功能。
    """
    import threading
    t = threading.Thread(target=daily_scan_job, args=(True,), daemon=True)
    t.start()
    return {"ok": True, "msg": "已触发扫描+自动交易，稍后在持仓/交易明细查看结果"}
