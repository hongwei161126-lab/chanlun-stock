"""
缠论核心引擎
实现：K线包含合并 → 顶底分型 → 笔 → 走势中枢 → MACD背驰
依据《缠中说禅：教你炒股票108课》第17、18、20、67、68、69课定义
"""
import numpy as np
import pandas as pd


# ============================================================
# 1. K线包含关系处理（合并K线）
# 第67-69课基础：先处理包含，再做分型
# ============================================================
def merge_klines(df):
    """
    处理K线包含关系，返回合并后的K线列表。
    规则（第65、67课）：
      - 相邻两K线，若高低点完全包含，则合并
      - 方向向上时取高者的高、低者的低中较高者为新低；向下反之
      - 合并后K线记 original_index 范围
    输入 df 需有列: open high low close
    """
    klines = []
    for i, row in df.iterrows():
        k = {
            "high": row["high"],
            "low": row["low"],
            "open": row["open"],
            "close": row["close"],
            "begin": i,
            "end": i,
        }
        if not klines:
            klines.append(k)
            continue
        prev = klines[-1]
        # 判断包含：prev 包含 k 或 k 包含 prev
        if (prev["high"] >= k["high"] and prev["low"] <= k["low"]) or \
           (k["high"] >= prev["high"] and k["low"] <= prev["low"]):
            # 确定方向：看前一根的方向
            direction = 1
            if len(klines) >= 2:
                direction = 1 if klines[-1]["high"] >= klines[-2]["high"] else -1
            if direction == 1:
                # 向上，取高高，低取较高
                new_high = max(prev["high"], k["high"])
                new_low = max(prev["low"], k["low"])
            else:
                # 向下，取低低，高取较低
                new_high = min(prev["high"], k["high"])
                new_low = min(prev["low"], k["low"])
            prev["high"] = new_high
            prev["low"] = new_low
            prev["end"] = k["end"]
        else:
            klines.append(k)
    return klines


# ============================================================
# 2. 顶底分型
# 第66课：三K线模式
# ============================================================
def find_fractals(klines, min_gap=5):
    """
    在合并后的K线上识别顶分型/底分型。
    顶分型：第2根高点是三根中最高，且低点也最高
    底分型：第2根低点是三根中最低，且高点也最低
    min_gap: 相邻分型间至少间隔的合并K线数
    """
    fractals = []
    n = len(klines)
    if n < 3:
        return fractals
    for i in range(1, n - 1):
        prev, cur, nxt = klines[i - 1], klines[i], klines[i + 1]
        # 顶分型
        if cur["high"] > prev["high"] and cur["high"] > nxt["high"] \
           and cur["low"] > prev["low"] and cur["low"] > nxt["low"]:
            fractals.append({"type": "top", "index": i, "value": cur["high"], "kline": cur})
        # 底分型
        elif cur["low"] < prev["low"] and cur["low"] < nxt["low"] \
             and cur["high"] < prev["high"] and cur["high"] < nxt["high"]:
            fractals.append({"type": "bottom", "index": i, "value": cur["low"], "kline": cur})
    # 过滤：相邻同类分型间需满足间隔
    filtered = []
    for f in fractals:
        if not filtered:
            filtered.append(f)
            continue
        last = filtered[-1]
        if f["type"] == last["type"]:
            # 同类，保留极值
            if (f["type"] == "top" and f["value"] > last["value"]) or \
               (f["type"] == "bottom" and f["value"] < last["value"]):
                filtered[-1] = f
        else:
            if f["index"] - last["index"] >= min_gap:
                filtered.append(f)
    return filtered


# ============================================================
# 3. 笔的划分
# 第67课：两个相邻的相反分型构成一笔
# ============================================================
def find_bi(fractals):
    """
    由分型序列生成笔。
    规则：顶底交替，顶到底为向下笔，底到顶为向上笔。
    """
    bi = []
    if len(fractals) < 2:
        return bi
    for i in range(1, len(fractals)):
        prev, cur = fractals[i - 1], fractals[i]
        if prev["type"] == cur["type"]:
            continue  # 同类跳过（理论上find_fractals已过滤）
        direction = "down" if prev["type"] == "top" else "up"
        bi.append({
            "direction": direction,
            "start_index": prev["index"],
            "start_value": prev["value"],
            "end_index": cur["index"],
            "end_value": cur["value"],
            "start_kline": prev["kline"],
            "end_kline": cur["kline"],
        })
    return bi


