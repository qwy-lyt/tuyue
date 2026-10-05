/* Admin console: accounts, invite codes and the audit trail.
   Every action here goes through an /api/admin route that re-checks the caller
   is an administrator, so hiding this UI is convenience, not the security gate. */

function adminError(message) {
  const box = $("admin-error");
  box.textContent = message || "";
  box.classList.toggle("hidden", !message);
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatTime(seconds) {
  if (!seconds) return "—";
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
       + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/* ------------------------------------------------------------------ */
/* users                                                               */
/* ------------------------------------------------------------------ */

async function loadAdminUsers() {
  const rows = await api("/api/admin/users");
  const wrap = $("admin-users-table");

  const table = document.createElement("table");
  table.className = "admin-table";
  table.innerHTML = `
    <thead><tr>
      <th>用户名</th><th>角色</th><th>状态</th>
      <th>创建</th><th>最后登录</th><th>用量</th><th>操作</th>
    </tr></thead>`;
  const body = document.createElement("tbody");

  for (const user of rows) {
    const tr = document.createElement("tr");
    if (user.disabled) tr.className = "row-disabled";

    const cells = {
      用户名: user.username,
      角色: user.is_admin ? "管理员" : "普通",
      状态: user.disabled ? "已停用" : "正常",
      创建: formatTime(user.created_at),
      最后登录: formatTime(user.last_login),
      用量: `${formatBytes(user.storage_bytes)} · ${user.conversations} 对话 · ${user.messages} 条`,
    };
    for (const value of Object.values(cells)) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }

    const actions = document.createElement("td");
    actions.className = "row-actions";
    actions.append(
      adminButton("改密码", () => resetPassword(user)),
      adminButton(user.disabled ? "启用" : "停用", () => toggleDisabled(user)),
      adminButton("删除", () => deleteUser(user), "btn-danger-text"),
    );
    tr.append(actions);
    body.append(tr);
  }

  table.append(body);
  wrap.replaceChildren(table);
}

function adminButton(label, handler, extraClass = "") {
  const button = document.createElement("button");
  button.className = `btn-ghost btn-tiny ${extraClass}`.trim();
  button.textContent = label;
  button.onclick = handler;
  return button;
}

async function copyText(text) {
  // navigator.clipboard only exists in a secure context, and plain HTTP over
  // Radmin VPN is not one -- so fall back to the old selection trick.
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through to the fallback */ }
  }

  const scratch = document.createElement("textarea");
  scratch.value = text;
  scratch.setAttribute("readonly", "");
  scratch.style.position = "fixed";
  scratch.style.top = "-1000px";
  document.body.append(scratch);
  scratch.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  scratch.remove();
  return copied;
}

async function resetPassword(user) {
  const password = window.prompt(`给「${user.username}」设置新密码（至少 10 位）`);
  if (!password) return;
  try {
    await api(`/api/admin/users/${user.id}/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    adminError("");
    await loadAdminUsers();
  } catch (error) {
    adminError(error.message);
  }
}


async function toggleDisabled(user) {
  const disabling = !user.disabled;
  if (disabling) {
    const ok = await confirmDialog({
      title: "停用账号",
      text: `停用后「${user.username}」无法登录，现有会话立即失效。可以随时恢复。`,
      confirmLabel: "停用",
    });
    if (!ok) return;
  }
  try {
    await api(`/api/admin/users/${user.id}/disabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled: disabling }),
    });
    adminError("");
    await loadAdminUsers();
  } catch (error) {
    adminError(error.message);
  }
}

async function deleteUser(user) {
  const ok = await confirmDialog({
    title: "删除账号",
    text: `「${user.username}」的账号、全部对话和上传的文件都会被永久删除，无法恢复。`,
    confirmLabel: "永久删除",
  });
  if (!ok) return;
  try {
    await api(`/api/admin/users/${user.id}`, { method: "DELETE" });
    adminError("");
    await loadAdminUsers();
  } catch (error) {
    adminError(error.message);
  }
}

async function createUser() {
  const username = $("new-user-name").value.trim();
  const password = $("new-user-pass").value;
  if (!username || !password) {
    adminError("用户名和密码都要填");
    return;
  }
  try {
    await api("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, is_admin: $("new-user-admin").checked }),
    });
    $("new-user-name").value = "";
    $("new-user-pass").value = "";
    $("new-user-admin").checked = false;
    adminError("");
    await loadAdminUsers();
  } catch (error) {
    adminError(error.message);
  }
}

/* ------------------------------------------------------------------ */
/* invite codes                                                        */
/* ------------------------------------------------------------------ */

