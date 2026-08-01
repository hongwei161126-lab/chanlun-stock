"""
模拟交易引擎
支持两种模式：
  - 半自动：策略推荐买卖点，用户手动确认下单
  - 全自动：策略触发后自动下单（虚拟）
使用 SQLite 持久化持仓、交易记录、账户余额
止盈止损规则（缠论视角）：
  - 止损：跌破买入点对应的中枢下沿 ZD，或买入价下跌 N%
  - 止盈：出现第一类卖点（上涨背驰），或达到目标涨幅
"""
import os
import sqlite3
import json
import time
from datetime import datetime

DB_PATH = "trading.db"
# 初始虚拟资金
INITIAL_CAPITAL = 100000.0

# A股交易费用规则（模拟实盘）
COMMISSION_RATE = 0.00025   # 佣金费率 万2.5
COMMISSION_MIN = 5.0        # 佣金最低5元
STAMP_TAX_RATE = 0.0005     # 印花税 千0.5（卖出单边）
TRANSFER_FEE_RATE = 0.00001 # 过户费 万0.1（沪市双边）


def _calc_fee(amount, action, symbol):
    """计算交易费用。amount=成交金额, action='buy'|'sell', symbol=股票代码"""
    # 佣金（双边，最低5元）
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    fee = commission
    detail = {"commission": round(commission, 2)}
    # 印花税（卖出单边）
    if action == "sell":
        stamp = amount * STAMP_TAX_RATE
        fee += stamp
        detail["stamp_tax"] = round(stamp, 2)
    # 过户费（沪市：6/9开头，双边）
    if symbol.startswith(("6", "9")):
        transfer = amount * TRANSFER_FEE_RATE
        fee += transfer
        detail["transfer_fee"] = round(transfer, 2)
    detail["total"] = round(fee, 2)
    return round(fee, 2), detail


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """初始化数据库表"""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY,
            balance REAL NOT NULL,        -- 可用现金
            initial REAL NOT NULL,        -- 初始资金
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT,
            shares INTEGER NOT NULL,       -- 持仓股数
            avg_cost REAL NOT NULL,        -- 平均成本
            buy_price REAL,                -- 买入价(记录最近)
            stop_loss REAL,                -- 止损价
            take_profit REAL,              -- 止盈价
            strategy TEXT,                 -- 策略来源(如 缠论一买)
            mode TEXT,                     -- auto | manual
            opened_at TEXT,
            UNIQUE(symbol)
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL,          -- buy | sell
            price REAL NOT NULL,
            shares INTEGER NOT NULL,
            amount REAL NOT NULL,
            strategy TEXT,
            mode TEXT,
            reason TEXT,                   -- 交易原因
            pnl REAL DEFAULT 0,            -- 卖出时记录盈亏
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        # 初始化账户
        row = c.execute("SELECT * FROM account WHERE id=1").fetchone()
        if not row:
            c.execute("INSERT INTO account(id,balance,initial,updated_at) VALUES(1,?,?,?)",
                      (INITIAL_CAPITAL, INITIAL_CAPITAL, datetime.now().isoformat()))
        # 默认设置
        row = c.execute("SELECT * FROM settings WHERE key='auto_mode'").fetchone()
        if not row:
            c.execute("INSERT INTO settings(key,value) VALUES('auto_mode','off')")
        # ---- 数据库迁移：为旧表追加 strategy_term 列（长期/短期策略区分） ----
        # strategy_term: 'long'(日线级别) | 'short'(30分/60分级别)
        _ensure_column(c, "positions", "strategy_term", "TEXT DEFAULT 'long'")
        _ensure_column(c, "trades", "strategy_term", "TEXT DEFAULT 'long'")
        # 交易费用列（模拟实盘交税）
        _ensure_column(c, "trades", "fee", "REAL DEFAULT 0")
        c.commit()


