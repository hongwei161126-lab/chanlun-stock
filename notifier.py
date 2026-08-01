"""
推送提醒模块
支持两种推送方式：
  - 邮件（SMTP）：通用，alwaysdata 可直接发送
  - Server酱（微信推送）：需 SCTKEY，扫码关注公众号即可收到微信通知
配置存在 trading.db 的 settings 表，通过 Web UI 设置。
"""
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

import trading


def _get(key, default=""):
    return trading.get_setting(key, default)


def send(title, content, html=None):
    """
    统一推送入口：根据已配置的渠道发送。
    返回 {"ok": bool, "msg": str, "channels": [...]}
    """
    results = []
    # 邮件
    email_enabled = _get("notify_email_enabled", "off") == "on"
    if email_enabled:
        r = _send_email(title, content)
        results.append(("邮件", r))
    # Server酱
    sct_enabled = _get("notify_sct_enabled", "off") == "on"
    if sct_enabled:
        r = _send_serverchan(title, content)
        results.append(("Server酱", r))
    if not results:
        return {"ok": False, "msg": "未启用任何推送渠道", "channels": []}
    ok = any(r[1]["ok"] for r in results)
    msgs = [f"{name}: {r['msg']}" for name, r in results]
    return {"ok": ok, "msg": "；".join(msgs), "channels": [r[0] for r in results]}


def _send_email(title, content):
    """通过 SMTP 发送邮件"""
    smtp_host = _get("notify_smtp_host")
    smtp_port = int(_get("notify_smtp_port", "465"))
    smtp_user = _get("notify_smtp_user")
    smtp_pass = _get("notify_smtp_pass")
    to_addr = _get("notify_email_to")
    if not all([smtp_host, smtp_user, smtp_pass, to_addr]):
        return {"ok": False, "msg": "邮件配置不完整"}
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[缠论选股] {title}"
        msg["From"] = smtp_user
        msg["To"] = to_addr
        msg.attach(MIMEText(content, "plain", "utf-8"))
        import ssl
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=ctx, timeout=20) as s:
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_addr], msg.as_string())
        return {"ok": True, "msg": f"已发送至{to_addr}"}
    except Exception as e:
        return {"ok": False, "msg": f"发送失败: {e}"}


def _send_serverchan(title, content):
    """通过 Server酱 发送微信推送（https://sct.ftqq.com）"""
    sctkey = _get("notify_sct_key")
    if not sctkey:
        return {"ok": False, "msg": "未配置SCTKEY"}
    try:
        url = f"https://sctapi.ftqq.com/{sctkey}.send"
        r = requests.post(url, data={"title": title, "desp": content}, timeout=15)
        j = r.json()
        if j.get("code") == 0:
            return {"ok": True, "msg": "微信推送成功"}
        return {"ok": False, "msg": j.get("message", "未知错误")}
    except Exception as e:
        return {"ok": False, "msg": f"推送失败: {e}"}


def notify_scan_results(hits):
    """扫描完成后推送高评分命中结果"""
    if not hits:
        return
    # 只推送评分≥65的（优良信号）
    top = [h for h in hits if h.get("score", 0) >= 65]
    if not top:
        return
    title = f"扫描完成：{len(hits)}只命中，{len(top)}只高评分"
    lines = [f"## 缠论扫描结果\n共命中{len(hits)}只，以下是评分≥65的优质标的：\n"]
    for h in top[:15]:
        lines.append(
            f"- **{h['name']}({h['symbol']})** 评分{h['score']} | "
            f"{'/'.join(h['buy_types'])} | {h.get('score_detail','')}"
        )
    if len(top) > 15:
        lines.append(f"\n...还有{len(top)-15}只")
    content = "\n".join(lines)
    return send(title, content)


def notify_trade(action, symbol, name, price, shares, reason, pnl=None):
    """交易成交后推送"""
    emoji = "买入" if action == "buy" else "卖出"
    pnl_str = f" 盈亏{pnl:.2f}" if pnl is not None else ""
    title = f"{emoji}{name}({symbol}) @{price:.2f}"
    content = f"## {emoji}提醒\n\n- 股票：{name}({symbol})\n- 价格：{price:.2f}\n- 数量：{shares}股\n- 原因：{reason}{pnl_str}"
    return send(title, content)
