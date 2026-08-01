"""
自动交易执行器
基于缠论扫描结果自动模拟买卖：
  - 买入：每日扫描后，对评分≥阈值的标的自动买入（资金分配）
  - 卖出：检查持仓止盈止损 + 缠论卖点信号
"""
import trading
import scanner
from data_fetcher import fetch_kline, fetch_realtime
from strategy import full_recommendation
import config
import notifier


# 自动交易参数默认值
DEFAULT_BUY_SCORE = 70       # 买入评分阈值
DEFAULT_MAX_POSITIONS = 5    # 最大持仓数
DEFAULT_BUY_RATIO = 0.18     # 单只仓位占比（18%）
DEFAULT_MIN_SCORE = 65       # 推送阈值


def run_auto_trade():
    """
    执行一轮自动交易（扫描完成后调用）：
      1. 检查持仓止盈止损 → 卖出
      2. 检查持仓缠论卖点 → 卖出
      3. 扫描结果中按评分买入新标的
    返回执行摘要
    """
    summary = {"sold": [], "bought": [], "errors": []}
    account = trading.get_account()

    # ---- 1. 止盈止损 + 缠论卖点检查 ----
    positions = trading.get_positions()
    if positions:
        codes = []
        for p in positions:
            pre = "sh" if p["symbol"].startswith(("6", "9", "5")) else "sz"
            codes.append(pre + p["symbol"])
        rt = fetch_realtime(codes) or []
        # 双key：{ 纯代码: price, 带前缀代码: price }，避免止盈/卖点两处查找格式不一致
        price_map = {}
        for r in rt:
            if not r.get("price"):
                continue
            price_map[r.get("code", "")] = r["price"]                 # 纯6位代码
            if r.get("prefix"):
                price_map[r["prefix"]] = r["price"]                   # sh/sz+代码 前缀
        # 纯 symbol 也建一份，方便持仓直接按 symbol 查
        for r in rt:
            if not r.get("price") or not r.get("code"):
                continue
            price_map[r["code"]] = r["price"]

        # 1a) 止盈止损触发（按纯6位symbol查找）
        signals = trading.check_stop_loss_take_profit(price_map)
        for sig in signals:
            r = trading.sell(sig["symbol"], sig["price"], reason=sig["reason"])
            if r.get("ok"):
                summary["sold"].append(f"{sig['symbol']}@{sig['price']:.2f}({sig['reason']})")
                notifier.notify_trade("sell", sig["symbol"], "", sig["price"], 0, sig["reason"], r.get("pnl"))

        # 1b) 缠论卖点检查（用实时价卖出，禁止用K线收盘价）
        # 先把刚刚卖出的持仓剔除，避免重复卖出
        sold_now = {s.split("@")[0] for s in summary["sold"]}
        for p in positions:
            if p["symbol"] in sold_now:
                continue
            try:
                df = fetch_kline(p["symbol"], level="daily", count=120)
                rec = full_recommendation(df, config.CHANLUN_PARAMS)
                if rec.get("sell_points"):
                    sp = rec["sell_points"][0]
                    # 必须用实时价：直接按 symbol（6位纯代码）查 price_map
                    rt_p = price_map.get(p["symbol"])
                    if not rt_p or rt_p <= 0:
                        summary["errors"].append(f"{p['symbol']}无实时价，跳过卖点卖出")
                        continue
                    r = trading.sell(p["symbol"], rt_p, reason=f"缠论{sp['type']}")
                    if r.get("ok"):
                        summary["sold"].append(f"{p['symbol']}@{rt_p:.2f}(缠论{sp['type']})")
                        notifier.notify_trade("sell", p["symbol"], p["name"], rt_p, p["shares"],
                                              f"缠论{sp['type']}", r.get("pnl"))
            except Exception as e:
                summary["errors"].append(f"{p['symbol']}卖点检查失败: {e}")

    # ---- 3. 自动买入 ----
    auto_mode = trading.get_setting("auto_mode", "off") == "on"
    if not auto_mode:
        return summary

    hits = scanner.get_hits()
    if not hits:
        return summary

    buy_score = int(trading.get_setting("auto_buy_score", DEFAULT_BUY_SCORE))
    max_pos = int(trading.get_setting("auto_max_positions", DEFAULT_MAX_POSITIONS))
    buy_ratio = float(trading.get_setting("auto_buy_ratio", DEFAULT_BUY_RATIO))

    cur_positions = trading.get_positions()
    held_symbols = {p["symbol"] for p in cur_positions}
    available_slots = max_pos - len(held_symbols)
    if available_slots <= 0:
        return summary

    account = trading.get_account()
    # 按评分降序，买入未持仓的高分标的
    candidates = [h for h in hits if h["score"] >= buy_score and h["symbol"] not in held_symbols]
    # 批量获取实时行情，禁止用缓存/推荐价下单
    cand_codes = []
    for h in candidates:
        pre = "sh" if h["symbol"].startswith(("6", "9", "5")) else "sz"
        cand_codes.append(pre + h["symbol"])
    rt_map = {}
    if cand_codes:
        try:
            rt = fetch_realtime(cand_codes) or []
            for r in rt:
                rt_map[r.get("code", "")] = r
        except Exception:
            pass
    bought = 0
    for h in candidates:
        if bought >= available_slots:
            break
        # 必须用实时价，找不到实时价则跳过（禁止自动改价）
        pre = "sh" if h["symbol"].startswith(("6", "9", "5")) else "sz"
        rt = rt_map.get(pre + h["symbol"])
        if not rt or not rt.get("price") or rt["price"] <= 0:
            summary["errors"].append(f"{h['symbol']}无实时价，跳过")
            continue
        rt_price = float(rt["price"])
        buy_amount = account["balance"] * buy_ratio
        # 整百股（用实时价计算）
        shares = int(buy_amount / rt_price / 100) * 100
        if shares < 100:
            continue
        # 获取止盈止损
        try:
            df = fetch_kline(h["symbol"], level="daily", count=120)
            rec = full_recommendation(df, config.CHANLUN_PARAMS)
            if rec["buy_points"]:
                bp = rec["buy_points"][0]
                r = trading.buy(
                    h["symbol"], h["name"], rt_price, shares,
                    strategy=f"自动-{h['buy_types'][0]}", mode="auto",
                    stop_loss=bp["stop_loss"], take_profit=bp["take_profit"],
                    reason=f"评分{h['score']} {h.get('score_detail','')}",
                    strategy_term="long"  # 日线扫描=长期策略
                )
                if r.get("ok"):
                    summary["bought"].append(f"{h['name']}({h['symbol']}) {shares}股@{rt_price:.2f}")
                    notifier.notify_trade("buy", h["symbol"], h["name"], rt_price, shares,
                                          f"自动买入 评分{h['score']}")
                    bought += 1
        except Exception as e:
            summary["errors"].append(f"{h['symbol']}买入失败: {e}")

    return summary
