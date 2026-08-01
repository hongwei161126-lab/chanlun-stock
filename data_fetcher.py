"""
数据获取层
使用腾讯财经K线接口（反爬宽松，数据稳定）直连访问。
东方财富接口获取全市场股票列表。
支持日线(daily)与30分钟(30min)两个级别。

反爬策略：
  - UA 轮换：每次请求从 UA 池随机选取，避免固定特征
  - 抖动间隔：请求间隔 = 基础间隔 + 随机抖动，避免均匀节奏
  - 指数退避重试：失败时 0.5s → 1s → 2s 递增重试
  - 限流熔断：连续501/403时暂停5s冷却，保护后续请求
"""
import os
# 国内接口直连，不走代理（代理对国内域名不稳定）
os.environ["NO_PROXY"] = "*"

import random
import time
import threading
import requests
import pandas as pd

# UA 池（主流桌面浏览器）
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
]
_TIMEOUT = 20
# 腾讯接口 session（直连）
_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.headers.update({"Referer": "https://gu.qq.com/"})
# 东方财富接口 session（直连）
_EM_SESSION = requests.Session()
_EM_SESSION.trust_env = False
_EM_SESSION.headers.update({"Referer": "https://quote.eastmoney.com/"})
# 每线程独立的最近请求时间，避免全局锁瓶颈
_tls = threading.local()
_TLS_GAP = 0.35  # 单线程基础间隔（秒）
_TLS_JITTER = 0.18  # 随机抖动上限（秒），实际间隔 = _TLS_GAP + random*JITTER

# 限流熔断：全局连续失败计数
_breaker_lock = threading.Lock()
_breaker = {"fails": 0, "cooldown_until": 0.0}
_BREAKER_THRESHOLD = 3       # 连续3次限流触发熔断
_BREAKER_COOLDOWN = 5.0      # 熔断冷却5秒


def _throttle():
    """请求间最小间隔（按线程独立计时）+ 随机抖动，避免均匀节奏触发限流"""
    now = time.time()
    last = getattr(_tls, "last_ts", 0.0)
    gap = _TLS_GAP + random.random() * _TLS_JITTER
    wait = gap - (now - last)
    if wait > 0:
        time.sleep(wait)
    _tls.last_ts = time.time()


def _check_breaker():
    """熔断检查：若处于冷却期则等待"""
    with _breaker_lock:
        cd = _breaker["cooldown_until"] - time.time()
    if cd > 0:
        time.sleep(cd)


def _mark_fail(status_code):
    """记录失败，连续限流状态码触发熔断"""
    if status_code in (403, 429, 501, 503):
        with _breaker_lock:
            _breaker["fails"] += 1
            if _breaker["fails"] >= _BREAKER_THRESHOLD:
                _breaker["cooldown_until"] = time.time() + _BREAKER_COOLDOWN
                _breaker["fails"] = 0
        return True
    return False


def _mark_success():
    """请求成功，重置失败计数"""
    with _breaker_lock:
        _breaker["fails"] = 0


def _apply_ua(session):
    """每次请求前从UA池随机选取一个UA"""
    session.headers["User-Agent"] = random.choice(_UA_POOL)


def _qqsymbol(symbol):
    """腾讯代码前缀：sh=沪，sz=深"""
    return f"sh{symbol}" if symbol.startswith(("6", "9", "5")) else f"sz{symbol}"


# 腾讯K线类型：日线用 fqkline，分钟线用 mkline
_QQ_DAILY_TYPE = {"daily": "day", "week": "week", "month": "month"}
_QQ_MIN_TYPE = {"30min": "m30", "5min": "m5", "15min": "m15", "60min": "m60"}


