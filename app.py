# -*- coding: utf-8 -*-
"""
PVE 多主机管理平台
Flask 后端:保存宿主机凭据,聚合展示 CPU/内存/存储/虚拟机信息
"""
import os
import re
from functools import wraps

import secrets
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from flask import Flask, request, jsonify, g, render_template, session, redirect, url_for

from pve_client import PVEClient, PVEAuthError
from users_store import verify_user, any_users

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hosts.db")
SECRET_KEY_PATH = os.path.join(BASE_DIR, ".secret_key")


def get_or_create_secret_key():
    """生成/复用一个持久化的 Flask session 签名密钥。
    必须落盘而不能每次进程启动随机生成——否则 gunicorn 多 worker 场景下
    各 worker 密钥不一致,会导致登录状态随机失效。"""
    for _ in range(2):
        try:
            with open(SECRET_KEY_PATH, "r") as f:
                key = f.read().strip()
                if key:
                    return key
        except FileNotFoundError:
            pass
        try:
            fd = os.open(SECRET_KEY_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            key = secrets.token_hex(32)
            with os.fdopen(fd, "w") as f:
                f.write(key)
            return key
        except FileExistsError:
            continue  # 极端并发情况下重试读取
    return secrets.token_hex(32)  # 兜底,不应该走到这里


app = Flask(__name__)
app.secret_key = os.environ.get("PVE_PANEL_SECRET") or get_or_create_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

DISK_KEY_RE = re.compile(r"^(scsi|virtio|ide|sata)\d+$")
DISK_STORAGE_RE = re.compile(r"^([^:]+):[^,]+(?:,.*)?$")
DISK_SIZE_RE = re.compile(r"size=(\d+(?:\.\d+)?)([KMGT])")

SIZE_UNIT = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


# ---------------------------------------------------------------- 数据库
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            hostname TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 8006,
            username TEXT NOT NULL,
            realm TEXT NOT NULL DEFAULT 'pam',
            password TEXT NOT NULL,
            verify_ssl INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cache_entries (
            key TEXT PRIMARY KEY,
            json_data TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache_entries(expires_at)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- 鉴权(账号密码登录 + Session)
# 账号密码存放在 users.json(见 manage_users.py),没有任何账号时面板会拒绝所有访问,
# 需要先在服务器上执行: python3 manage_users.py add <用户名>
@app.before_request
def require_login():
    if request.path in ("/login", "/logout") or request.path.startswith("/static"):
        return
    if session.get("username"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"error": "未登录或登录已过期,请重新登录"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("username"):
        return redirect(url_for("index"))

    error = None
    no_users = not any_users()
    if request.method == "POST":
        if no_users:
            error = "尚未创建任何账号,请先在服务器上执行: python3 manage_users.py add <用户名>"
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if username and verify_user(username, password):
                session.permanent = True
                session["username"] = username
                next_url = request.args.get("next") or url_for("index")
                return redirect(next_url)
            error = "用户名或密码错误"
    return render_template("login.html", error=error, no_users=no_users)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "已退出"})


# ---------------------------------------------------------------- 工具函数
def parse_disk_config(cfg):
    """从虚拟机 config 中解析磁盘 -> (存储池, 分配大小字节) 列表"""
    disks = []
    for key, val in cfg.items():
        if not DISK_KEY_RE.match(key):
            continue
        if not isinstance(val, str):
            continue
        m = DISK_STORAGE_RE.match(val)
        storage = m.group(1) if m else "未知"
        size_bytes = 0
        sm = DISK_SIZE_RE.search(val)
        if sm:
            size_bytes = float(sm.group(1)) * SIZE_UNIT[sm.group(2)]
        disks.append({"key": key, "storage": storage, "size_bytes": int(size_bytes)})
    return disks


def fmt_bytes(n):
    if n is None:
        return None
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def fmt_uptime(seconds):
    if not seconds:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    parts.append(f"{minutes}分钟")
    return "".join(parts)


def extract_primary_ip(agent_net):
    """从 guest agent 网卡数据里挑一个看起来像业务网卡的 IPv4"""
    if not agent_net:
        return None
    skip_names = {"lo", "docker0"}
    for iface in agent_net:
        name = iface.get("name", "")
        if name in skip_names or name.startswith("veth") or name.startswith("br-"):
            continue
        for ip in iface.get("ip-addresses", []):
            if ip.get("ip-address-type") == "ipv4" and not ip.get("ip-address", "").startswith("169.254"):
                return ip.get("ip-address")
    return None


def summarize_fsinfo(fsinfo):
    """汇总 guest agent 返回的文件系统信息,估算磁盘使用率"""
    if not fsinfo:
        return None
    total_bytes = 0
    used_bytes = 0
    for fs in fsinfo:
        total = fs.get("total-bytes")
        used = fs.get("used-bytes")
        if total:
            total_bytes += total
            used_bytes += used or 0
    if total_bytes == 0:
        return None
    return {
        "total_bytes": total_bytes,
        "used_bytes": used_bytes,
        "used_percent": round(used_bytes / total_bytes * 100, 1),
    }


def make_client(row):
    return PVEClient(
        hostname=row["hostname"],
        port=row["port"],
        username=row["username"],
        realm=row["realm"],
        password=row["password"],
        verify_ssl=bool(row["verify_ssl"]),
    )


# ---------------------------------------------------------------- 页面
@app.route("/")
def index():
    return render_template("index.html")



# ---------------------------------------------------------------- 简单内存缓存
# 集群数据(11节点x201VM)全量拉取约 20-45 秒,为避免每次打开页面/自动加载都重复等待,
# 加 60 秒内存缓存(手动点"刷新"时带 refresh=1 强制绕过)。
_TTL_CACHE = {}
_CACHE_TTL_SECONDS = 60

def ttl_cache(key_builder):
    """装饰器:按 key_builder(*args) 生成缓存键,TTL 内直接返回上次结果。"""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_builder(*args, **kwargs)
            now = time.monotonic()
            hit = _TTL_CACHE.get(key)
            if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
            value = fn(*args, **kwargs)
            _TTL_CACHE[key] = (now, value)
            if len(_TTL_CACHE) > 200:
                for k in [k for k, v in _TTL_CACHE.items() if now - v[0] >= _CACHE_TTL_SECONDS]:
                    _TTL_CACHE.pop(k, None)
            return value
        return wrapper
    return deco

def cache_clear():
    """清空全部缓存(供管理操作后调用)。"""
    _TTL_CACHE.clear()


# ---------------------------------------------------------------- SQLite 缓存读写
def get_sqlite_cache(key, ttl_seconds=300):
    """从 SQLite 读取缓存,返回解析后的 Python 对象,过期或不存在返回 None。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        now = int(time.time())
        row = conn.execute(
            "SELECT json_data FROM cache_entries WHERE key = ? AND expires_at > ?",
            (key, now),
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row["json_data"])
    except Exception:
        pass
    return None


def set_sqlite_cache(key, data, ttl_seconds=300):
    """将 Python 对象序列化后写入 SQLite 缓存。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO cache_entries (key, json_data, updated_at, expires_at) VALUES (?, ?, ?, ?)",
            (key, json.dumps(data, ensure_ascii=False), now, now + ttl_seconds),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 单个宿主机数据聚合(纯函数,可被预拉脚本复用)
def summarize_host_row(row):
    """对单条 hosts 表记录,实时连接 PVE 聚合节点状态。
    返回与 list_hosts 中 summarize 完全一致的字典。"""
    info = {
        "id": row["id"],
        "name": row["name"],
        "hostname": row["hostname"],
        "port": row["port"],
        "username": row["username"],
        "realm": row["realm"],
        "status": "online",
        "error": None,
        "nodes": [],
    }
    try:
        client = make_client(row)
        client.login()
        for node in client.get_nodes():
            node_name = node["node"]
            status = client.get_node_status(node_name) or {}
            storages = client.get_node_storage(node_name) or []
            mem = status.get("memory", {})
            cpu_info = status.get("cpuinfo", {})
            rootfs = status.get("rootfs", {})
            storage_list = []
            for st in storages:
                if not st.get("active"):
                    continue
                storage_list.append({
                    "name": st.get("storage"),
                    "type": st.get("type"),
                    "total_bytes": st.get("total", 0),
                    "used_bytes": st.get("used", 0),
                    "used_percent": round(st.get("used", 0) / st["total"] * 100, 1) if st.get("total") else 0,
                })
            info["nodes"].append({
                "node": node_name,
                "vm_status": node.get("status", "unknown"),
                "cpu_percent": round(status.get("cpu", 0) * 100, 1),
                "cpu_cores": cpu_info.get("cpus"),
                "mem_total_bytes": mem.get("total"),
                "mem_used_bytes": mem.get("used"),
                "mem_used_percent": round(mem.get("used", 0) / mem["total"] * 100, 1) if mem.get("total") else 0,
                "rootfs_total_bytes": rootfs.get("total"),
                "rootfs_used_bytes": rootfs.get("used"),
                "rootfs_used_percent": round(rootfs.get("used", 0) / rootfs["total"] * 100, 1) if rootfs.get("total") else 0,
                "uptime": fmt_uptime(status.get("uptime")),
                "storages": storage_list,
            })
    except PVEAuthError as e:
        info["status"] = "error"
        info["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        info["status"] = "error"
        info["error"] = f"获取状态失败: {e}"
    return info


# ---------------------------------------------------------------- 主机管理 API
@app.route("/api/hosts", methods=["GET"])
@ttl_cache(lambda: "hosts:" + request.args.get("refresh", "0"))
def list_hosts():
    # 优先读取 SQLite 持久缓存( cron 每 5 分钟预热),秒开体验
    if request.args.get("refresh") != "1":
        cached = get_sqlite_cache("hosts_summary")
        if cached is not None:
            return jsonify(cached)

    db = get_db()
    rows = db.execute("SELECT * FROM hosts ORDER BY id").fetchall()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(summarize_host_row, rows))

    set_sqlite_cache("hosts_summary", results)
    return jsonify(results)


@app.route("/api/hosts", methods=["POST"])
def add_host():
    payload = request.get_json(force=True) or {}
    required = ["name", "hostname", "username", "password"]
    missing = [f for f in required if not payload.get(f)]
    if missing:
        return jsonify({"error": f"缺少必填字段: {', '.join(missing)}"}), 400

    row = {
        "name": payload["name"],
        "hostname": payload["hostname"],
        "port": int(payload.get("port") or 8006),
        "username": payload["username"],
        "realm": payload.get("realm") or "pam",
        "password": payload["password"],
        "verify_ssl": 1 if payload.get("verify_ssl") else 0,
    }

    # 先测试连接,失败则不落库,直接把原因返回给前端
    try:
        client = PVEClient(
            hostname=row["hostname"], port=row["port"], username=row["username"],
            realm=row["realm"], password=row["password"], verify_ssl=bool(row["verify_ssl"]),
        )
        client.login()
    except PVEAuthError as e:
        return jsonify({"error": str(e)}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO hosts (name, hostname, port, username, realm, password, verify_ssl) "
        "VALUES (:name, :hostname, :port, :username, :realm, :password, :verify_ssl)",
        row,
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "message": "添加成功"}), 201