# ============================================================
# 4. 走势中枢
# 第17、18课：至少三段次级别走势（这里用三笔）重叠部分
# ============================================================
def find_zhongshu(bi_list, min_bi=3):
    """
    在笔序列上识别中枢。
    取连续三笔，其重叠区间 [max(低点), min(高点)] 为中枢区间 [ZD, ZG]。
    后续笔若离开后回抽不破 ZG/ZD，可判定中枢破坏。
    返回中枢列表，每个含 start_index, end_index, ZG, ZD, bi_range
    """
    zhongshu = []
    n = len(bi_list)
    if n < min_bi:
        return zhongshu
    i = 0
    while i <= n - min_bi:
        # 取三笔 a, b, c
        a, b, c = bi_list[i], bi_list[i + 1], bi_list[i + 2]
        # 三笔的高低点
        highs = [a["start_value"], a["end_value"], b["start_value"], b["end_value"],
                 c["start_value"], c["end_value"]]
        # 重叠区间：三笔重叠部分
        # 第17课：中枢区间 = [max(次低点), min(次高点)]
        # 简化：取三笔各自的高低点重叠
        seg_highs = [
            max(a["start_value"], a["end_value"]),
            max(b["start_value"], b["end_value"]),
            max(c["start_value"], c["end_value"]),
        ]
        seg_lows = [
            min(a["start_value"], a["end_value"]),
            min(b["start_value"], b["end_value"]),
            min(c["start_value"], c["end_value"]),
        ]
        ZG = min(seg_highs)  # 中枢上沿
        ZD = max(seg_lows)   # 中枢下沿
        if ZG <= ZD:
            # 无重叠，不构成中枢
            i += 1
            continue
        # 中枢成立，向后延伸：检查后续笔是否回到中枢内
        end_idx = i + 2
        while end_idx + 1 < n:
            nxt = bi_list[end_idx + 1]
            # 若笔的区间与中枢 [ZD,ZG] 有重叠，中枢延续
            nxt_high = max(nxt["start_value"], nxt["end_value"])
            nxt_low = min(nxt["start_value"], nxt["end_value"])
            if nxt_low <= ZG and nxt_high >= ZD:
                end_idx += 1  # 延续
            else:
                break  # 离开中枢
        zhongshu.append({
            "start_index": i,
            "end_index": end_idx,
            "ZG": ZG,
            "ZD": ZD,
            "ZZ": (ZG + ZD) / 2,  # 中枢中轴
            "bi_list": bi_list[i:end_idx + 1],
        })
        i = end_idx + 1
    return zhongshu


# ============================================================
# 5. MACD 背驰判断
# 第24、26、27课：用MACD辅助判断趋势力度减弱
# ============================================================
def calc_macd(df, fast=12, slow=26, signal=9):
    """计算MACD，返回 df 附加列 macd, macd_signal, macd_hist"""
    close = df["close"].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd = (dif - dea) * 2
    df = df.copy()
    df["dif"] = dif
    df["dea"] = dea
    df["macd"] = macd
    df["macd_hist"] = macd
    return df