async function loadInvites() {
  const rows = await api("/api/admin/invites");
  const wrap = $("admin-invites-table");

  const table = document.createElement("table");
  table.className = "admin-table";
  table.innerHTML = `
    <thead><tr>
      <th>邀请码</th><th>备注</th><th>已用/上限</th><th>到期</th><th>创建</th><th>操作</th>
    </tr></thead>`;
  const body = document.createElement("tbody");

  for (const invite of rows) {
    const tr = document.createElement("tr");

    const codeCell = document.createElement("td");
    const code = document.createElement("code");
    code.className = "secret-inline";
    code.textContent = invite.code;
    codeCell.append(code);

    const values = [
      invite.note || "—",
      // max_uses of 0 means the code never runs out.
      invite.max_uses > 0 ? `${invite.uses} / ${invite.max_uses}` : `已用 ${invite.uses} / 无上限`,
      invite.expires_at ? formatTime(invite.expires_at) : "永久",
      formatTime(invite.created_at),
    ];
    tr.append(codeCell);
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }

    const actions = document.createElement("td");
    actions.className = "row-actions";
    const copyButton = adminButton("复制", async () => {
      const copied = await copyText(invite.code);
      copyButton.textContent = copied ? "已复制" : "请手动选中";
      setTimeout(() => { copyButton.textContent = "复制"; }, 1600);
    });
    actions.append(copyButton, adminButton("吊销", () => revokeInvite(invite.code), "btn-danger-text"));
    tr.append(actions);
    body.append(tr);
  }

  table.append(body);
  wrap.replaceChildren(table);
}

async function createInvite() {
  const days = parseInt($("new-invite-days").value, 10);
  const unlimited = $("new-invite-unlimited").checked;
  const typed = parseInt($("new-invite-uses").value, 10);

  try {
    await api("/api/admin/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        note: $("new-invite-note").value.trim(),
        // 0 tells the server "no cap"; an empty box does the same thing.
        max_uses: unlimited || !Number.isFinite(typed) ? 0 : Math.max(typed, 0),
        expires_in_days: Number.isFinite(days) && days > 0 ? days : null,
      }),
    });
    $("new-invite-note").value = "";
    $("new-invite-days").value = "";
    $("new-invite-unlimited").checked = false;
    adminError("");
    await loadInvites();
  } catch (error) {
    adminError(error.message);
  }
}

async function revokeInvite(code) {
  const ok = await confirmDialog({
    title: "吊销邀请码",
    text: `邀请码 ${code} 将立即失效。已经用它注册的账号不受影响。`,
    confirmLabel: "吊销",
  });
  if (!ok) return;
  try {
    await api(`/api/admin/invites/${encodeURIComponent(code)}`, { method: "DELETE" });
    adminError("");
    await loadInvites();
  } catch (error) {
    adminError(error.message);
  }
}

/* ------------------------------------------------------------------ */
/* audit log                                                           */
/* ------------------------------------------------------------------ */

const AUDIT_LABELS = {
  login: "登录成功",
  login_failed: "登录失败",
  logout: "退出",
  register: "注册",
  register_rejected: "注册被拒",
  upload: "上传文件",
  settings_updated: "修改设置",
  admin_create_user: "创建账号",
  admin_reset_password: "重置密码",
  admin_disable_user: "停用账号",
  admin_enable_user: "启用账号",
  admin_delete_user: "删除账号",
  admin_create_invite: "生成邀请码",
  admin_revoke_invite: "吊销邀请码",
};

async function loadAudit() {
  const rows = await api("/api/admin/audit?limit=200");
  const wrap = $("admin-audit-table");

  const table = document.createElement("table");
  table.className = "admin-table";
  table.innerHTML = "<thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>详情</th><th>来源</th></tr></thead>";
  const body = document.createElement("tbody");

  for (const entry of rows) {
    const tr = document.createElement("tr");
    const values = [
      formatTime(entry.at),
      entry.actor || "—",
      AUDIT_LABELS[entry.action] || entry.action,
      entry.detail || "—",
      entry.ip || "—",
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.append(td);
    }
    body.append(tr);
  }

  table.append(body);
  wrap.replaceChildren(table);
}

/* ------------------------------------------------------------------ */
/* panel                                                               */
/* ------------------------------------------------------------------ */

const ADMIN_LOADERS = { users: loadAdminUsers, invites: loadInvites, audit: loadAudit };

async function showAdminTab(name) {
  for (const button of document.querySelectorAll("#admin-overlay .tab")) {
    button.classList.toggle("active", button.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll("#admin-overlay .tab-panel")) {
    panel.classList.add("hidden");
  }
  $("admin-" + name).classList.remove("hidden");

  adminError("");
  try {
    await ADMIN_LOADERS[name]();
  } catch (error) {
    adminError(error.message);
  }
}

async function openAdmin() {
  $("admin-overlay").classList.remove("hidden");
  await showAdminTab("users");
}

$("open-admin").onclick = openAdmin;
$("admin-close").onclick = () => $("admin-overlay").classList.add("hidden");
$("create-user").onclick = createUser;
$("create-invite").onclick = createInvite;
$("new-invite-unlimited").onchange = () => {
  $("new-invite-uses").disabled = $("new-invite-unlimited").checked;
};
$("refresh-audit").onclick = () => showAdminTab("audit");

for (const button of document.querySelectorAll("#admin-overlay .tab")) {
  button.onclick = () => showAdminTab(button.dataset.tab);
}