@app.route("/api/hosts/<int:host_id>", methods=["DELETE"])
def delete_host(host_id):
    db = get_db()
    db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    db.commit()
    return jsonify({"message": "已删除"})


# ---------------------------------------------------------------- 虚拟机列表 API
def fetch_host_vms(row, include_agent=True):
    """拉取某台宿主机(及其集群所有节点)下的虚拟机详情列表。
    返回 (vms, error_message)。error_message 非空时 vms 为 []。"""
    try:
        client = make_client(row)
        client.login()
    except PVEAuthError as e:
        return [], str(e)

    try:
        nodes = client.get_nodes()
    except Exception as e:  # noqa: BLE001
        return [], f"获取节点列表失败: {e}"

    def build_vm(node_name, vm):
        vmid = vm["vmid"]
        cpu_alloc = vm.get("cpus")
        cpu_percent = round((vm.get("cpu") or 0) * 100, 1)
        mem_total = vm.get("maxmem") or 0
        mem_used = vm.get("mem") or 0
        mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0

        result = {
            "node": node_name,
            "vmid": vmid,
            "name": vm.get("name"),
            "status": vm.get("status"),
            "uptime": fmt_uptime(vm.get("uptime")),
            "cpu_alloc_cores": cpu_alloc,
            "cpu_percent": cpu_percent,
            "mem_alloc_bytes": mem_total,
            "mem_alloc_human": fmt_bytes(mem_total),
            "mem_used_bytes": mem_used,
            "mem_percent": mem_percent,
            "disks": [],
            "disk_alloc_total_human": None,
            "ip_address": None,
            "disk_used_percent": None,
        }

        # 磁盘分配 + 存储池,来自 config(仅在需要时查询,减少调用量)
        try:
            cfg = client.get_vm_config(node_name, vmid)
            disks = parse_disk_config(cfg)
            result["disks"] = [
                {**d, "size_human": fmt_bytes(d["size_bytes"])} for d in disks
            ]
            total_alloc = sum(d["size_bytes"] for d in disks)
            result["disk_alloc_total_human"] = fmt_bytes(total_alloc) if total_alloc else None
        except Exception:  # noqa: BLE001
            pass

        # guest agent 数据:仅对运行中的虚拟机尝试,失败则静默跳过
        if vm.get("status") == "running":
            # 列表接口的 mem 字段是宿主机侧统计(约等于 memhost),不是客户机
            # 内部真实使用量,这里用 status/current 里的 mem 重新计算,和 PVE
            # 网页版"概要"页的内存使用率口径保持一致。
            try:
                cur = client.get_vm_status_current(node_name, vmid)
                real_mem_used = cur.get("mem")
                if real_mem_used is not None and mem_total:
                    result["mem_used_bytes"] = real_mem_used
                    result["mem_percent"] = round(real_mem_used / mem_total * 100, 1)
            except Exception:  # noqa: BLE001
                pass

            if include_agent:
                net = client.get_vm_agent_network(node_name, vmid)
                result["ip_address"] = extract_primary_ip(net)
                fsinfo = client.get_vm_agent_fsinfo(node_name, vmid)
                fs_summary = summarize_fsinfo(fsinfo)
                if fs_summary:
                    result["disk_used_percent"] = fs_summary["used_percent"]
                    result["disk_used_human"] = fmt_bytes(fs_summary["used_bytes"])

        return result

    all_vms = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = []
        for node in nodes:
            node_name = node["node"]
            try:
                vms = client.get_vms(node_name)
            except Exception:  # noqa: BLE001
                continue
            for vm in vms:
                futures.append(pool.submit(build_vm, node_name, vm))
        for fut in as_completed(futures):
            try:
                all_vms.append(fut.result())
            except Exception:  # noqa: BLE001
                continue

    all_vms.sort(key=lambda x: (x["node"], x["vmid"]))
    return all_vms, None


