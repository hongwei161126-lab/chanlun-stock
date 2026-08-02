"""
登录鉴权模块：
  - 首次使用：前端引导"设置密码"（只设一次），保存 SHA-256 hash 到 settings 表
  - 登录：POST 明文密码，后端比对hash，正确后签发 7天 有效期的随机 token
  - 鉴权：所有 /api/* 请求（除 /api/auth/*）必须带 Authorization: Bearer <token>
  - 退出登录：token删除
密码哈希用 hashlib.sha256（避免额外依赖bcrypt，alwaysdata/任何平台都能用）
token用64位随机hex字符串，保存在settings表（简单单用户场景，不做多用户）
"""
import os
import hashlib
import secrets
import time
from functools import wraps
from flask import request, jsonify

import trading


_PWD_HASH_KEY = "auth_pwd_hash"
_TOKEN_KEY = "auth_token"
_TOKEN_CREATED_KEY = "auth_token_created"
_TOKEN_TTL = 7 * 24 * 3600  # 7天


def _hash(pwd: str) -> str:
    """SHA-256哈希密码（加盐）"""
    salt = "chanlun-stock-salt-v1::"
    return hashlib.sha256((salt + pwd).encode("utf-8")).hexdigest()


def is_password_set() -> bool:
    """是否已设置密码"""
    return bool(trading.get_setting(_PWD_HASH_KEY, ""))


def set_password(new_pwd: str) -> None:
    """设置/修改密码，同时失效旧token"""
    if not new_pwd or len(new_pwd) < 4:
        raise ValueError("密码至少4位")
    trading.set_setting(_PWD_HASH_KEY, _hash(new_pwd))
    # 重置token
    trading.set_setting(_TOKEN_KEY, "")
    trading.set_setting(_TOKEN_CREATED_KEY, "")


def check_password(pwd: str) -> bool:
    """校验密码是否正确"""
    expected = trading.get_setting(_PWD_HASH_KEY, "")
    if not expected:
        return False
    return secrets.compare_digest(expected, _hash(pwd))


def _gen_token() -> str:
    return secrets.token_hex(32)


def issue_token() -> str:
    """密码正确后调用，签发新token"""
    tok = _gen_token()
    trading.set_setting(_TOKEN_KEY, tok)
    trading.set_setting(_TOKEN_CREATED_KEY, str(int(time.time())))
    return tok


def revoke_token() -> None:
    """退出登录，失效token"""
    trading.set_setting(_TOKEN_KEY, "")
    trading.set_setting(_TOKEN_CREATED_KEY, "")


def _valid_token(tok: str) -> bool:
    if not tok:
        return False
    expected = trading.get_setting(_TOKEN_KEY, "")
    if not expected or not secrets.compare_digest(expected, tok):
        return False
    created = trading.get_setting(_TOKEN_CREATED_KEY, "0")
    try:
        ct = int(created)
    except (TypeError, ValueError):
        return False
    if time.time() - ct > _TOKEN_TTL:
        # 过期
        revoke_token()
        return False
    return True


def require_auth(view_func):
    """
    装饰器：API必须带合法 Authorization: Bearer <token> 头
    例外：/api/auth/* 不鉴权（登录/设置密码接口本身）
    如果未设置密码（首次启动），则所有API都放行，前端会显示"请设置密码"引导页
    """
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # 未设置密码时放行（首次使用引导设置）
        if not is_password_set():
            return view_func(*args, **kwargs)
        # auth相关接口不鉴权（登录时还没token）
        if request.path.startswith("/api/auth/"):
            return view_func(*args, **kwargs)
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            tok = header[7:].strip()
        else:
            tok = ""
        # 兼容查询参数（方便curl调试）: ?token=xxx
        if not tok:
            tok = request.args.get("token", "")
        if not _valid_token(tok):
            return jsonify({"error": "未登录或登录已过期，请重新登录", "need_login": True}), 401
        return view_func(*args, **kwargs)
    return wrapper
