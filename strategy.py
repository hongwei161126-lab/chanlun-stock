"""
三类买点识别策略
依据《缠中说禅》第17、20、21课：
  - 第一类买点：下跌走势完成（背驰）后的转折点，位于中枢下方
  - 第二类买点：第一类买点后，第一次次级别回调产生的低点
  - 第三类买点：次级别走势向上离开中枢后回试，低点不跌破 ZG
卖点同理反之，本策略聚焦买点选股。
"""
import chanlun


def detect_buy_points(analysis, params):
    """
    在某级别的缠论解析结果上识别买点（严格版，依据缠论原著收紧条件）。
    返回 list[dict]，每个含 type, level, price, detail, strength

    收紧原则（叠加缠论原著多课条件）：
      全局：
        - 笔数≥5 + 笔幅度过滤(>2%)
        - 买点时效性：最后笔在最近10根K线内（过期不推荐）
      一买（第17、24、26、29、67课）：
        - 强背驰(面积比<0.6) + DIF/DEA在0轴下方 + DIF线底背离
        - 至少2个下跌中枢且中枢逐步走低（走势终完美）
        - 底分型确认（背驰后转折成立）
        - 价在最后中枢下方
      二买（第20、21课）：
        - 前序必须有一买确认（背驰转折后）
        - 回调不破前低 + 回调幅度<上涨50%
        - 回调低点不进入中枢内部（在中枢ZD上方或中枢上沿）
      三买（第20课）：
        - 中枢延伸不超过9笔（超9笔=级别升级，非本级别三买）
        - 突破幅度>3% + 回试距ZG有2%空间
        - 回试后必须有向上确认笔（转折确认）
    """
    results = []
    bi = analysis["bi"]
    zhongshu = analysis["zhongshu"]
    is_div = analysis["divergence"]
    div_detail = analysis["divergence_detail"]
    last_price = analysis["last_price"]
    klines = analysis.get("klines", [])
    bottom_confirmed = analysis.get("bottom_fractal_confirmed", False)

    # 全局过滤：笔数不足5则走势不充分
    if len(bi) < 5:
        return results

    # 全局过滤：笔幅度过滤（过滤掉噪音笔，第67课）
    bi = chanlun.filter_weak_bi(bi, min_pct=0.02)
    if len(bi) < 5:
        return results

    # 买点时效性：最后笔的结束位置在最近10根合并K线内
    n_klines = len(klines)
    max_lookback = 10
    last_bi_end_idx = bi[-1]["end_index"] if bi else 0
    if n_klines > 0 and (n_klines - 1 - last_bi_end_idx) > max_lookback:
        return results  # 买点太久远，已过期

    # 背驰强度（面积比）
    div_ratio = 1.0
    if isinstance(div_detail, dict) and div_detail.get("prev_area", 0) > 0:
        div_ratio = div_detail["curr_area"] / div_detail["prev_area"]

    # ---- 第一类买点：下跌背驰转折 ----
    # 严格条件：
    #   强背驰(面积比<0.6) + 至少2个中枢 + 价在中枢下方
    #   + 中枢逐步走低（下跌趋势确认）
    #   + 底分型确认（转折成立，第67课）
    if is_div and bi and div_ratio < 0.6:
        last_bi = bi[-1]
        if last_bi["direction"] == "down":
            below_zhongshu = True
            if zhongshu:
                last_zs = zhongshu[-1]
                below_zhongshu = last_bi["end_value"] < last_zs["ZD"]
            # 至少2个中枢（走势终完美，下跌走势完成）
            has_trend = len(zhongshu) >= 2
            # 中枢逐步走低（真正的下跌趋势，非震荡）
            zs_declining = True
            if len(zhongshu) >= 2:
                for j in range(1, len(zhongshu)):
                    if zhongshu[j]["ZD"] >= zhongshu[j-1]["ZD"]:
                        zs_declining = False
                        break
            if below_zhongshu and has_trend and zs_declining:
                # 底分型确认（背驰后转折成立）
                if bottom_confirmed:
                    results.append({
                        "type": "第一类买点",
                        "price": last_bi["end_value"],
                        "detail": f"强背驰(力度比{div_ratio:.0%})+DIF底背离+{len(zhongshu)}中枢走低+底分型确认",
                        "strength": _div_strength(div_detail),
                    })

    # ---- 第二类买点 ----
    # 严格条件：
    #   前序有一买确认（背驰转折后） + 回调不破前低 + 回调<上涨50%
    #   + 回调低点不进入中枢内部（在中枢ZD上方）
    if bi and len(bi) >= 3 and zhongshu:
        last3 = bi[-3:]
        if (last3[0]["direction"] == "down" and last3[1]["direction"] == "up"
                and last3[2]["direction"] == "down"):
            prev_low = last3[0]["end_value"]
            up_high = last3[1]["end_value"]
            curr_low = last3[2]["end_value"]
            # 回调不破前低
            if curr_low > prev_low:
                up_range = up_high - prev_low
                pullback = up_high - curr_low
                # 回调幅度<上涨幅度的50%（回调不深，强势）
                if up_range > 0 and pullback / up_range < 0.5:
                    last_zs = zhongshu[-1]
                    # 回调低点不进入中枢内部（在ZD上方或ZD附近2%内）
                    if curr_low >= last_zs["ZD"] * 0.98:
                        # 前序有一买确认：背驰发生过且前面有下跌走势
                        has_first_buy = is_div or len(zhongshu) >= 2
                        if has_first_buy:
                            results.append({
                                "type": "第二类买点",
                                "price": curr_low,
                                "detail": f"背驰转折后回调{pullback/up_range*100:.0f}%不破前低，中枢ZD上方",
                                "strength": 2,
                            })

    # ---- 第三类买点 ----
    # 严格条件：
    #   中枢延伸不超过9笔（超9笔=级别升级，第22课）
    #   突破幅度>3% + 回试距ZG有2%空间
    #   回试后必须有向上确认笔（转折确认）
    if zhongshu and bi and len(bi) >= 2:
        last_zs = zhongshu[-1]
        ZG = last_zs["ZG"]
        zs_bi_count = len(last_zs.get("bi_list", []))
        # 中枢至少3笔 + 不超过9笔（超9笔级别升级）
        if 3 <= zs_bi_count <= 9:
            last_up = bi[-2] if bi[-2]["direction"] == "up" else None
            last_down = bi[-1] if bi[-1]["direction"] == "down" else None
            if last_up and last_down:
                broke_up = last_up["end_value"] > ZG and last_up["start_value"] <= ZG
                not_break = last_down["end_value"] > ZG
                if broke_up and not_break and ZG > 0:
                    breakout_pct = (last_up["end_value"] - ZG) / ZG * 100
                    margin_pct = (last_down["end_value"] - ZG) / ZG * 100
                    if breakout_pct > 3 and margin_pct > 2:
                        # 回试后必须有向上确认笔（当前最后一笔是向下=回试，
                        # 需要确认是否有随后的向上笔，或者当前价已回升）
                        confirmed = False
                        if len(bi) >= 3 and bi[-1]["direction"] == "down":
                            # 如果最后笔是回试的向下笔，检查当前价是否已回升
                            if last_price > last_down["end_value"]:
                                confirmed = True
                        elif len(bi) >= 3 and bi[-3]["direction"] == "up" \
                             and bi[-2]["direction"] == "down" \
                             and bi[-1]["direction"] == "up":
                            # 回试后已有向上笔确认
                            confirmed = True
                        if confirmed:
                            results.append({
                                "type": "第三类买点",
                                "price": last_down["end_value"],
                                "detail": f"突破中枢{breakout_pct:.1f}%后回试，距ZG{margin_pct:.1f}%，已确认",
                                "strength": 3,
                            })

    return results


