"""
缠论股票APP - Flask后端API
提供：行情查询、缠论分析、买卖推荐、模拟交易、收益统计
启动：python app.py  然后手机/浏览器访问 http://本机IP:5000
"""
import os
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import config
from data_fetcher import fetch_kline, fetch_realtime, fetch_index_realtime, fetch_minute
import strategy
import trading
import scanner
import notifier
import scheduler
import auth
from chanlun import analyze as chanlun_analyze

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# 防止浏览器缓存旧版 app.js / style.css / index.html：
# HTML/JS/CSS 静态文件一律加 no-cache（每次协商验证，但仍可用 304），避免登录功能等新逻辑因为缓存没生效
@app.after_request
def _no_cache_static(resp):
    path = request.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

# ============================================================
# 全局鉴权中间件：所有 /api/* 请求（除 /api/auth/* 和 未设密码时）都要token
# ============================================================
@app.before_request
def _auth_middleware():
    path = request.path
    if not path.startswith("/api/"):
        return  # 静态资源不鉴权
    # 未设置密码 -> 放行（首次使用）
    if not auth.is_password_set():
        return
    # /api/auth/* 接口本身不鉴权
    if path.startswith("/api/auth/"):
        return
    header = request.headers.get("Authorization", "")
    tok = header[7:].strip() if header.startswith("Bearer ") else ""
    if not tok:
        tok = request.args.get("token", "")
    if not auth._valid_token(tok):
        return jsonify({"error": "未登录或登录已过期，请重新登录", "need_login": True}), 401


# 简单股票名称映射（实际可从接口获取）
NAME_MAP = {
    "600519": "贵州茅台", "000858": "五粮液", "601318": "中国平安",
    "600036": "招商银行", "000333": "美的集团", "600276": "恒瑞医药",
    "601012": "隆基绿能", "300750": "宁德时代", "601899": "紫金矿业",
    "002594": "比亚迪", "600900": "长江电力", "000001": "平安银行",
    "601398": "工商银行", "600887": "伊利股份", "002475": "立讯精密",
}


def _name(symbol):
    return NAME_MAP.get(symbol, symbol)


# 买点对应的允许现价上限（追涨空间）：避免现价远超推荐买入价
# 注：策略收紧后要求底分型确认（价格已回升），上限需适当放宽
_BUY_UPPER_LIMIT = {
    "第一类买点": 1.10,  # 一买：下跌转折+底分型确认，允许现价超买点10%
    "第二类买点": 1.10,  # 二买：回调低点，允许+10%
    "第三类买点": 1.12,  # 三买：突破回试，允许+12%
}


def _price_reasonable(hit, cur_price):
    """
    判断现价是否仍处于买点的合理介入区间。
    hit 可能带 buy_point_prices: [(type_name, buy_price), ...]
    兼容旧缓存无此字段时默认放行。
    规则：只要有任意一个买点满足 cur_price <= buy_price * 上限，就算合理。
    """
    bpps = hit.get("buy_point_prices") or []
    if not bpps:
        return True  # 老缓存或无价格信息，默认放行
    if not cur_price or cur_price <= 0:
        return True  # 没拿到实时价时放过去
    for type_name, bp in bpps:
        if not bp or bp <= 0:
            continue
        limit = _BUY_UPPER_LIMIT.get(type_name, 1.06)
        if cur_price <= bp * limit:
            return True
    return False


# ============================================================
# 静态页面
# ============================================================
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ============================================================
# 登录鉴权 API（/api/auth/* 不被中间件拦截）
# ============================================================
@app.route("/api/auth/state", methods=["GET"])
def auth_state():
    """返回当前鉴权状态：是否已设密码、调用方是否已登录（通过token有效性判断）"""
    pwd_set = auth.is_password_set()
    logged_in = False
    if pwd_set:
        header = request.headers.get("Authorization", "")
        tok = header[7:].strip() if header.startswith("Bearer ") else request.args.get("token", "")
        logged_in = auth._valid_token(tok)
    return jsonify({"password_set": pwd_set, "logged_in": logged_in})