def _ensure_column(c, table, column, definition):
    """安全地为已存在的表追加列（若不存在）"""
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ============================================================
# 账户与统计
# ============================================================
def get_account():
    with _conn() as c:
        row = c.execute("SELECT * FROM account WHERE id=1").fetchone()
        if not row:
            init_db()
            row = c.execute("SELECT * FROM account WHERE id=1").fetchone()
        # 计算总资产 = 现金 + 持仓市值
        positions = c.execute("SELECT * FROM positions").fetchall()
        market_value = 0.0
        for p in positions:
            # 市值用最新价（需外部传入，这里先按成本估算）
            market_value += p["shares"] * p["avg_cost"]
        return {
            "balance": row["balance"],
            "initial": row["initial"],
            "market_value": market_value,
            "total_assets": row["balance"] + market_value,
            "total_pnl": row["balance"] + market_value - row["initial"],
            "total_pnl_pct": (row["balance"] + market_value - row["initial"]) / row["initial"] * 100,
            "auto_mode": get_setting("auto_mode") == "on",
        }


def get_setting(key, default=""):
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, str(value)))
        c.commit()


# ============================================================
# 持仓与交易
# ============================================================
def get_positions(latest_prices=None):
    """获取持仓，latest_prices={symbol:price} 用于计算浮动盈亏"""
    latest_prices = latest_prices or {}
    with _conn() as c:
        rows = c.execute("SELECT * FROM positions ORDER BY opened_at DESC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        cur_price = latest_prices.get(r["symbol"], r["avg_cost"])
        d["current_price"] = cur_price
        d["market_value"] = cur_price * r["shares"]
        d["cost"] = r["avg_cost"] * r["shares"]
        d["float_pnl"] = (cur_price - r["avg_cost"]) * r["shares"]
        d["float_pnl_pct"] = (cur_price / r["avg_cost"] - 1) * 100 if r["avg_cost"] else 0
        result.append(d)
    return result


def get_trades(limit=100):
    with _conn() as c:
        rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_trades_by_term(term="long", action="", limit=100):
    """按策略周期查询交易明细。term: long|short, action: buy|sell|空"""
    sql = "SELECT * FROM trades WHERE strategy_term=?"
    params = [term]
    if action in ("buy", "sell"):
        sql += " AND action=?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_paired_trades(term="long", limit=100):
    """
    按策略周期返回配对交易（单股单行）：买入价/卖出价/盈亏/收益率
    逻辑：同一股票，按时间顺序，买入和卖出配对（FIFO）。
    未卖出的持仓只显示买入价。
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM trades WHERE strategy_term=? ORDER BY symbol, id ASC",
            (term,)
        ).fetchall()
    # 按 symbol 分组
    by_symbol = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(dict(r))

    paired = []
    for symbol, trades in by_symbol.items():
        name = trades[0]["name"]
        buy_queue = []  # 待配对的买入
        for t in trades:
            if t["action"] == "buy":
                buy_queue.append(t)
            else:  # sell
                # FIFO 配对
                remaining_shares = t["shares"]
                while remaining_shares > 0 and buy_queue:
                    buy = buy_queue[0]
                    match_shares = min(buy["shares"], remaining_shares)
                    buy_price = buy["price"]
                    sell_price = t["price"]
                    pnl = t["pnl"] * match_shares / t["shares"] if t["shares"] else 0
                    buy_fee = (buy.get("fee") or 0) * match_shares / buy["shares"] if buy["shares"] else 0
                    sell_fee = (t.get("fee") or 0) * match_shares / t["shares"] if t["shares"] else 0
                    pnl_pct = (sell_price / buy_price - 1) * 100 if buy_price else 0
                    paired.append({
                        "symbol": symbol,
                        "name": name,
                        "buy_price": round(buy_price, 3),
                        "sell_price": round(sell_price, 3),
                        "shares": match_shares,
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "fee": round(buy_fee + sell_fee, 2),
                        "strategy": buy["strategy"],
                        "buy_date": (buy["created_at"] or "")[:10],
                        "sell_date": (t["created_at"] or "")[:10],
                        "reason": t["reason"],
                    })
                    remaining_shares -= match_shares
                    buy["shares"] -= match_shares
                    if buy["shares"] <= 0:
                        buy_queue.pop(0)
        # 未卖出的买入（持仓中）
        for buy in buy_queue:
            if buy["shares"] > 0:
                paired.append({
                    "symbol": symbol,
                    "name": name,
                    "buy_price": round(buy["price"], 3),
                    "sell_price": None,
                    "shares": buy["shares"],
                    "pnl": None,
                    "pnl_pct": None,
                    "fee": round(buy.get("fee") or 0, 2),
                    "strategy": buy["strategy"],
                    "buy_date": (buy["created_at"] or "")[:10],
                    "sell_date": "",
                    "reason": "持仓中",
                })
    # 按买入日期倒序
    paired.sort(key=lambda x: x["buy_date"], reverse=True)
    return paired[:limit]


def buy(symbol, name, price, shares, strategy="手动", mode="manual",
        stop_loss=None, take_profit=None, reason="", strategy_term="long"):
    """买入建仓/加仓。strategy_term: 'long'(日线) | 'short'(30分/60分)"""
    amount = price * shares
    fee, fee_detail = _calc_fee(amount, "buy", symbol)
    total_cost = amount + fee  # 实际扣款=成交金额+佣金
    created = datetime.now().isoformat()
    with _conn() as c:
        acct = c.execute("SELECT balance FROM account WHERE id=1").fetchone()
        if acct["balance"] < total_cost:
            return {"ok": False, "msg": f"资金不足，需{total_cost:.2f}(含费{fee:.2f})，可用{acct['balance']:.2f}"}
        # 检查是否已有持仓
        pos = c.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
        # 含费成本均价
        if pos:
            new_shares = pos["shares"] + shares
            new_avg = (pos["avg_cost"] * pos["shares"] + total_cost) / new_shares
            c.execute("""UPDATE positions SET shares=?, avg_cost=?, buy_price=?,
                         stop_loss=COALESCE(?,stop_loss), take_profit=COALESCE(?,take_profit),
                         strategy_term=?
                         WHERE symbol=?""",
                      (new_shares, new_avg, price, stop_loss, take_profit, strategy_term, symbol))
        else:
            avg_cost = total_cost / shares
            c.execute("""INSERT INTO positions(symbol,name,shares,avg_cost,buy_price,
                         stop_loss,take_profit,strategy,mode,strategy_term,opened_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (symbol, name, shares, avg_cost, price, stop_loss, take_profit,
                       strategy, mode, strategy_term, created))
        c.execute("UPDATE account SET balance=balance-?, updated_at=? WHERE id=1",
                  (total_cost, created))
        c.execute("""INSERT INTO trades(symbol,name,action,price,shares,amount,fee,strategy,mode,reason,strategy_term,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (symbol, name, "buy", price, shares, amount, fee, strategy, mode, reason, strategy_term, created))
        c.commit()
    return {"ok": True, "msg": f"买入{symbol} {shares}股@{price:.2f} 费用{fee:.2f}"}


def sell(symbol, price, shares=None, reason=""):
    """卖出，shares=None 表示全部清仓"""
    with _conn() as c:
        pos = c.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
        if not pos:
            return {"ok": False, "msg": f"无{symbol}持仓"}
        sell_shares = pos["shares"] if shares is None else min(shares, pos["shares"])
        amount = price * sell_shares
        fee, fee_detail = _calc_fee(amount, "sell", symbol)
        net_amount = amount - fee  # 实际到账=成交金额-佣金-印花税-过户费
        pnl = net_amount - pos["avg_cost"] * sell_shares  # 净盈亏扣除费用
        created = datetime.now().isoformat()
        # 继承持仓的策略周期（长期/短期）
        term = pos["strategy_term"] if "strategy_term" in pos.keys() else "long"
        new_shares = pos["shares"] - sell_shares
        if new_shares <= 0:
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        else:
            c.execute("UPDATE positions SET shares=? WHERE symbol=?", (new_shares, symbol))
        c.execute("UPDATE account SET balance=balance+?, updated_at=? WHERE id=1",
                  (net_amount, created))
        c.execute("""INSERT INTO trades(symbol,name,action,price,shares,amount,fee,strategy,mode,reason,pnl,strategy_term,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (symbol, pos["name"], "sell", price, sell_shares, amount, fee,
                   pos["strategy"], pos["mode"], reason, pnl, term, created))
        c.commit()
    return {"ok": True, "msg": f"卖出{symbol} {sell_shares}股@{price:.2f} 费用{fee:.2f} 净盈亏{pnl:.2f}", "pnl": pnl}


def check_stop_loss_take_profit(latest_prices):
    """
    检查所有持仓的止盈止损触发情况（全自动模式用）。
    返回触发的信号列表 [{symbol, action, price, reason}]
    """
    signals = []
    with _conn() as c:
        rows = c.execute("SELECT * FROM positions").fetchall()
    for r in rows:
        price = latest_prices.get(r["symbol"])
        if not price:
            continue
        if r["stop_loss"] and price <= r["stop_loss"]:
            signals.append({"symbol": r["symbol"], "action": "sell", "price": price,
                            "reason": f"止损触发(≤{r['stop_loss']:.2f})"})
        elif r["take_profit"] and price >= r["take_profit"]:
            signals.append({"symbol": r["symbol"], "action": "sell", "price": price,
                            "reason": f"止盈触发(≥{r['take_profit']:.2f})"})
    return signals


# ============================================================
# 收益统计
# ============================================================
def get_stats():
    """计算胜率、总盈亏等统计，按策略周期(长期/短期)分别统计"""
    with _conn() as c:
        # 已平仓交易（有pnl的卖出）
        sells = c.execute("SELECT * FROM trades WHERE action='sell' AND pnl IS NOT NULL").fetchall()

    def _calc(rows):
        win = sum(1 for s in rows if s["pnl"] > 0)
        lose = sum(1 for s in rows if s["pnl"] <= 0)
        total_pnl = sum(s["pnl"] for s in rows)
        win_rate = win / len(rows) * 100 if rows else 0
        # 收益率 = 总盈亏 / 初始资金（单策略占用资金的近似）
        pnl_pct = total_pnl / INITIAL_CAPITAL * 100 if rows else 0
        return {
            "total_trades": len(rows),
            "win_count": win,
            "lose_count": lose,
            "win_rate": round(win_rate, 2),
            "realized_pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        }

    # 全部
    overall = _calc(sells)
    # 长期策略（日线）
    long_rows = [s for s in sells if (s["strategy_term"] if "strategy_term" in s.keys() else "long") == "long"]
    # 短期策略（30分/60分）
    short_rows = [s for s in sells if (s["strategy_term"] if "strategy_term" in s.keys() else "long") == "short"]
    return {
        **overall,
        "long": _calc(long_rows),
        "short": _calc(short_rows),
    }


# ============================================================
# 自选股
# ============================================================
def get_watchlist():
    with _conn() as c:
        rows = c.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


def add_watch(symbol, name=""):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO watchlist(symbol,name,added_at) VALUES(?,?,?)",
                  (symbol, name, datetime.now().isoformat()))
        c.commit()
    return {"ok": True}


def remove_watch(symbol):
    with _conn() as c:
        c.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
        c.commit()
    return {"ok": True}


def reset_all(capital=INITIAL_CAPITAL):
    """清空所有交易数据，重置账户到初始资金"""
    with _conn() as c:
        c.execute("DELETE FROM positions")
        c.execute("DELETE FROM trades")
        c.execute("DELETE FROM watchlist")
        c.execute("DELETE FROM settings WHERE key != 'auto_mode'")
        c.execute("UPDATE account SET balance=?, initial=?, updated_at=? WHERE id=1",
                  (capital, capital, datetime.now().isoformat()))
        c.commit()
    return {"ok": True, "msg": f"已清空所有数据，资金重置为{capital:.0f}"}


# 初始化
init_db()