def fetch_kline(symbol, level="daily", count=300):
    """
    获取K线数据（优先新浪，回退东方财富，再回退腾讯）。
    symbol: 6位代码，如 "600519"
    level: "daily" | "30min" | "60min"
    count: 拉取根数
    返回 DataFrame(date, open, high, low, close)
    """
    # 优先新浪（最稳定，反爬宽松）
    try:
        return _fetch_sina_kline(symbol, level, count)
    except Exception:
        pass
    # 回退东方财富
    try:
        return _fetch_em_kline(symbol, level, count)
    except Exception:
        pass
    # 最后回退腾讯
    if level in _QQ_DAILY_TYPE:
        return _fetch_qq_daily(symbol, level, count)
    elif level in _QQ_MIN_TYPE:
        return _fetch_qq_min(symbol, level, count)
    raise ValueError(f"不支持的级别: {level}")


# 新浪K线级别映射：scale（分钟数），日线=240
_SINA_SCALE = {"daily": 240, "60min": 60, "30min": 30, "15min": 15, "5min": 5}


def _fetch_sina_kline(symbol, level, count):
    """新浪K线接口（反爬宽松，数据稳定）"""
    scale = _SINA_SCALE.get(level)
    if scale is None:
        raise ValueError(f"新浪不支持级别: {level}")
    qs = _qqsymbol(symbol)  # 复用 sh/sz 前缀
    url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var=/CN_MarketDataService.getKLineData?"
           f"symbol={qs}&scale={scale}&datalen={count}")
    _check_breaker()
    _throttle()
    _apply_ua(_EM_SESSION)
    r = _EM_SESSION.get(url, timeout=_TIMEOUT)
    if r.status_code != 200:
        _mark_fail(r.status_code)
        raise RuntimeError(f"新浪K线 HTTP {r.status_code}")
    # 解析 var=([...]) 格式
    txt = r.text
    start = txt.find("([")
    end = txt.rfind("])")
    if start < 0 or end < 0:
        _mark_fail(502)
        raise RuntimeError(f"新浪K线响应格式异常")
    import json
    data = json.loads(txt[start + 1:end + 1])
    if not data:
        raise RuntimeError(f"{symbol} {level} 无数据")
    _mark_success()
    rows = [{"date": d["day"], "open": float(d["open"]), "close": float(d["close"]),
             "high": float(d["high"]), "low": float(d["low"])} for d in data]
    return _to_df(rows, count)


# 东方财富K线级别映射
_EM_KLT = {"daily": 101, "week": 102, "month": 103, "30min": 30, "60min": 60, "15min": 15, "5min": 5}


def _em_secid(symbol):
    """东方财富secid：沪市1.代码，深市0.代码"""
    if symbol.startswith(("6", "9", "5")):
        return f"1.{symbol}"
    return f"0.{symbol}"


def _fetch_em_kline(symbol, level, count):
    """东方财富K线接口（带反爬：UA轮换+抖动+熔断+指数退避）"""
    klt = _EM_KLT.get(level)
    if klt is None:
        raise ValueError(f"东方财富不支持级别: {level}")
    secid = _em_secid(symbol)
    url = (f"http://push2.eastmoney.com/api/qt/stock/kline/get?"
           f"secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt={klt}&fqt=1&end=20500101&lmt={count}")
    last_err = None
    for attempt in range(3):
        _check_breaker()
        _throttle()
        _apply_ua(_EM_SESSION)
        try:
            r = _EM_SESSION.get(url, timeout=_TIMEOUT)
            if r.status_code != 200:
                _mark_fail(r.status_code)
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.5 * (attempt + 1))  # 指数退避
                continue
            klines = r.json().get("data", {}).get("klines", [])
            if not klines:
                raise RuntimeError(f"{symbol} {level} 无数据")
            _mark_success()
            rows = [{"date": k.split(",")[0],
                     "open": float(k.split(",")[1]),
                     "close": float(k.split(",")[2]),
                     "high": float(k.split(",")[3]),
                     "low": float(k.split(",")[4])} for k in klines]
            return _to_df(rows, count)
        except Exception as e:
            last_err = str(e)
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"东方财富获取 {symbol} {level} 失败: {last_err}")


