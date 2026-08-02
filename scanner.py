"""
全市场缠论扫描器
多线程并发扫描沪深A股日线买点，带进度跟踪和结果缓存。
扫描结果持久化到文件，防止gunicorn worker重启导致缓存丢失。
"""
import os
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from data_fetcher import fetch_kline, fetch_all_stocks
from strategy import analyze_stock

# 持久化缓存文件路径
_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_cache.json")

# 扫描状态
_state_lock = threading.Lock()
_state = {
    "status": "idle",        # idle | scanning | done | error
    "total": 0,
    "scanned": 0,
    "hits": 0,
    "errors": 0,
    "started_at": 0.0,
    "finished_at": 0.0,
    "msg": "",
}

# 扫描结果缓存 [{symbol, name, price, buy_types, grade, detail}, ...]
_cache_lock = threading.Lock()
_cache = {
    "hits": [],
    "updated_at": 0.0,
}

# 启动时从文件加载缓存
try:
    if os.path.exists(_CACHE_FILE):
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
            _cache["hits"] = saved.get("hits", [])
            _cache["updated_at"] = saved.get("updated_at", 0.0)
        # 同步状态为done（worker重启后恢复）
        with _state_lock:
            _state["status"] = "done"
            _state["hits"] = len(_cache["hits"])
            _state["msg"] = f"从缓存恢复{len(_cache['hits'])}只命中"
            _state["finished_at"] = _cache["updated_at"]
except Exception:
    pass

# 缓存有效期（秒）：2小时
CACHE_TTL = 7200
# 扫描并发线程数（海外服务器到国内延迟高，降低并发避免超时）
MAX_WORKERS = 4
# 每只扫描拉取的K线根数（日线）
SCAN_KLINE_COUNT = 120


def get_status():
    """获取当前扫描状态"""
    with _state_lock:
        s = dict(_state)
    with _cache_lock:
        s["cached_hits"] = len(_cache["hits"])
        s["cache_age"] = round(time.time() - _cache["updated_at"], 0) if _cache["updated_at"] else 0
    return s


def get_hits():
    """获取缓存的扫描命中结果（已按信号强度排序）"""
    with _cache_lock:
        if _cache["hits"]:
            return [dict(h) for h in _cache["hits"]]
    # 内存缓存为空，尝试从文件恢复
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                hits = saved.get("hits", [])
                if hits:
                    return [dict(h) for h in hits]
    except Exception:
        pass
    return []


def is_scanning():
    with _state_lock:
        return _state["status"] == "scanning"


def _calc_score(buy_points, analysis):
    """
    综合评分（0-100），依据缠论结构强度：
      - 买点类型权重（40分）：一买40 > 三买32 > 二买28
      - 背驰强度（25分）：面积比<0.3得满分，越接近1分越低
      - 走势完整度（20分）：中枢数≥3满分，2个15分，1个8分
      - 多买点共振（15分）：同时命中2类买点+10，3类+15
    返回 (score, score_detail)
    """
    types = [b["type"] for b in buy_points]
    # 1. 买点类型权重（取最高权重）
    type_weight = {"第一类买点": 40, "第三类买点": 32, "第二类买点": 28}
    type_score = max(type_weight.get(t, 20) for t in types)

    # 2. 背驰强度
    div_detail = analysis.get("divergence_detail")
    div_score = 0
    div_ratio = 1.0
    if isinstance(div_detail, dict) and div_detail.get("prev_area", 0) > 0:
        div_ratio = div_detail["curr_area"] / div_detail["prev_area"]
        # 面积比<0.3满分25，0.3-0.6得18，0.6-0.8得10，>0.8得5
        if div_ratio < 0.3:
            div_score = 25
        elif div_ratio < 0.6:
            div_score = 18
        elif div_ratio < 0.8:
            div_score = 10
        else:
            div_score = 5
    elif analysis.get("divergence"):
        div_score = 12  # 有背驰但无面积数据

    # 3. 走势完整度（中枢数量）
    zs_count = len(analysis.get("zhongshu", []))
    if zs_count >= 3:
        zs_score = 20
    elif zs_count == 2:
        zs_score = 15
    elif zs_count == 1:
        zs_score = 8
    else:
        zs_score = 0

    # 4. 多买点共振
    unique_types = set(types)
    if len(unique_types) >= 3:
        resonance_score = 15
    elif len(unique_types) == 2:
        resonance_score = 10
    else:
        resonance_score = 0

    total = type_score + div_score + zs_score + resonance_score
    detail = (f"买点{type_score}+背驰{div_score}(比{div_ratio:.0%})"
              f"+中枢{zs_score}({zs_count}个)+共振{resonance_score}")
    return total, detail


