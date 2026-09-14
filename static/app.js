const state = {
  hosts: [],
  activeHostId: null,
  vms: [],
  sortKey: "vmid",
  sortDir: 1,
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname);
    throw new Error("未登录");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `请求失败(${res.status})`);
  }
  return res.json();
}

function fmtPct(p) {
  if (p === null || p === undefined) return "-";
  return `${p}%`;
}

function pctClass(p) {
  if (p === null || p === undefined) return "";
  if (p >= 90) return "danger";
  if (p >= 75) return "warn";
  return "";
}

function bar(pct) {
  const cls = pctClass(pct);
  const width = pct === null || pct === undefined ? 0 : Math.min(pct, 100);
  return `<div class="cell-bar">
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${width}%"></div></div>
    <span class="val">${fmtPct(pct)}</span>
  </div>`;
}

function gauge(label, pct, color) {
  const val = pct === null || pct === undefined ? 0 : pct;
  const ring = color ? `--ring-color:${color};` : "";
  return `<div class="gauge">
    <div class="gauge-ring" style="--pct:${val};${ring}"><span>${pct === null || pct === undefined ? "-" : Math.round(pct) + "%"}</span></div>
    <div class="gauge-label">${label}</div>
  </div>`;
}

// -------------------------------------------------- 宿主机表格
async function loadHosts(forceRefresh = false) {
  const tbody = document.getElementById("host-tbody");
  // 立即显示自动加载提示,避免用户等待 30-45 秒无反馈
  const section = document.getElementById("vm-section");
  section.classList.remove("hidden");
  document.getElementById("vm-section-title").textContent = "虚拟机列表";
  document.getElementById("vm-section-sub").textContent = "正在自动加载虚拟机列表(含 Guest Agent 数据,首次可能较慢)…";
  document.getElementById("vm-tbody").innerHTML = `<tr><td colspan="12" class="muted center">正在自动加载...</td></tr>`;
  try {
    state.hosts = await api("/api/hosts" + (forceRefresh ? "?refresh=1" : ""));
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted center">加载失败: ${e.message}</td></tr>`;
    return;
  }

  if (state.hosts.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" class="muted center">还没有添加任何宿主机<br><button class="btn btn-primary" onclick="openAddModal()">+ 添加第一台宿主机</button></td></tr>`;
    return;
  }

  // 扁平化：每个节点渲染为一行
  let rows = [];
  for (const host of state.hosts) {
    const online = host.status === "online";
    if (!online) {
      rows.push(renderHostRow(host, null));
    } else if (host.nodes && host.nodes.length > 0) {
      for (const node of host.nodes) {
        rows.push(renderHostRow(host, node));
      }
    } else {
      rows.push(renderHostRow(host, null));
    }
  }
  tbody.innerHTML = rows.join("");

  // 自动加载第一个在线宿主机的虚拟机列表
  const firstOnlineHost = state.hosts.find((h) => h.status === "online");
  if (firstOnlineHost) {
    openVmSection(firstOnlineHost.id, forceRefresh);
  }
}

function renderHostRow(host, node) {
  const online = host.status === "online" && node !== null;
  const statusDot = `<span class="status-dot ${online ? "online" : "error"}"></span>`;
  const statusText = online ? "在线" : (host.status === "online" ? "离线" : "异常");

  let cells;
  if (!online) {
    cells = `
      <td class="muted" colspan="4">${escapeHtml(host.error || "无法获取状态")}</td>
      <td>-</td>
      <td>-</td>
    `;
  } else {
    cells = `
      <td>${renderMiniGauge(node.cpu_percent)}</td>
      <td>${renderMiniGauge(node.mem_used_percent)}</td>
      <td>${renderMiniGauge(node.rootfs_used_percent)}</td>
      <td>${renderStorageSummary(node.storages)}</td>
    `;
  }

  const nodeLabel = node ? ` · ${escapeHtml(node.node)}` : "";
  const name = escapeHtml(host.name) + nodeLabel;

  return `
    <tr class="host-row" data-id="${host.id}">
      <td class="col-node">
        <div class="row-node-name">${statusDot}${name}</div>
        <div class="row-node-addr">${escapeHtml(host.hostname)}:${host.port}</div>
      </td>
      <td class="col-status">${statusText}</td>
      ${cells}

    </tr>
  `;
}