def _fetch_qq_daily(symbol, level, count):
    klt = _QQ_DAILY_TYPE[level]
    qs = _qqsymbol(symbol)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qs},{klt},,,{count},qfq"
    data = _request(url, symbol, level)
    obj = data.get("data", {}).get(qs, {})
    klines = None
    for key in (f"qfq{klt}", klt):
        if key in obj and obj[key]:
            klines = obj[key]
            break
    if not klines:
        raise RuntimeError(f"{symbol} {level} 无数据")
    # 每根: [date, open, close, high, low, volume, ...]
    rows = [{"date": k[0], "open": float(k[1]), "close": float(k[2]),
             "high": float(k[3]), "low": float(k[4])} for k in klines]
    return _to_df(rows, count)


def _fetch_qq_min(symbol, level, count):
    klt = _QQ_MIN_TYPE[level]
    qs = _qqsymbol(symbol)
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={qs},{klt},,,{count},qfq"
    data = _request(url, symbol, level)
    obj = data.get("data", {}).get(qs, {})
    klines = obj.get(klt) or obj.get(f"qfq{klt}")
    if not klines:
        raise RuntimeError(f"{symbol} {level} 无数据")
    # 每根: [time("202607311430"), open, close, high, low, volume, {}, 振幅]
    rows = []
    for k in klines:
        t = k[0]
        # 归一化为可解析日期：202607311430 -> 2026-07-31 14:30
        date_str = f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}" if len(t) == 12 else t
        rows.append({"date": date_str, "open": float(k[1]), "close": float(k[2]),
                     "high": float(k[3]), "low": float(k[4])})
    return _to_df(rows, count)


def _request(url, symbol, level):
    """带反爬的 GET（UA轮换+抖动+熔断+指数退避重试）"""
    last_err = None
    for attempt in range(3):
        _check_breaker()
        _throttle()
        _apply_ua(_SESSION)
        try:
            r = _SESSION.get(url, timeout=_TIMEOUT)
            if r.status_code != 200:
                _mark_fail(r.status_code)
                last_err = f"HTTP {r.status_code}"
                time.sleep(0.5 * (attempt + 1))
                continue
            _mark_success()
            return r.json()
        except Exception as e:
            last_err = str(e)
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"腾讯获取 {symbol} {level} 失败: {last_err}")


def _to_df(rows, count):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) > count:
        df = df.iloc[-count:].reset_index(drop=True)
    return df


# ============================================================
# 实时行情（腾讯 qt 接口，支持大盘指数 + 个股）
# ============================================================
# 大盘指数代码
INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000016": "上证50",
}


def fetch_realtime(codes):
    """
    批量获取实时行情（大盘指数或个股）。
    codes: list[str]，如 ["sh000001","sz399001"] 或 ["sh600519"]
    返回 list[dict]: {code, name, price, prev_close, open, high, low,
                     change, change_pct, amount, time}
    """
    qs = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={qs}"
    for attempt in range(3):
        _throttle()
        try:
            r = _SESSION.get(url, timeout=_TIMEOUT)
            r.encoding = "gbk"
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"实时行情获取失败: {e}")
            time.sleep(1.0 * (attempt + 1))

    results = []
    for line in r.text.strip().split(";"):
        line = line.strip()
        if not line or "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 35:
            continue
        # 提取代码前缀
        prefix = line.split("=")[0].replace("v_", "").strip('"')
        price = float(parts[3])
        prev = float(parts[4])
        chg = (price - prev) / prev * 100 if prev else 0
        results.append({
            "code": parts[2],
            "prefix": prefix,
            "name": parts[1],
            "price": round(price, 3),
            "prev_close": round(prev, 3),
            "open": round(float(parts[5]), 3) if parts[5] else 0,
            "high": round(float(parts[33]), 3) if len(parts) > 33 and parts[33] else 0,
            "low": round(float(parts[34]), 3) if len(parts) > 34 and parts[34] else 0,
            "change": round(price - prev, 3),
            "change_pct": round(chg, 2),
            "amount": float(parts[37]) if len(parts) > 37 and parts[37] else 0,
            "time": parts[30] if len(parts) > 30 else "",
            "is_index": prefix.startswith(("sh000", "sz399")),
        })
    return results


def fetch_index_realtime():
    """获取主要大盘指数实时行情"""
    return fetch_realtime(list(INDEX_CODES.keys()))