def check_divergence(df, bi_list, lookback=2):
    """
    判断最近一段向下笔是否对前一段向下笔构成背驰。
    背驰定义（第26课）：同向趋势中，后一段走势力度 < 前一段。
    力度用 MACD 红绿柱面积近似。
    返回 (is_divergence, detail)
    """
    if len(bi_list) < 3:
        return False, {"reason": "笔数不足"}
    # 找最近的向下笔
    down_bi = [b for b in bi_list if b["direction"] == "down"]
    if len(down_bi) < 2:
        return False, {"reason": "向下笔不足2段"}
    # 取最近 lookback 段向下笔对比
    recent = down_bi[-lookback:] if len(down_bi) >= lookback else down_bi
    if len(recent) < 2:
        return False, {"reason": "可对比向下笔不足"}
    prev_d, curr_d = recent[-2], recent[-1]
    # 价格创新低（向下笔延续）
    price_lower = curr_d["end_value"] < prev_d["end_value"]
    if not price_lower:
        return False, {"reason": "价格未创新低，非背驰结构"}
    # 计算 MACD 柱面积（用dif差值近似力度）
    try:
        prev_area = _macd_area(df, prev_d)
        curr_area = _macd_area(df, curr_d)
    except Exception:
        return False, {"reason": "MACD计算异常"}
    # 背驰：价格新低但力度（面积）减小
    is_div = curr_area < prev_area and curr_area > 0
    return is_div, {
        "prev_low": prev_d["end_value"],
        "curr_low": curr_d["end_value"],
        "prev_area": round(prev_area, 4),
        "curr_area": round(curr_area, 4),
        "price_lower": price_lower,
    }


def _macd_area(df, bi):
    """计算一笔区间内 MACD 柱的面积（绝对值累计）"""
    # 用合并K线的 begin/end 映射回原始 df 行
    start = bi["start_kline"]["begin"]
    end = bi["end_kline"]["end"]
    seg = df.loc[start:end]
    if "macd" not in seg.columns or len(seg) == 0:
        return 0.0
    return float(np.abs(seg["macd"].values).sum())


def check_divergence_up(df, bi_list, lookback=2):
    """
    判断最近一段向上笔是否对前一段向上笔构成背驰（上涨背驰，用于第一类卖点）。
    背驰定义：同向趋势中，后一段走势力度 < 前一段。
    力度用 MACD 红绿柱面积近似。
    返回 (is_divergence, detail)
    """
    if len(bi_list) < 3:
        return False, {"reason": "笔数不足"}
    # 找最近的向上笔
    up_bi = [b for b in bi_list if b["direction"] == "up"]
    if len(up_bi) < 2:
        return False, {"reason": "向上笔不足2段"}
    recent = up_bi[-lookback:] if len(up_bi) >= lookback else up_bi
    if len(recent) < 2:
        return False, {"reason": "可对比向上笔不足"}
    prev_u, curr_u = recent[-2], recent[-1]
    # 价格创新高（上涨趋势延续）
    price_higher = curr_u["end_value"] > prev_u["end_value"]
    if not price_higher:
        return False, {"reason": "价格未创新高，非上涨背驰结构"}
    try:
        prev_area = _macd_area(df, prev_u)
        curr_area = _macd_area(df, curr_u)
    except Exception:
        return False, {"reason": "MACD计算异常"}
    # 上涨背驰：价格新高但力度（面积）减小
    is_div = curr_area < prev_area and curr_area > 0
    return is_div, {
        "prev_high": prev_u["end_value"],
        "curr_high": curr_u["end_value"],
        "prev_area": round(prev_area, 4),
        "curr_area": round(curr_area, 4),
        "price_higher": price_higher,
    }


# ============================================================
# 6. 完整解析入口
# ============================================================
def analyze(df, params):
    """
    对一只股票的K线数据做完整缠论解析。
    返回 dict: {klines, fractals, bi, zhongshu, divergence, last_price, macd_df}
    """
    p = params
    # 1. K线合并
    klines = merge_klines(df) if p["merge_kline"] else [
        {"high": r["high"], "low": r["low"], "open": r["open"], "close": r["close"],
         "begin": i, "end": i} for i, r in df.iterrows()
    ]
    # 2. 分型
    fractals = find_fractals(klines, min_gap=p["min_klines_between_fractals"])
    # 3. 笔
    bi = find_bi(fractals)
    # 4. 中枢
    zhongshu = find_zhongshu(bi, min_bi=p["min_zigzag_for_zhongshu"])
    # 5. MACD + 背驰
    macd_df = calc_macd(df, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    is_div, div_detail = check_divergence(macd_df, bi, p["divergence_lookback"])
    return {
        "klines": klines,
        "fractals": fractals,
        "bi": bi,
        "zhongshu": zhongshu,
        "divergence": is_div,
        "divergence_detail": div_detail,
        "last_price": float(df["close"].iloc[-1]),
        "macd_df": macd_df,
    }