function renderMiniGauge(pct) {
  const val = pct === null || pct === undefined ? 0 : pct;
  const cls = pctClass(pct);
  return `<div class="mini-gauge ${cls}">
    <div class="mini-ring" style="--pct:${val};"><span>${pct === null || pct === undefined ? "-" : Math.round(pct) + "%"}</span></div>
  </div>`;
}

function renderStorageSummary(storages) {
  if (!storages || storages.length === 0) return `<span class="muted">无</span>`;
  const sorted = [...storages].sort((a, b) => (b.used_percent || 0) - (a.used_percent || 0));
  const top = sorted.slice(0, 2);
  const more = sorted.length > 2 ? ` <span class="muted">+${sorted.length - 2} more</span>` : "";
  const items = top.map((s) => {
    const cls = pctClass(s.used_percent);
    return `<div class="storage-mini">
      <span>${escapeHtml(s.name)}</span>
      <span class="storage-mini-pct ${cls}">${fmtBytesShort(s.used_bytes)}/${fmtBytesShort(s.total_bytes)}</span>
    </div>`;
  }).join("");
  return `<div class="storage-mini-wrap">${items}${more}</div>`;
}

function fmtBytesShort(n) {
  if (!n) return "0B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)}${units[i]}`;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// -------------------------------------------------- 虚拟机表格
async function openVmSection(hostId, forceRefresh = false) {
  state.activeHostId = hostId;
  const host = state.hosts.find((h) => h.id === hostId);
  const section = document.getElementById("vm-section");
  section.classList.remove("hidden");
  document.getElementById("vm-section-title").textContent = `虚拟机列表 · ${host ? host.name : hostId}`;
  document.getElementById("vm-section-sub").textContent = "正在自动加载虚拟机列表(含 Guest Agent 数据,首次可能较慢)…";
  document.getElementById("vm-tbody").innerHTML = `<tr><td colspan="12" class="muted center">加载中…</td></tr>`;
  section.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    state.vms = await api(`/api/hosts/${hostId}/vms${forceRefresh ? "?refresh=1" : ""}`);
    document.getElementById("vm-section-sub").textContent = `共 ${state.vms.length} 台虚拟机`;
    renderVmTable();
  } catch (e) {
    document.getElementById("vm-tbody").innerHTML = `<tr><td colspan="12" class="muted center">加载失败: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function renderVmTable() {
  const filterVal = document.getElementById("vm-filter").value.trim().toLowerCase();
  let rows = state.vms.filter((vm) => {
    if (!filterVal) return true;
    return String(vm.vmid).includes(filterVal)
      || (vm.name || "").toLowerCase().includes(filterVal)
      || (vm.ip_address || "").toLowerCase().includes(filterVal)
      || (vm.node || "").toLowerCase().includes(filterVal);
  });

  rows = rows.slice().sort((a, b) => {
    const ka = a[state.sortKey], kb = b[state.sortKey];
    if (ka === null || ka === undefined) return 1;
    if (kb === null || kb === undefined) return -1;
    if (typeof ka === "number" && typeof kb === "number") return (ka - kb) * state.sortDir;
    return String(ka).localeCompare(String(kb)) * state.sortDir;
  });

  const tbody = document.getElementById("vm-tbody");
  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="muted center">没有匹配的虚拟机</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map((vm) => {
    const statusPill = `<span class="pill ${vm.status === "running" ? "running" : "stopped"}">${vm.status === "running" ? "运行中" : "已停止"}</span>`;
    const diskAlloc = vm.disks && vm.disks.length
      ? vm.disks.map((d) => `${d.key}: ${d.size_human || "-"} @ ${escapeHtml(d.storage)}`).join("<br>")
      : (vm.disk_alloc_total_human || "-");
    return `
      <tr>
        <td>${vm.vmid}</td>
        <td>${escapeHtml(vm.name || "-")}</td>
        <td>${escapeHtml(vm.node || "-")}</td>
        <td>${escapeHtml(vm.ip_address || "-")}</td>
        <td>${statusPill}</td>
        <td>${vm.status === "running" ? vm.uptime : "-"}</td>
        <td>${vm.cpu_alloc_cores || "-"} 核</td>
        <td>${vm.status === "running" ? bar(vm.cpu_percent) : "-"}</td>
        <td>${vm.mem_alloc_human || "-"}</td>
        <td>${vm.status === "running" ? bar(vm.mem_percent) : "-"}</td>
        <td>${diskAlloc}</td>
        <td>${vm.disk_used_percent !== null && vm.disk_used_percent !== undefined ? bar(vm.disk_used_percent) : `<span class="muted">需 Agent</span>`}</td>
      </tr>
    `;
  }).join("");
}

async function exportVmsToExcel() {
  if (!state.activeHostId) return;
  const btn = document.getElementById("btn-export-excel");
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = "导出中…";
  try {
    const res = await fetch(`/api/hosts/${state.activeHostId}/vms/export`);
    if (res.status === 401) {
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `导出失败(${res.status})`);
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : "pve-vms.xlsx";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert(`导出失败: ${e.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}
document.getElementById("btn-export-excel").addEventListener("click", exportVmsToExcel);

document.getElementById("vm-filter").addEventListener("input", renderVmTable);
document.getElementById("btn-close-vms").addEventListener("click", () => {
  document.getElementById("vm-section").classList.add("hidden");
  state.activeHostId = null;
});
document.querySelectorAll("#vm-table thead th").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = 1; }
    renderVmTable();
  });
});

