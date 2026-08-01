"""
缠论选股主程序
策略：日线 + 30分钟 联动
  1. 日线级别扫描三类买点（定方向）
  2. 30分钟级别扫描三类买点（找精确买点）
  3. 日线出现买点 = 中线机会；日线+30分钟同时出现买点 = 强信号

运行：python selector.py
"""
import pandas as pd
import config
from data_fetcher import fetch_kline
from strategy import analyze_stock


def scan_one(symbol, params):
    """扫描单只股票，返回日线与30分钟的买点"""
    out = {"symbol": symbol, "daily": [], "min30": [], "daily_price": None, "min30_price": None, "error": None}
    try:
        # 日线
        df_d = fetch_kline(symbol, level="daily", count=config.DATA_PARAMS["daily_count"])
        _, buy_d = analyze_stock(df_d, params)
        out["daily"] = buy_d
        out["daily_price"] = float(df_d["close"].iloc[-1])
    except Exception as e:
        out["error"] = f"日线: {e}"
        return out
    try:
        # 30分钟
        df_m = fetch_kline(symbol, level="30min", count=config.DATA_PARAMS["min30_count"])
        _, buy_m = analyze_stock(df_m, params)
        out["min30"] = buy_m
        out["min30_price"] = float(df_m["close"].iloc[-1])
    except Exception as e:
        # 30分钟失败不致命，仅记录
        out["error"] = (out["error"] + " | " if out["error"] else "") + f"30分钟: {e}"
    return out


def grade(result):
    """给单只股票的扫描结果打分/分级"""
    daily_types = {b["type"] for b in result["daily"]}
    min30_types = {b["type"] for b in result["min30"]}
    # 共振：日线与30分钟同时出现买点
    common = daily_types & min30_types
    if common:
        return "★★★ 强共振", ", ".join(common)
    if daily_types:
        return "★★ 日线信号", ", ".join(daily_types)
    if min30_types:
        return "★ 30分钟信号", ", ".join(min30_types)
    return "—", ""


def main():
    params = config.CHANLUN_PARAMS
    pool = config.STOCK_POOL
    print(f"开始缠论选股扫描，股票池 {len(pool)} 只，日线+30分钟联动")
    print("=" * 80)

    rows = []
    for i, symbol in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {symbol} ...", end=" ", flush=True)
        r = scan_one(symbol, params)
        if r["error"] and not r["daily"] and not r["min30"]:
            print(f"失败: {r['error']}")
            rows.append({"代码": symbol, "评级": "错误", "买点类型": "", "日线价": "", "30分钟价": "", "详情": r["error"]})
            continue
        grade_str, types_str = grade(r)
        # 收集买点详情
        details = []
        for b in r["daily"]:
            details.append(f"日线-{b['type']}@{b['price']:.2f}({b['detail']})")
        for b in r["min30"]:
            details.append(f"30分-{b['type']}@{b['price']:.2f}({b['detail']})")
        detail_str = " | ".join(details) if details else "无信号"
        print(f"{grade_str} {types_str}")
        rows.append({
            "代码": symbol,
            "评级": grade_str,
            "买点类型": types_str or "无",
            "日线价": round(r["daily_price"], 2) if r["daily_price"] else "",
            "30分钟价": round(r["min30_price"], 2) if r["min30_price"] else "",
            "详情": detail_str,
        })

    # 输出结果
    df = pd.DataFrame(rows)
    # 按评级排序：强共振 > 日线 > 30分钟 > 无 > 错误
    order = {"★★★ 强共振": 0, "★★ 日线信号": 1, "★ 30分钟信号": 2, "—": 3, "错误": 4}
    df["_o"] = df["评级"].map(lambda x: order.get(x, 9))
    df = df.sort_values("_o").drop(columns="_o").reset_index(drop=True)

    df.to_csv(config.OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print("=" * 80)
    print(f"扫描完成，结果已保存至 {config.OUTPUT_FILE}")
    print()
    # 打印命中信号的股票
    hit = df[df["评级"].isin(["★★★ 强共振", "★★ 日线信号", "★ 30分钟信号"])]
    if len(hit):
        print(f"命中买点信号的股票（{len(hit)} 只）:")
        for _, row in hit.iterrows():
            print(f"  {row['代码']}  {row['评级']}  {row['买点类型']}")
            print(f"      {row['详情']}")
    else:
        print("当前无股票命中买点信号。")


if __name__ == "__main__":
    main()
