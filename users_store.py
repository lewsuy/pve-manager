# -*- coding: utf-8 -*-
"""
用户存储:账号密码保存在 users.json,密码只存哈希(werkzeug 的 PBKDF2),不存明文。
app.py(登录校验)和 manage_users.py(账号管理 CLI)都依赖这个模块,保证读写逻辑一致。
"""
import json
import os
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_PATH = os.path.join(BASE_DIR, "users.json")


def load_users():
    """返回 {username: {"password_hash": ..., "created_at": ...}}"""
    if not os.path.exists(USERS_PATH):
        return {}
    try:
        with open(USERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_users(users):
    """原子写入,并把文件权限收紧到仅当前用户可读写"""
    tmp_path = USERS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, USERS_PATH)
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass


def any_users():
    return len(load_users()) > 0


def user_exists(username):
    return username in load_users()


def add_user(username, password):
    users = load_users()
    users[username] = {
        "password_hash": generate_password_hash(password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    save_users(users)


def set_password(username, password):
    users = load_users()
    if username not in users:
        return False
    users[username]["password_hash"] = generate_password_hash(password)
    save_users(users)
    return True


def delete_user(username):
    users = load_users()
    if username not in users:
        return False
    del users[username]
    save_users(users)
    return True


def verify_user(username, password):
    users = load_users()
    entry = users.get(username)
    if not entry:
        return False
    return check_password_hash(entry["password_hash"], password)