// -------------------------------------------------- 添加宿主机弹层
function openAddModal() {
  document.getElementById("modal-backdrop").classList.remove("hidden");
  document.getElementById("add-host-error").classList.add("hidden");
}
function closeAddModal() {
  document.getElementById("modal-backdrop").classList.add("hidden");
  document.getElementById("add-host-form").reset();
}
document.getElementById("btn-add-host").addEventListener("click", openAddModal);
document.getElementById("btn-cancel-add").addEventListener("click", closeAddModal);
document.getElementById("modal-backdrop").addEventListener("click", (e) => {
  if (e.target.id === "modal-backdrop") closeAddModal();
});

document.getElementById("add-host-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const errBox = document.getElementById("add-host-error");
  errBox.classList.add("hidden");
  const submitBtn = form.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = "连接中…";

  const data = {
    name: form.name.value.trim(),
    hostname: form.hostname.value.trim(),
    port: Number(form.port.value) || 8006,
    realm: form.realm.value,
    username: form.username.value.trim(),
    password: form.password.value,
    verify_ssl: form.verify_ssl.checked,
  };

  try {
    await api("/api/hosts", { method: "POST", body: JSON.stringify(data) });
    closeAddModal();
    loadHosts();
  } catch (err) {
    errBox.textContent = err.message;
    errBox.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "测试并添加";
  }
});

document.getElementById("btn-refresh").addEventListener("click", () => {
  loadHosts(true);
});

document.getElementById("btn-logout").addEventListener("click", async () => {
  try {
    await fetch("/logout", { method: "POST" });
  } finally {
    location.href = "/login";
  }
});

// -------------------------------------------------- 时钟 + 初始加载
function tickClock() {
  document.getElementById("clock").textContent = new Date().toLocaleString("zh-CN", { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

loadHosts();
