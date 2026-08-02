# 缠论选股策略配置

# 股票池（小样本测试：10-20只，覆盖不同行业的热门标的）
STOCK_POOL = [
    "600519",  # 贵州茅台
    "000858",  # 五粮液
    "601318",  # 中国平安
    "600036",  # 招商银行
    "000333",  # 美的集团
    "600276",  # 恒瑞医药
    "601012",  # 隆基绿能
    "300750",  # 宁德时代
    "601899",  # 紫金矿业
    "002594",  # 比亚迪
    "600900",  # 长江电力
    "000001",  # 平安银行
    "601398",  # 工商银行
    "600887",  # 伊利股份
    "002475",  # 立讯精密
]

# 联动级别：日线定方向，30分钟找精确买点
BIG_LEVEL = "daily"       # 大级别
SMALL_LEVEL = "30min"     # 小级别

# 缠论参数
CHANLUN_PARAMS = {
    # K线包含处理：第n根与第n-1根比较时的合并规则
    "merge_kline": True,
    # 笔的划分：最少独立K线数（分型间至少N根独立K线，收紧到7）
    "min_klines_between_fractals": 7,
    # 中枢：至少3笔重叠
    "min_zigzag_for_zhongshu": 3,
    # MACD背驰参数
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    # 背驰判断：近期趋势力度对比的回看笔数
    "divergence_lookback": 2,
}

# 选股结果输出
OUTPUT_FILE = "result.csv"

# 数据获取
DATA_PARAMS = {
    # 拉取的K线根数
    "daily_count": 300,
    "min30_count": 240,
    # 数据源：akshare | tdx
    "source": "akshare",
}