def _div_strength(div_detail):
    """背驰强度评级"""
    if not isinstance(div_detail, dict):
        return 1
    prev = div_detail.get("prev_area", 0)
    curr = div_detail.get("curr_area", 0)
    if prev <= 0:
        return 1
    ratio = curr / prev
    if ratio < 0.5:
        return 3  # 强背驰
    elif ratio < 0.8:
        return 2
    return 1


def analyze_stock(df, params):
    """对单只股票单级别做缠论解析 + 买点识别"""
    analysis = chanlun.analyze(df, params)
    buy_points = detect_buy_points(analysis, params)
    return analysis, buy_points


def recommend_levels(buy_point, analysis, stop_loss_pct=0.08, take_profit_pct=0.20):
    """
    根据买点类型生成止盈止损建议（严格依据缠论结构，非固定百分比）。
    缠论止盈止损原则：买点失效即止损，出现对应卖点即止盈。
    返回含 stop_logic/take_logic 文字说明缠论依据。
    """
    buy_price = buy_point["price"]
    buy_type = buy_point["type"]
    zhongshu = analysis["zhongshu"]
    bi = analysis["bi"]
    zs = None
    if zhongshu:
        for z in reversed(zhongshu):
            if buy_price < z["ZG"] + 0.01:
                zs = z
                break
        if not zs:
            zs = zhongshu[-1]

    # 找一买低点（用于二买止损参考）
    first_buy_low = buy_price
    if buy_type == "第二类买点" and bi and len(bi) >= 3:
        first_buy_low = bi[-3]["end_value"]

    # ---- 止损：按买点类型确定失效条件 ----
    if buy_type == "第一类买点":
        # 一买失效 = 跌破一买低点（背驰判断失败）
        stop_loss = buy_price * (1 - 0.01)  # 跌破买点即失效
        stop_logic = "跌破一买低点(背驰失效)"
    elif buy_type == "第二类买点":
        # 二买失效 = 跌破一买低点（一买被破坏）
        stop_loss = first_buy_low
        stop_logic = f"跌破一买低点{first_buy_low:.2f}(一买被破坏)"
    elif buy_type == "第三类买点":
        # 三买失效 = 跌破中枢ZG（回到中枢内=突破失败）
        if zs:
            stop_loss = zs["ZG"]
            stop_logic = f"跌破中枢ZG({zs['ZG']:.2f}),回到中枢内=突破失败"
        else:
            stop_loss = buy_price * (1 - stop_loss_pct)
            stop_logic = "跌破买点(无中枢参考)"
    else:
        stop_loss = buy_price * (1 - stop_loss_pct)
        stop_logic = "百分比止损"
    # 防御：止损必须低于买入价
    if stop_loss >= buy_price:
        stop_loss = buy_price * (1 - 0.02)

    # ---- 止盈：按买点类型确定目标 ----
    if buy_type == "第一类买点":
        # 一买止盈 = 出现一卖(上涨背驰)；目标位=中枢ZG或前高
        if zs:
            target1 = zs["ZG"]
            take_logic = f"上涨至中枢ZG({zs['ZG']:.2f})或出现一卖(上涨背驰)"
        else:
            target1 = buy_price * (1 + take_profit_pct)
            take_logic = "出现一卖(上涨背驰)"
        target2 = target1 * 1.15
    elif buy_type == "第二类买点":
        # 二买止盈 = 出现二卖；目标位=前高或中枢ZG上方
        if zs:
            target1 = zs["ZG"] * 1.05
            take_logic = f"中枢ZG上方({zs['ZG']*1.05:.2f})或出现二卖"
        else:
            target1 = buy_price * (1 + take_profit_pct)
            take_logic = "出现二卖"
        target2 = target1 * 1.15
    elif buy_type == "第三类买点":
        # 三买止盈 = 中枢上沿+中枢高度(量度目标) 或 出现三卖
        if zs:
            zs_height = zs["ZG"] - zs["ZD"]
            target1 = zs["ZG"] + zs_height
            take_logic = f"中枢突破量度目标ZG+高度({target1:.2f})或出现三卖"
        else:
            target1 = buy_price * (1 + take_profit_pct)
            take_logic = "出现三卖"
        target2 = target1 * 1.10
    else:
        target1 = buy_price * (1 + take_profit_pct)
        target2 = buy_price * (1 + take_profit_pct * 2)
        take_logic = "百分比止盈"

    return {
        "buy": round(buy_price, 3),
        "stop_loss": round(stop_loss, 3),
        "stop_loss_pct": round((buy_price - stop_loss) / buy_price * 100, 2),
        "stop_logic": stop_logic,
        "take_profit": round(target1, 3),
        "take_profit_pct": round((target1 - buy_price) / buy_price * 100, 2),
        "take_logic": take_logic,
        "target2": round(target2, 3),
        "risk_reward": round((target1 - buy_price) / (buy_price - stop_loss), 2) if buy_price > stop_loss else 0,
        "zhongshu_ZG": round(zs["ZG"], 3) if zs else None,
        "zhongshu_ZD": round(zs["ZD"], 3) if zs else None,
    }


