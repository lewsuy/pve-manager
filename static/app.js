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

// -------------------------------------------------- 宿主机卡片
async function loadHosts() {
  const grid = document.getElementById("host-grid");
  try {
    state.hosts = await api("/api/hosts");
  } catch (e) {
    grid.innerHTML = `<div class="empty-state">加载失败: ${e.message}</div>`;
    return;
  }

  if (state.hosts.length === 0) {
    grid.innerHTML = `<div class="empty-state">还没有添加任何宿主机<br><button class="btn btn-primary" onclick="openAddModal()">+ 添加第一台宿主机</button></div>`;
    return;
  }

  // 扁平化：每个节点渲染为一个独立卡片，横排显示
  let cards = [];
  for (const host of state.hosts) {
    const online = host.status === "online";
    if (!online) {
      cards.push(renderHostCard(host, null));
    } else if (host.nodes && host.nodes.length > 0) {
      for (const node of host.nodes) {
        cards.push(renderHostCard(host, node));
      }
    } else {
      cards.push(renderHostCard(host, null));
    }
  }
  grid.innerHTML = cards.join("");

  grid.querySelectorAll(".host-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".host-delete")) return;
      const id = Number(card.dataset.id);
      openVmSection(id);
    });
  });
  grid.querySelectorAll(".host-delete").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      if (!confirm("确定要删除这台宿主机吗?(仅移除面板记录,不影响 PVE 本身)")) return;
      await api(`/api/hosts/${id}`, { method: "DELETE" });
      loadHosts();
    });
  });
}

function renderHostCard(host, node) {
  const online = host.status === "online" && node !== null;
  const statusDot = `<span class="status-dot ${online ? "online" : "error"}"></span>`;

  let body;
  if (!online) {
    body = `<div class="host-error">${escapeHtml(host.error || "无法获取状态")}</div>`;
  } else {
    body = renderNodeBlock(node);
  }

  const nodeLabel = node ? ` · ${escapeHtml(node.node)}` : "";

  return `
    <div class="host-card" data-id="${host.id}">
      <div class="host-card-head">
        <div>
          <p class="host-name">${statusDot}${escapeHtml(host.name)}${nodeLabel}</p>
          <p class="host-addr">${escapeHtml(host.hostname)}:${host.port}</p>
        </div>
        <button class="btn btn-ghost host-delete" data-id="${host.id}" title="删除">×</button>
      </div>
      ${body}
    </div>
  `;
}

function renderNodeBlock(n) {
  const rootPct = n.rootfs_used_percent;
  return `
    <div class="node-block">
      <div class="node-block-head">
        <span class="node-name"><span class="status-dot ${n.vm_status === "online" ? "running" : "stopped"}"></span>${escapeHtml(n.node)}</span>
        <span class="node-uptime">运行 ${n.uptime}</span>
      </div>
      <div class="gauge-row">
        ${gauge("CPU · " + (n.cpu_cores || "-") + "核", n.cpu_percent)}
        ${gauge("内存 · " + fmtBytesShort(n.mem_total_bytes), n.mem_used_percent)}
        ${gauge("系统盘", rootPct)}
      </div>
      ${renderStorageList(n.storages)}
    </div>
  `;
}

function renderStorageList(storages) {
  if (!storages || storages.length === 0) return "";
  return `<div class="storage-list">` + storages.map((s) => {
    const cls = pctClass(s.used_percent);
    return `<div class="storage-item">
      <div class="storage-item-head"><span>${escapeHtml(s.name)} (${escapeHtml(s.type)})</span><span>${fmtBytesShort(s.used_bytes)} / ${fmtBytesShort(s.total_bytes)}</span></div>
      <div class="bar-track"><div class="bar-fill ${cls}" style="width:${Math.min(s.used_percent, 100)}%"></div></div>
    </div>`;
  }).join("") + `</div>`;
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
async function openVmSection(hostId) {
  state.activeHostId = hostId;
  const host = state.hosts.find((h) => h.id === hostId);
  const section = document.getElementById("vm-section");
  section.classList.remove("hidden");
  document.getElementById("vm-section-title").textContent = `虚拟机列表 · ${host ? host.name : hostId}`;
  document.getElementById("vm-section-sub").textContent = "正在加载(含 Guest Agent 数据,首次可能较慢)…";
  document.getElementById("vm-tbody").innerHTML = `<tr><td colspan="12" class="muted center">加载中…</td></tr>`;
  section.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    state.vms = await api(`/api/hosts/${hostId}/vms`);
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
  loadHosts();
  if (state.activeHostId) openVmSection(state.activeHostId);
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