@app.route("/api/hosts/<int:host_id>/vms", methods=["GET"])
@ttl_cache(lambda host_id: "vms:%s:%s:%s" % (host_id, request.args.get("agent", "1"), request.args.get("refresh", "0")))
def list_vms(host_id):
    # 优先读取 SQLite 持久缓存
    if request.args.get("refresh") != "1":
        cached = get_sqlite_cache(f"host_vms:{host_id}")
        if cached is not None:
            return jsonify(cached)

    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if row is None:
        return jsonify({"error": "主机不存在"}), 404

    include_agent = request.args.get("agent", "1") != "0"
    vms, error = fetch_host_vms(row, include_agent=include_agent)
    if error:
        return jsonify({"error": error}), 400
    set_sqlite_cache(f"host_vms:{host_id}", vms)
    return jsonify(vms)


@app.route("/api/hosts/<int:host_id>/vms/export", methods=["GET"])
def export_vms(host_id):
    from io import BytesIO
    from flask import send_file
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    db = get_db()
    row = db.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if row is None:
        return jsonify({"error": "主机不存在"}), 404

    vms, error = fetch_host_vms(row, include_agent=True)
    if error:
        return jsonify({"error": error}), 400

    wb = Workbook()
    ws = wb.active
    ws.title = "虚拟机清单"

    headers = [
        "ID", "名称", "节点", "IP 地址", "状态", "运行时间",
        "CPU 分配(核)", "CPU 使用率(%)",
        "内存分配", "内存使用率(%)",
        "存储分配", "磁盘使用率(%)",
    ]
    ws.append(headers)
    header_fill = PatternFill(start_color="1F5678", end_color="1F5678", fill_type="solid")
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for vm in vms:
        disk_alloc = ", ".join(
            f"{d['key']}: {d.get('size_human') or '-'}@{d['storage']}" for d in (vm.get("disks") or [])
        ) or (vm.get("disk_alloc_total_human") or "-")
        ws.append([
            vm.get("vmid"),
            vm.get("name") or "-",
            vm.get("node") or "-",
            vm.get("ip_address") or "-",
            "运行中" if vm.get("status") == "running" else "已停止",
            vm.get("uptime") if vm.get("status") == "running" else "-",
            vm.get("cpu_alloc_cores") or "-",
            vm.get("cpu_percent") if vm.get("status") == "running" else "-",
            vm.get("mem_alloc_human") or "-",
            vm.get("mem_percent") if vm.get("status") == "running" else "-",
            disk_alloc,
            vm.get("disk_used_percent") if vm.get("disk_used_percent") is not None else "-",
        ])

    widths = [8, 22, 14, 16, 10, 14, 12, 14, 12, 14, 40, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    host_name = row["name"] or f"host-{host_id}"
    filename = f"pve-vms-{host_name}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PVE_PANEL_PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # 被 gunicorn / uwsgi 等 WSGI 服务器导入时,__main__ 分支不会执行,
    # 这里保证无论哪种启动方式,数据库表都会被创建。
    init_db()