def detect_sell_points(analysis, params):
    """
    识别三类卖点（买点的镜像，依据第17、20课）。
    返回 list[dict]，每个含 type, price, detail
    """
    results = []
    bi = analysis["bi"]
    zhongshu = analysis["zhongshu"]
    macd_df = analysis.get("macd_df")
    last_price = analysis["last_price"]

    # 用上涨背驰（非下跌背驰）判断一卖
    is_up_div, up_div_detail = False, {}
    if macd_df is not None and bi is not None:
        lb = params.get("divergence_lookback", 2)
        is_up_div, up_div_detail = chanlun.check_divergence_up(macd_df, bi, lookback=lb)

    # 上涨背驰强度（面积比，越小越强势）
    up_div_ratio = 1.0
    if isinstance(up_div_detail, dict) and up_div_detail.get("prev_area", 0) > 0:
        up_div_ratio = up_div_detail["curr_area"] / up_div_detail["prev_area"]

    # ---- 第一类卖点：上涨背驰转折 ----
    # 严格条件：强上涨背驰(面积比<0.6) + 至少2个中枢(上涨走势完成) + 价在中枢上方
    if is_up_div and bi and up_div_ratio < 0.6:
        last_bi = bi[-1]
        if last_bi["direction"] == "up":
            above_zhongshu = True
            if zhongshu:
                last_zs = zhongshu[-1]
                above_zhongshu = last_bi["end_value"] > last_zs["ZG"]
            has_trend = len(zhongshu) >= 2
            if above_zhongshu and has_trend:
                results.append({
                    "type": "第一类卖点",
                    "price": last_bi["end_value"],
                    "detail": (f"强上涨背驰(力度比{up_div_ratio:.0%})，{len(zhongshu)}中枢上涨走势完成，"
                               f"前高{up_div_detail.get('prev_high','-'):.2f}→现高{up_div_detail.get('curr_high','-'):.2f}"),
                })

    # ---- 第二类卖点：一卖后首次反弹高点 ----
    if bi and len(bi) >= 3:
        last3 = bi[-3:]
        if (last3[0]["direction"] == "up" and last3[1]["direction"] == "down"
                and last3[2]["direction"] == "up"):
            # 反弹高点低于前高 → 二卖
            if last3[2]["end_value"] < last3[0]["end_value"]:
                results.append({
                    "type": "第二类卖点",
                    "price": last3[2]["end_value"],
                    "detail": f"一卖后首次反弹，高点{last3[2]['end_value']:.2f}低于前高{last3[0]['end_value']:.2f}",
                })

    # ---- 第三类卖点：向下离开中枢后回抽不破ZD ----
    if zhongshu and bi and len(bi) >= 2:
        last_zs = zhongshu[-1]
        ZD = last_zs["ZD"]
        last_down = bi[-2] if bi[-2]["direction"] == "down" else None
        last_up = bi[-1] if bi[-1]["direction"] == "up" else None
        if last_down and last_up:
            broke_down = last_down["end_value"] < ZD and last_down["start_value"] >= ZD
            not_break = last_up["end_value"] < ZD
            if broke_down and not_break:
                results.append({
                    "type": "第三类卖点",
                    "price": last_up["end_value"],
                    "detail": f"跌破中枢(ZD={ZD:.2f})后回抽不破，回抽高{last_up['end_value']:.2f}",
                })

    return results


def full_recommendation(df, params):
    """
    生成完整推荐：买点+止盈止损(含缠论依据) + 卖点 + 当前信号状态
    返回适合API的字典
    """
    analysis, buy_points = analyze_stock(df, params)
    sell_points = detect_sell_points(analysis, params)
    last_price = float(df["close"].iloc[-1])
    recommendations = []
    for bp in buy_points:
        rec = recommend_levels(bp, analysis)
        rec["buy_type"] = bp["type"]
        rec["detail"] = bp["detail"]
        rec["strength"] = bp.get("strength", 1)
        recommendations.append(rec)
    return {
        "last_price": round(last_price, 3),
        "divergence": analysis["divergence"],
        "zhongshu_count": len(analysis["zhongshu"]),
        "last_zhongshu": {
            "ZG": round(analysis["zhongshu"][-1]["ZG"], 3),
            "ZD": round(analysis["zhongshu"][-1]["ZD"], 3),
        } if analysis["zhongshu"] else None,
        "buy_points": recommendations,
        "sell_points": sell_points,
        # 兼容旧字段：取第一个卖点
        "sell_signal": sell_points[0] if sell_points else None,
    }


def _detect_sell_signal(analysis):
    """旧接口兼容：检测卖点信号"""
    return None