@app.route("/api/auth/set-password", methods=["POST"])
def auth_set_password():
    """首次使用或修改密码"""
    data = request.json or {}
    pwd = (data.get("password") or "").strip()
    old = (data.get("old_password") or "").strip()
    try:
        if auth.is_password_set():
            # 改密码需要旧密码验证
            if not auth.check_password(old):
                return jsonify({"ok": False, "msg": "旧密码错误"}), 401
            auth.set_password(pwd)
        else:
            # 首次设置，无需旧密码
            auth.set_password(pwd)
        return jsonify({"ok": True, "msg": "密码设置成功"})
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)}), 400


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """密码登录，成功返回7天有效token"""
    data = request.json or {}
    pwd = (data.get("password") or "").strip()
    if not auth.is_password_set():
        return jsonify({"ok": False, "msg": "请先设置密码"}), 400
    if not auth.check_password(pwd):
        return jsonify({"ok": False, "msg": "密码错误"}), 401
    tok = auth.issue_token()
    return jsonify({"ok": True, "token": tok, "ttl_days": 7})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """退出登录（token失效）"""
    auth.revoke_token()
    return jsonify({"ok": True, "msg": "已退出登录"})


# ============================================================
# 行情接口
# ============================================================
@app.route("/api/index", methods=["GET"])
def index_realtime():
    """大盘指数实时行情"""
    try:
        data = fetch_index_realtime()
        # 格式化成交额
        for d in data:
            amt = d["amount"]
            if amt >= 1e8:
                d["amount_str"] = f"{amt/1e8:.1f}亿"
            elif amt >= 1e4:
                d["amount_str"] = f"{amt/1e4:.0f}万"
            else:
                d["amount_str"] = f"{amt:.0f}"
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/realtime", methods=["GET", "POST"])
def realtime_batch():
    """批量实时行情：GET ?codes=sh600519,sz000858 或 POST {codes:[...]}"""
    if request.method == "GET":
        codes = request.args.get("codes", "").split(",")
        codes = [c.strip() for c in codes if c.strip()]
    else:
        codes = request.json.get("codes", [])
    if not codes:
        return jsonify({"error": "请提供codes"}), 400
    try:
        return jsonify(fetch_realtime(codes))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quote/<symbol>")