# ============================================================
# 分时图（当日逐分钟走势）
# ============================================================
def fetch_minute(symbol):
    """
    获取当日分时图数据。
    返回 {date, prev_close, points:[{time, price, avg_price, volume, amount}]}
    """
    # 指数代码特殊处理（上证指数000001需用sh前缀）
    if symbol in ("000001", "000300", "000016"):
        code = f"sh{symbol}"
    elif symbol.startswith("399"):
        code = f"sz{symbol}"
    else:
        prefix = "sh" if symbol.startswith(("6", "9", "5")) else "sz"
        code = f"{prefix}{symbol}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    for attempt in range(3):
        _throttle()
        try:
            r = _SESSION.get(url, timeout=20)
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"分时数据获取失败: {e}")
            time.sleep(1.0 * (attempt + 1))

    data = r.json()
    inner = data["data"][code]
    minutes = inner["data"]["data"]   # [[time, price, cum_vol, cum_amount], ...]
    date = inner["data"]["date"]
    prev_close = float(inner["qt"][code][4])

    points = []
    for m in minutes:
        # m 可能是 "0930 1330.03 1191 158406573.03" 字符串，或 ["0930", 1330.03, ...] 数组
        if isinstance(m, str):
            parts = m.split(" ")
        else:
            parts = m
        t = str(parts[0])
        price = float(parts[1])
        vol = float(parts[2]) if len(parts) > 2 and parts[2] else 0
        amt = float(parts[3]) if len(parts) > 3 and parts[3] else 0
        # 均价 = 累计成交额 / (累计成交量*100)，vol单位是"手"
        avg = amt / (vol * 100) if vol > 0 else price
        points.append({
            "time": f"{t[:2]}:{t[2:]}" if len(t) == 4 else t,
            "price": round(price, 3),
            "avg_price": round(avg, 3),
            "volume": vol,
            "amount": amt,
        })
    return {
        "symbol": symbol,
        "date": f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date,
        "prev_close": round(prev_close, 3),
        "points": points,
    }


# ============================================================
# 全市场股票列表（新浪财经接口，sh_a + sz_a 分页获取）
# ============================================================
_SINA_NODE_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/"
                  "json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&node={node}")
_SINA_COUNT_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/"
                   "json_v2.php/Market_Center.getHQNodeStockCount?node={node}")


def _fetch_sina_page(node, page):
    """获取新浪单页股票数据"""
    url = _SINA_NODE_URL.format(node=node, page=page)
    for attempt in range(3):
        try:
            r = _EM_SESSION.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                return []
            time.sleep(0.4)


def _sina_count(node):
    """获取新浪节点股票总数"""
    url = _SINA_COUNT_URL.format(node=node)
    try:
        r = _EM_SESSION.get(url, timeout=10)
        return int(r.text.strip('"'))
    except Exception:
        return 0


def fetch_all_stocks():
    """
    获取沪深A股全列表（排除ST、退市、北交所）。
    使用新浪财经 sh_a + sz_a 节点并发分页获取。
    返回 list[dict]: {symbol, name}
    """
    from concurrent.futures import ThreadPoolExecutor

    nodes = ["sh_a", "sz_a"]
    all_items = []
    for node in nodes:
        total = _sina_count(node)
        if total <= 0:
            continue
        pages = (total + 99) // 100
        # 并发获取该节点的所有页
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda p: _fetch_sina_page(node, p), range(1, pages + 1)))
        for page_data in results:
            if page_data:
                all_items.extend(page_data)

    out = []
    seen = set()
    for it in all_items:
        code = str(it.get("code", "")).strip()
        name = str(it.get("name", "")).strip()
        if not code or len(code) != 6 or code in seen:
            continue
        # 排除ST、*ST、退市
        if "ST" in name or "退" in name:
            continue
        # 仅保留沪深主板/创业板/科创板代码
        if not code.startswith(("60", "00", "30", "68")):
            continue
        seen.add(code)
        out.append({"symbol": code, "name": name})
    return out
