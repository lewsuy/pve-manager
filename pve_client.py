# -*- coding: utf-8 -*-
"""
PVE API 客户端
封装 Proxmox VE REST API 的登录认证与常用查询接口
"""
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class PVEAuthError(Exception):
    pass


class PVEClient:
    def __init__(self, hostname, port, username, realm, password, verify_ssl=False, timeout=30):
        self.hostname = hostname
        self.port = port or 8006
        self.username = username
        self.realm = realm or "pam"
        self.password = password
        self.verify_ssl = bool(verify_ssl)
        self.timeout = timeout
        self.base_url = f"https://{self.hostname}:{self.port}/api2/json"
        self.session = requests.Session()
        self.csrf_token = None
        self._logged_in = False

    # ------------------------------------------------------------------
    def login(self):
        """使用账号密码登录,获取 Ticket / CSRF Token"""
        url = f"{self.base_url}/access/ticket"
        data = {
            "username": f"{self.username}@{self.realm}",
            "password": self.password,
        }
        try:
            resp = self.session.post(url, data=data, verify=self.verify_ssl, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise PVEAuthError(f"无法连接主机 {self.hostname}:{self.port} - {e}")

        if resp.status_code != 200:
            raise PVEAuthError(f"登录失败({resp.status_code}): 请检查账号密码/Realm 是否正确")

        payload = resp.json().get("data") or {}
        ticket = payload.get("ticket")
        csrf = payload.get("CSRFPreventionToken")
        if not ticket:
            raise PVEAuthError("登录失败: 未返回有效 Ticket")

        self.session.cookies.set("PVEAuthCookie", ticket)
        self.csrf_token = csrf
        self._logged_in = True

    # ------------------------------------------------------------------
    def _request(self, method, path, params=None, data=None):
        if not self._logged_in:
            self.login()
        url = f"{self.base_url}{path}"
        headers = {}
        if method.upper() != "GET":
            headers["CSRFPreventionToken"] = self.csrf_token
        resp = self.session.request(
            method, url, params=params, data=data,
            headers=headers, verify=self.verify_ssl, timeout=self.timeout,
        )
        if resp.status_code == 401:
            # ticket 可能过期,重新登录一次
            self.login()
            headers["CSRFPreventionToken"] = self.csrf_token
            resp = self.session.request(
                method, url, params=params, data=data,
                headers=headers, verify=self.verify_ssl, timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json().get("data")

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------ 便捷方法
    def get_version(self):
        return self.get("/version")

    def get_nodes(self):
        """集群下所有节点概况"""
        return self.get("/nodes") or []

    def get_node_status(self, node):
        """节点 CPU / 内存 / 负载等"""
        return self.get(f"/nodes/{node}/status")

    def get_node_storage(self, node):
        """节点存储池使用情况"""
        return self.get(f"/nodes/{node}/storage") or []

    def get_vms(self, node):
        """节点下 QEMU 虚拟机列表(基础信息)。
        注意:此接口返回的 mem 字段是宿主机侧统计的 qemu 进程占用内存
        (约等于 memhost),不是客户机内部真实使用量,展示内存使用率时
        不要直接用它,应改用 get_vm_status_current。"""
        return self.get(f"/nodes/{node}/qemu") or []

    def get_vm_status_current(self, node, vmid):
        """单台虚拟机的详细实时状态,其中 mem 字段是客户机内部真实内存
        使用量(来自 ballooninfo/guest agent),和 PVE 网页版"概要"页显示
        的内存使用率口径一致。"""
        return self.get(f"/nodes/{node}/qemu/{vmid}/status/current") or {}

    def get_vm_config(self, node, vmid):
        """虚拟机配置(用于解析磁盘 -> 存储池 映射)"""
        return self.get(f"/nodes/{node}/qemu/{vmid}/config") or {}

    def get_vm_agent_network(self, node, vmid):
        """通过 guest agent 获取网卡/IP 信息,失败返回 None"""
        try:
            data = self.get(f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces")
            return data.get("result") if data else None
        except requests.exceptions.HTTPError:
            return None
        except requests.exceptions.RequestException:
            return None

    def get_vm_agent_fsinfo(self, node, vmid):
        """通过 guest agent 获取磁盘分区使用情况,失败返回 None"""
        try:
            data = self.get(f"/nodes/{node}/qemu/{vmid}/agent/get-fsinfo")
            return data.get("result") if data else None
        except requests.exceptions.HTTPError:
            return None
        except requests.exceptions.RequestException:
            return None