def quote(symbol):
    """获取个股实时行情"""
    try:
        prefix = "sh" if symbol.startswith(("6", "9", "5")) else "sz"
        data = fetch_realtime([f"{prefix}{symbol}"])
        if not data:
            return jsonify({"error": "无数据"}), 404
        d = data[0]
        return jsonify({
            "symbol": symbol, "name": d["name"],
            "price": d["price"], "open": d["open"],
            "high": d["high"], "low": d["low"],
            "prev_close": d["prev_close"],
            "change": d["change"],
            "change_pct": d["change_pct"],
            "amount": d["amount"],
            "time": d["time"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/kline/<symbol>")
def kline(symbol):
    """获取K线数据用于绘图，可带 level 参数"""
    level = request.args.get("level", "daily")
    count = int(request.args.get("count", 120))
    try:
        df = fetch_kline(symbol, level=level, count=count)
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": r["date"].strftime("%Y-%m-%d %H:%M" if level != "daily" else "%Y-%m-%d"),
                "open": round(float(r["open"]), 3),
                "high": round(float(r["high"]), 3),
                "low": round(float(r["low"]), 3),
                "close": round(float(r["close"]), 3),
            })
        return jsonify({"symbol": symbol, "name": _name(symbol), "level": level, "klines": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/minute/<symbol>")
def minute(symbol):
    """获取当日分时图数据"""
    try:
        data = fetch_minute(symbol)
        data["name"] = _name(symbol)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 缠论分析与买卖点
# ============================================================
@app.route("/api/analyze/<symbol>")
def analyze(symbol):
    """缠论分析：返回K线+中枢+笔+买卖点标注"""
    level = request.args.get("level", "daily")
    count = int(request.args.get("count", 150))
    try:
        df = fetch_kline(symbol, level=level, count=count)
        analysis, buy_points = strategy.analyze_stock(df, config.CHANLUN_PARAMS)
        # 构建前端绘图数据
        klines = []
        for _, r in df.iterrows():
            klines.append({
                "date": r["date"].strftime("%Y-%m-%d %H:%M" if level != "daily" else "%Y-%m-%d"),
                "open": round(float(r["open"]), 3),
                "high": round(float(r["high"]), 3),
                "low": round(float(r["low"]), 3),
                "close": round(float(r["close"]), 3),
            })
        # 中枢（映射到K线索引区间）
        zhongshu = []
        for zs in analysis["zhongshu"]:
            zhongshu.append({
                "ZG": round(zs["ZG"], 3), "ZD": round(zs["ZD"], 3),
                "ZZ": round(zs["ZZ"], 3),
            })
        # 笔的端点（用于画趋势线）
        bi_points = []
        for b in analysis["bi"]:
            bi_points.append({
                "start_value": round(b["start_value"], 3),
                "end_value": round(b["end_value"], 3),
                "direction": b["direction"],
            })
        return jsonify({
            "symbol": symbol, "name": _name(symbol), "level": level,
            "klines": klines, "zhongshu": zhongshu, "bi": bi_points,
            "buy_points": buy_points,
            "divergence": analysis["divergence"],
            "last_price": round(float(df["close"].iloc[-1]), 3),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/recommend/<symbol>")
def recommend(symbol):
    """买卖推荐：买入点 + 止盈止损"""
    level = request.args.get("level", "daily")
    try:
        df = fetch_kline(symbol, level=level, count=300)
        rec = strategy.full_recommendation(df, config.CHANLUN_PARAMS)
        rec["symbol"] = symbol
        rec["name"] = _name(symbol)
        return jsonify(rec)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# 自选股
# ============================================================
@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    items = trading.get_watchlist()
    # 附加最新价
    result = []
    for it in items:
        try:
            df = fetch_kline(it["symbol"], level="daily", count=2)
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            chg = (last["close"] - prev["close"]) / prev["close"] * 100
            cur_price = round(float(last["close"]), 3)
            buy_price = round(float(it.get("buy_price") or 0), 3)
            pnl_pct = round((cur_price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0
            result.append({
                "symbol": it["symbol"], "name": it["name"] or _name(it["symbol"]),
                "price": cur_price,
                "change_pct": round(chg, 2),
                "buy_price": buy_price,
                "pnl_pct": pnl_pct,
            })
        except Exception:
            result.append({"symbol": it["symbol"], "name": it["name"], "price": 0, "change_pct": 0,
                           "buy_price": round(float(it.get("buy_price") or 0), 3), "pnl_pct": 0})
    return jsonify(result)


@app.route("/api/watchlist", methods=["POST"])
def add_watchlist():
    data = request.json
    symbol = data.get("symbol", "").strip()
    name = data.get("name", "") or _name(symbol)
    buy_price = float(data.get("buy_price", 0) or 0)
    if not symbol:
        return jsonify({"error": "代码不能为空"}), 400
    trading.add_watch(symbol, name, buy_price)
    return jsonify({"ok": True})


@app.route("/api/watchlist/<symbol>/sell", methods=["POST"])
def sell_watchlist(symbol):
    """卖出自选股：记录卖出价，计算盈亏，从自选列表移除"""
    data = request.json or {}
    sell_price = float(data.get("sell_price", 0) or 0)
    name = data.get("name", "") or _name(symbol)
    buy_price = float(data.get("buy_price", 0) or 0)
    if sell_price <= 0:
        return jsonify({"error": "卖出价无效"}), 400
    result = trading.sell_watch(symbol, name, buy_price, sell_price)
    return jsonify(result)


@app.route("/api/watchlist/stats", methods=["GET"])
def watch_stats():
    """自选股胜率/收益率统计"""
    return jsonify(trading.get_watch_stats())


@app.route("/api/watchlist/<symbol>", methods=["DELETE"])
def del_watchlist(symbol):
    trading.remove_watch(symbol)
    return jsonify({"ok": True})


# ============================================================
# 模拟交易
# ============================================================
@app.route("/api/account", methods=["GET"])
def account():
    acct = trading.get_account()
    return jsonify(acct)


@app.route("/api/positions", methods=["GET"])
def positions():
    # 获取持仓并批量取实时价（比单根K线更准确）
    pos = trading.get_positions()
    prices = {}
    if pos:
        codes = [("sh" if s["symbol"].startswith(("6", "9", "5")) else "sz") + s["symbol"] for s in pos]
        try:
            rt = fetch_realtime(codes) or []
            for r in rt:
                sym = r.get("code")
                if sym and r.get("price", 0) > 0:
                    prices[sym] = r["price"]
        except Exception:
            pass
        # 对取不到实时价的，fallback到持仓成本价
        for p in pos:
            if p["symbol"] not in prices:
                prices[p["symbol"]] = p["avg_cost"]
    pos = trading.get_positions(prices)
    return jsonify(pos)


@app.route("/api/trades", methods=["GET"])
def trades():
    limit = int(request.args.get("limit", 100))
    return jsonify(trading.get_trades(limit))


@app.route("/api/trade/buy", methods=["POST"])
def do_buy():
    data = request.json
    symbol = data["symbol"].strip()
    name = _name(symbol)
    price = float(data["price"])
    shares = int(data["shares"])
    strategy_name = data.get("strategy", "手动")
    mode = data.get("mode", "manual")
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")
    reason = data.get("reason", "")
    # 策略周期：daily→long(长期)，30min/60min→short(短期)
    strategy_term = data.get("strategy_term", "long")
    sl = float(stop_loss) if stop_loss else None
    tp = float(take_profit) if take_profit else None
    result = trading.buy(symbol, name, price, shares, strategy_name, mode, sl, tp, reason, strategy_term)
    return jsonify(result)


@app.route("/api/trade/sell", methods=["POST"])
def do_sell():
    data = request.json
    symbol = data["symbol"].strip()
    price = float(data["price"])
    shares = data.get("shares")
    shares = int(shares) if shares else None
    reason = data.get("reason", "")
    result = trading.sell(symbol, price, shares, reason)
    return jsonify(result)


# ============================================================
# 统计与设置
# ============================================================
@app.route("/api/stats", methods=["GET"])
def stats():
    return jsonify(trading.get_stats())


@app.route("/api/reset", methods=["POST"])
def reset_data():
    """清空所有交易数据，重置为初始资金"""
    r = trading.reset_all()
    return jsonify(r)


@app.route("/api/trades/by_term")
def trades_by_term():
    """按策略周期(长期/短期)分组返回交易明细，供前端点击查看"""
    term = request.args.get("term", "long")
    action = request.args.get("action", "")  # buy|sell|空=全部
    limit = int(request.args.get("limit", 100))
    return jsonify(trading.get_trades_by_term(term, action, limit))


@app.route("/api/trades/paired")
def trades_paired():
    """按策略周期返回配对交易（单股单行）：买入价/卖出价/盈亏/收益率"""
    term = request.args.get("term", "long")
    limit = int(request.args.get("limit", 100))
    return jsonify(trading.get_paired_trades(term, limit))


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "GET":
        return jsonify({
            "auto_mode": trading.get_setting("auto_mode", "off"),
            "stop_loss_pct": float(trading.get_setting("stop_loss_pct", "0.08")),
            "take_profit_pct": float(trading.get_setting("take_profit_pct", "0.20")),
        })
    data = request.json
    for k, v in data.items():
        trading.set_setting(k, v)
    return jsonify({"ok": True})


@app.route("/api/auto/run", methods=["POST"])
def auto_run():
    """
    立即执行自动策略（与scheduler/trigger完全一致的逻辑）。
    执行流程：后台线程 → 全市场扫描 → 等扫描完 → 检查卖点+止盈止损卖出 → 评分达标者自动买入。
    周末/节假日也能执行（force=True），用于测试。
    返回兼容前端期望的 count 字段。
    """
    r = scheduler.trigger_now()
    return jsonify({
        "ok": r.get("ok", True),
        "msg": r.get("msg", ""),
        # 后台异步执行，前端改看"msg"提示扫描中/稍后查看
        "count": 0,
        "async": True,
    })


# ============================================================
# 全市场扫描 & 推荐股票池
# ============================================================
@app.route("/api/scan/start", methods=["POST"])
def scan_start():
    """启动全市场缠论扫描（后台非阻塞）"""
    ok, msg = scanner.start_scan()
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/scan/status")
def scan_status():
    """查询扫描进度"""
    return jsonify(scanner.get_status())


@app.route("/api/scan/hits")
def scan_hits():
    """获取扫描命中结果（带实时行情 + 现价合理性过滤）"""
    hits = scanner.get_hits()
    if not hits:
        return jsonify([])
    # 批量获取实时行情补涨跌幅
    codes = []
    for h in hits[:80]:  # 先多拿一点，过滤后保证至少60只
        pre = "sh" if h["symbol"].startswith(("6", "9", "5")) else "sz"
        codes.append(pre + h["symbol"])
    rt_map = {}
    try:
        rt = fetch_realtime(codes)
        if rt:
            for r in rt:
                rt_map[r["code"]] = r
    except Exception:
        pass
    out = []
    for h in hits[:80]:
        r = rt_map.get(h["symbol"], {})
        cur_price = r.get("price") or h.get("price") or 0
        # 关键过滤：现价必须仍处于买点合理区间内（避免追高）
        if not _price_reasonable(h, cur_price):
            continue
        out.append({
            "symbol": h["symbol"],
            "name": h["name"],
            "price": r.get("price", h["price"]),
            "change_pct": r.get("change_pct", 0),
            "buy_types": h["buy_types"],
            "grade": h["grade"],
            "score": h["score"],
            "score_detail": h["score_detail"],
            "detail": h["detail"],
        })
        if len(out) >= 60:
            break
    return jsonify(out)


@app.route("/api/pool", methods=["GET"])
def pool():
    """推荐股票池：优先返回全市场扫描命中（带现价合理性过滤），无缓存则返回样本池"""
    hits = scanner.get_hits()
    if hits:
        # 复用 scan_hits 的逻辑 + 现价合理性过滤
        codes = []
        for h in hits[:80]:  # 先多拿，过滤后保60
            pre = "sh" if h["symbol"].startswith(("6", "9", "5")) else "sz"
            codes.append(pre + h["symbol"])
        rt_map = {}
        try:
            rt = fetch_realtime(codes)
            if rt:
                for r in rt:
                    rt_map[r["code"]] = r
        except Exception:
            pass
        out = []
        for h in hits[:80]:
            r = rt_map.get(h["symbol"], {})
            cur_price = r.get("price") or h.get("price") or 0
            # 关键过滤：现价必须仍处于买点合理区间内（避免追高）
            if not _price_reasonable(h, cur_price):
                continue
            out.append({
                "symbol": h["symbol"],
                "name": h["name"],
                "price": r.get("price", h["price"]),
                "change_pct": r.get("change_pct", 0),
                "buy_types": h["buy_types"],
                "grade": h["grade"],
                "score": h["score"],
                "score_detail": h["score_detail"],
                "detail": h["detail"],
            })
            if len(out) >= 60:
                break
        return jsonify(out)
    # 无扫描缓存，返回空（只显示命中的股票）
    return jsonify([])


# ============================================================
# 自动化 & 推送配置
# ============================================================
@app.route("/api/scheduler/jobs")
def get_jobs():
    """获取定时任务列表"""
    return jsonify(scheduler.get_jobs())


@app.route("/api/scheduler/trigger", methods=["POST"])
def trigger_job():
    """手动触发一次扫描+自动交易"""
    r = scheduler.trigger_now()
    return jsonify(r)


@app.route("/api/notify/config", methods=["GET", "POST"])
def notify_config():
    """获取/保存推送配置"""
    if request.method == "GET":
        return jsonify({
            "email_enabled": trading.get_setting("notify_email_enabled", "off"),
            "smtp_host": trading.get_setting("notify_smtp_host", ""),
            "smtp_port": trading.get_setting("notify_smtp_port", "465"),
            "smtp_user": trading.get_setting("notify_smtp_user", ""),
            "email_to": trading.get_setting("notify_email_to", ""),
            "sct_enabled": trading.get_setting("notify_sct_enabled", "off"),
            "sct_key": trading.get_setting("notify_sct_key", ""),
        })
    data = request.json or {}
    for k in ["notify_email_enabled", "notify_smtp_host", "notify_smtp_port",
              "notify_smtp_user", "notify_smtp_pass", "notify_email_to",
              "notify_sct_enabled", "notify_sct_key"]:
        if k.replace("notify_", "") in data or k in data:
            val = data.get(k.replace("notify_", ""), data.get(k, ""))
            trading.set_setting(k, val)
    return jsonify({"ok": True, "msg": "推送配置已保存"})


@app.route("/api/notify/test", methods=["POST"])
def notify_test():
    """发送测试推送"""
    r = notifier.send("测试推送", "这是一条来自缠论选股APP的测试消息，收到说明配置成功。")
    return jsonify(r)


@app.route("/api/auto/config", methods=["GET", "POST"])
def auto_config():
    """获取/保存自动交易配置"""
    if request.method == "GET":
        return jsonify({
            "auto_mode": trading.get_setting("auto_mode", "off"),
            "buy_score": trading.get_setting("auto_buy_score", "70"),
            "max_positions": trading.get_setting("auto_max_positions", "5"),
            "buy_ratio": trading.get_setting("auto_buy_ratio", "0.18"),
        })
    data = request.json or {}
    for k in ["auto_mode", "buy_score", "max_positions", "buy_ratio"]:
        if k in data:
            trading.set_setting(f"auto_{k}" if not k.startswith("auto_") else k, data[k])
    return jsonify({"ok": True, "msg": "自动交易配置已保存"})


# ============================================================
# 启动
# ============================================================
# 初始化定时任务（gunicorn 和 app.run 都会触发）
scheduler.init_scheduler()

if __name__ == "__main__":
    # 0.0.0.0 让手机同局域网可访问，threaded 支持并发请求
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
