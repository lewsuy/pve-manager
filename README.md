# PVE 控制台 — 多宿主机管理面板

一个轻量的 Flask 应用,用账号密码添加多台 PVE 宿主机,集中查看:

- **资产清单**:每台宿主机(含集群下所有节点)的 CPU / 内存 / 系统盘使用率,以及各存储池的容量占用
- **虚拟机列表**:ID、名称、IP 地址(需 Guest Agent)、运行状态、运行时间、CPU 分配/使用率、内存分配/使用率、存储分配(大小+存储池)、磁盘使用率(需 Guest Agent)

## 1. 部署

建议部署在你现有的运维服务器上(比如已经跑 monitor 平台的那台机器),和 PVE 主机网络互通即可,**不需要**在 PVE 主机上安装任何东西(纯调用官方 REST API)。

```bash
# 上传/解压项目后
cd pve-manager
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 首次启动会自动建表(hosts.db, SQLite)
python3 app.py
```

默认监听 `0.0.0.0:5055`,浏览器访问 `http://<部署机IP>:5055`。

### 生产环境建议

**1) 创建登录账号**(不创建的话面板会拒绝所有访问):
```bash
cd /opt/pve-manager
source venv/bin/activate
python3 manage_users.py add admin
# 交互式输入两次密码(至少 6 位),回车确认即可
```
访问面板会先跳转到独立的登录页,输入用户名+密码登录,session 有效期 7 天(存在浏览器 Cookie 里)。多个 gunicorn worker 之间的登录状态是共享的(程序会在项目目录下自动生成 `.secret_key` 文件用于签名 session)。

账号密码只存在项目目录下的 `users.json` 里,且只存密码的哈希值(不是明文),`users.json` 和 `.secret_key` 一样属于敏感文件,注意文件权限、不要提交到 git、不要对外暴露。

账号管理命令:
```bash
python3 manage_users.py list             # 列出所有账号
python3 manage_users.py add <用户名>      # 添加账号
python3 manage_users.py passwd <用户名>   # 修改密码
python3 manage_users.py delete <用户名>   # 删除账号
```

**2) 用 gunicorn 做 WSGI Server,不要用 `python3 app.py` 直接跑**(已加入 `requirements.txt`):
```bash
gunicorn -w 2 -b 0.0.0.0:5055 app:app
```

**3) 用 systemd 常驻,开机自启、崩溃自动重启**

假设你的部署路径是 `/opt/pve-manager`,虚拟环境在 `/opt/pve-manager/venv`(和你目前的路径一致)。项目里已经带了写好的 `pve-manager.service`,按下面的路径核对无误后直接用:

```bash
# 如果部署路径不是 /opt/pve-manager,先改一下 service 文件里的
# WorkingDirectory 和 ExecStart 两行路径

cp pve-manager.service /etc/systemd/system/pve-manager.service
systemctl daemon-reload
systemctl enable --now pve-manager
systemctl status pve-manager      # 确认是 active (running)
journalctl -u pve-manager -f      # 查看实时日志
```

常用管理命令:
```bash
systemctl restart pve-manager     # 重启(比如替换了新代码之后)
systemctl stop pve-manager
systemctl disable pve-manager     # 取消开机自启
```

- 建议只在内网访问,或在前面加一层 Nginx + HTTPS + 访问控制(可以直接用你现有的 OpenResty 反代)。
- `hosts.db` 里保存的是宿主机的明文密码,请注意文件权限(默认 `chmod 600`),更安全的做法见下方"关于凭据安全"。

## 2. 使用说明

1. 打开面板,点击右上角「+ 添加宿主机」
2. 填写地址、端口(默认 8006)、用户名、密码、Realm(pam 为 Linux 系统账号,pve 为 PVE 本地账号)
3. 点击"测试并添加",系统会先尝试登录,失败会直接提示原因(账号密码错误 / 网络不通等),成功才会保存
4. 添加成功后,该主机的卡片会显示 CPU / 内存 / 系统盘环形仪表盘,以及各存储池占用条
5. 点击卡片,下方会展开该主机(及其所在集群所有节点)的虚拟机完整列表,支持按列排序、按 ID/名称/IP 过滤
6. 卡片右上角「×」可以从面板移除这台主机(只是删除面板记录,不影响 PVE 本身)

### 关于 IP 地址和磁盘使用率为何有时显示"-"或"需 Agent"

这两项数据依赖虚拟机内部安装并启用了 **qemu-guest-agent**:

```bash
# Debian/Ubuntu 虚拟机内执行
apt install qemu-guest-agent -y
systemctl enable --now qemu-guest-agent
```

同时需要在 PVE 虚拟机的「选项」里把 "QEMU Guest Agent" 设为启用。没装的虚拟机仍会正常显示其他字段(状态、CPU/内存分配与使用率、存储分配),只是拿不到真实内部 IP 和磁盘占用。

### 关于 CPU/内存使用率的含义

- **CPU 使用率**:PVE 原生返回值,是该虚拟机相对其"已分配核数"的使用比例,不是宿主机整体 CPU 占用
- **内存使用率**:虚拟机当前占用内存 / 分配内存(maxmem)
- **存储分配**:解析虚拟机配置里的磁盘定义(scsi/virtio/ide/sata),显示每块盘分配大小及所在的存储池名称
- **磁盘使用率**:来自 Guest Agent 上报的分区实际使用情况,汇总后计算(排除了循环挂载等特殊分区的误差可能存在,仅供参考)

## 3. 关于凭据安全(重要)

当前版本为了满足"账号密码添加"的需求,数据库中直接保存了明文密码。更安全的替代方案:

- **改用 PVE API Token**(推荐):在 PVE 里为一个只读角色的账号创建 API Token,权限可以限制到最小(比如仅 `VM.Audit` + `Datastore.Audit` + `Sys.Audit`),即使 Token 泄露风险也远小于 root 密码。如果你需要,我可以把 `pve_client.py` 改造成支持 Token 认证(`Authorization: PVEAPIToken=user@realm!tokenid=uuid`),表单里增加"使用 Token"选项。
- 给面板账号在 PVE 里单独建一个**只读角色**,不要直接用 root,遵循最小权限原则。
- 部署机上限制 `hosts.db` 文件权限,并做好整机访问控制。

## 4. 目录结构

```
pve-manager/
├── app.py               # Flask 主应用与路由
├── pve_client.py         # PVE REST API 封装
├── users_store.py        # 账号密码存取(users.json 读写、哈希校验)
├── manage_users.py       # 账号管理 CLI(add/passwd/delete/list)
├── pve-manager.service   # systemd 服务单元文件
├── requirements.txt
├── hosts.db              # 运行后自动生成(SQLite,宿主机凭据)
├── users.json            # 运行 manage_users.py 后生成(账号密码哈希)
├── .secret_key           # 运行后自动生成(session 签名密钥)
├── templates/
│   ├── index.html
│   └── login.html
└── static/
    ├── style.css
    └── app.js
```