def _scan_one(stock, params):
    """
    扫描单只股票
    Returns:
        (hit_dict, error_flag) 或 (None, 0)
        hit_dict: 命中时返回推荐对象；error_flag: 0=成功(有无信号均可)，1=异常(网络/解析失败)
    """
    symbol = stock["symbol"]
    name = stock["name"]
    try:
        df = fetch_kline(symbol, level="daily", count=SCAN_KLINE_COUNT)
        if len(df) < 30:
            return None, 0  # 数据不足，非错误
        analysis, buy_points = analyze_stock(df, params)
        if not buy_points:
            return None, 0  # 无买点，非错误
        price = round(float(df["close"].iloc[-1]), 3)
        types = [b["type"] for b in buy_points]
        # 保存每个买点的历史买入价，用于后续现价合理性过滤
        buy_point_prices = []  # [(type_name, buy_price), ...]
        for b in buy_points:
            try:
                bp = round(float(b["price"]), 3)
            except Exception:
                continue
            buy_point_prices.append((b["type"], bp))
        # 综合评分
        score, score_detail = _calc_score(buy_points, analysis)
        # 评级（兼容旧字段）：按分数分档
        if score >= 80:
            grade = 5
        elif score >= 65:
            grade = 4
        elif score >= 50:
            grade = 3
        elif score >= 35:
            grade = 2
        else:
            grade = 1
        detail = "；".join(f"{b['type']}@{b['price']:.2f}" for b in buy_points)
        return {
            "symbol": symbol,
            "name": name,
            "price": price,
            "buy_types": types,
            "buy_point_prices": buy_point_prices,
            "grade": grade,
            "score": score,
            "score_detail": score_detail,
            "detail": detail,
        }, 0
    except Exception:
        # 数据获取/解析异常，错误计数+1
        return None, 1


def start_scan():
    """启动后台全市场扫描（非阻塞）。若已在扫描则返回False。"""
    with _state_lock:
        if _state["status"] == "scanning":
            return False, "扫描进行中"
        _state.update({
            "status": "scanning", "total": 0, "scanned": 0,
            "hits": 0, "errors": 0, "started_at": time.time(),
            "finished_at": 0.0, "msg": "正在获取股票列表...",
        })
    t = threading.Thread(target=_scan_worker, daemon=True)
    t.start()
    return True, "扫描已启动"


def _scan_worker():
    """后台扫描工作线程"""
    params = config.CHANLUN_PARAMS
    try:
        stocks = fetch_all_stocks()
    except Exception as e:
        with _state_lock:
            _state.update({"status": "error", "msg": f"获取股票列表失败: {e}"})
        return

    total = len(stocks)
    with _state_lock:
        _state["total"] = total
        _state["msg"] = f"开始扫描 {total} 只股票..."

    hits = []
    scanned = 0
    errors = 0
    hit_lock = threading.Lock()

    def _done(future):
        nonlocal scanned, errors
        try:
            result, err = future.result()
        except Exception:
            result, err = None, 1  # future自身异常(如取消)也计为错误
        scanned += 1
        errors += err
        if result is not None:
            with hit_lock:
                hits.append(result)
        with _state_lock:
            _state["scanned"] = scanned
            _state["hits"] = len(hits)
            _state["errors"] = errors
            if scanned % 50 == 0:
                _state["msg"] = f"已扫描 {scanned}/{total}，命中 {len(hits)}，失败 {errors}"

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_scan_one, s, params) for s in stocks]
        for f in as_completed(futures):
            _done(f)

    # 按综合评分降序排序（同分按代码升序）
    hits.sort(key=lambda x: (-x["score"], x["symbol"]))

    with _cache_lock:
        _cache["hits"] = hits
        _cache["updated_at"] = time.time()
        # 持久化到文件，防止worker重启丢失
        try:
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"hits": hits, "updated_at": _cache["updated_at"]}, f, ensure_ascii=False)
        except Exception:
            pass
    with _state_lock:
        _state.update({
            "status": "done",
            "finished_at": time.time(),
            "msg": f"扫描完成：{total}只，命中{len(hits)}只，失败{errors}只",
            "errors": errors,
        })
