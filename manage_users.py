#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PVE 控制台 - 用户管理 CLI

用法:
    python3 manage_users.py add <用户名>      添加一个新账号(命令行交互输入密码)
    python3 manage_users.py passwd <用户名>   修改某个账号的密码
    python3 manage_users.py delete <用户名>   删除某个账号
    python3 manage_users.py list             列出所有账号

密码不会以明文出现在命令行历史或进程列表中(用 getpass 交互输入),
users.json 里也只保存哈希值。
"""
import argparse
import getpass
import sys

from users_store import add_user, delete_user, load_users, set_password, user_exists

MIN_PASSWORD_LEN = 6


def _prompt_password(confirm_label="确认密码: "):
    pw1 = getpass.getpass("设置密码: ")
    pw2 = getpass.getpass(confirm_label)
    if pw1 != pw2:
        print("两次输入的密码不一致,已取消")
        sys.exit(1)
    if len(pw1) < MIN_PASSWORD_LEN:
        print(f"密码至少需要 {MIN_PASSWORD_LEN} 位,已取消")
        sys.exit(1)
    return pw1


def cmd_add(args):
    username = args.username.strip()
    if not username:
        print("用户名不能为空")
        sys.exit(1)
    if user_exists(username):
        print(f"用户 {username} 已存在。如需修改密码请用: python3 manage_users.py passwd {username}")
        sys.exit(1)
    password = _prompt_password()
    add_user(username, password)
    print(f"已添加用户: {username}")


def cmd_passwd(args):
    username = args.username.strip()
    if not user_exists(username):
        print(f"用户 {username} 不存在")
        sys.exit(1)
    password = _prompt_password("确认新密码: ")
    set_password(username, password)
    print(f"已更新 {username} 的密码")


def cmd_delete(args):
    username = args.username.strip()
    if not user_exists(username):
        print(f"用户 {username} 不存在")
        sys.exit(1)
    confirm = input(f"确定删除用户 {username}?此操作不可撤销,输入 yes 确认: ")
    if confirm.strip().lower() != "yes":
        print("已取消")
        return
    delete_user(username)
    print(f"已删除用户: {username}")


def cmd_list(args):  # noqa: ARG001
    users = load_users()
    if not users:
        print("当前没有任何账号——面板会拒绝所有访问。")
        print("请先执行: python3 manage_users.py add <用户名> 创建第一个管理员账号")
        return
    print(f"{'用户名':<20}{'创建时间'}")
    print("-" * 50)
    for name, info in users.items():
        print(f"{name:<20}{info.get('created_at', '-')}")


def main():
    parser = argparse.ArgumentParser(description="PVE 控制台用户管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="添加账号")
    p_add.add_argument("username")
    p_add.set_defaults(func=cmd_add)

    p_passwd = sub.add_parser("passwd", help="修改账号密码")
    p_passwd.add_argument("username")
    p_passwd.set_defaults(func=cmd_passwd)

    p_delete = sub.add_parser("delete", help="删除账号")
    p_delete.add_argument("username")
    p_delete.set_defaults(func=cmd_delete)

    p_list = sub.add_parser("list", help="列出所有账号")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
