/* Chat UI: conversation sidebar, drag-and-drop uploads, SSE streaming. */

const $ = (id) => document.getElementById(id);

const state = {
  conversationId: null,
  library: [],        // every file this account has uploaded, newest first
  selectedIds: [],    // which of them go with the next message
  streaming: false,
  appName: "图阅",     // replaced by /api/status on load
  avatarText: "图",
};

/* ------------------------------------------------------------------ */
/* file library                                                        */
/* ------------------------------------------------------------------ */

function libraryEntry(fileId) {
  return state.library.find((item) => item.file_id === fileId) || null;
}

async function loadFileLibrary() {
  try {
    state.library = await api("/api/files");
  } catch {
    state.library = [];
  }

  // Drop selections for files that no longer exist.
  state.selectedIds = state.selectedIds.filter((id) => libraryEntry(id));

  renderFileLibrary();
  renderSelection();
}

function renderFileLibrary() {
  const box = $("file-library");
  box.replaceChildren();
  $("file-count").textContent = state.library.length ? `（${state.library.length}）` : "";

  if (!state.library.length) {
    const empty = document.createElement("div");
    empty.className = "library-empty";
    empty.textContent = "还没有文件。上传后会出现在这里，想聊哪个点哪个。";
    box.append(empty);
    return;
  }

  for (const item of state.library) {
    const selected = state.selectedIds.includes(item.file_id);
    const row = document.createElement("div");
    row.className = "library-item" + (selected ? " selected" : "");
    // The name can be ellipsised in a 270px sidebar, so the tooltip repeats it.
    row.title = `${item.filename}\n${selected ? "点击取消选择" : "点击选中，然后用它提问"}`;

    const thumb = document.createElement("div");
    thumb.className = "library-thumb";
    if (item.thumbnail) {
      const img = document.createElement("img");
      img.src = item.thumbnail;
      img.alt = "";
      thumb.append(img);
    } else {
      thumb.textContent = "📄";
    }

    const meta = document.createElement("div");
    meta.className = "library-meta";
    const name = document.createElement("div");
    name.className = "library-name";
    name.textContent = item.filename;
    const sub = document.createElement("div");
    sub.className = "library-sub";
    const bits = [];
    if (item.image_count) bits.push(`${item.image_count} 张图`);
    bits.push(item.has_text ? "含文字" : "无文字层");
    sub.textContent = bits.join(" · ");
    meta.append(name, sub);

    // The extract action lives here, next to the file it applies to.
    const extract = document.createElement("button");
    extract.type = "button";
    extract.className = "library-action";
    extract.textContent = "数据";
    extract.title = "读取这份文件的数据";
    extract.onclick = (event) => {
      event.stopPropagation();
      extractFor([item.file_id]);
    };

    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.textContent = "×";
    del.title = `删除「${item.filename}」`;
    del.setAttribute("aria-label", del.title);
    del.onclick = (event) => {
      event.stopPropagation();
      deleteFile(item.file_id);
    };

    row.append(thumb, meta, extract, del);
    row.onclick = () => toggleFileSelection(item.file_id);
    box.append(row);
  }
}

function toggleFileSelection(fileId) {
  const index = state.selectedIds.indexOf(fileId);
  if (index === -1) {
    state.selectedIds.push(fileId);
  } else {
    state.selectedIds.splice(index, 1);
  }
  renderFileLibrary();
  renderSelection();
}

function clearSelection() {
  state.selectedIds = [];
  renderFileLibrary();
  renderSelection();
}

/** Take a file out of the library, along with everything derived from it. */
async function deleteFile(fileId) {
  const item = libraryEntry(fileId);
  if (!item) return;

  const confirmed = await confirmDialog({
    title: "删除文件",
    text:
      `「${item.filename}」的原件、解析缓存和已读出的数据会一并删除，无法恢复。` +
      "已经聊过的内容不受影响。",
    confirmLabel: "删除",
  });
  if (!confirmed) return;

  try {
    await api(`/api/files/${fileId}`, { method: "DELETE" });
  } catch (error) {
    alert(`删除失败：${error.message}`);
    return;
  }

  state.selectedIds = state.selectedIds.filter((id) => id !== fileId);
  if (state.fileCache) delete state.fileCache[fileId];

  // The panel was showing a set this file belonged to, and that set can never be
  // asked for again -- better to close it than leave a table with no subject.
  if (panel.fileIds.includes(fileId)) clearPanel();

  await loadFileLibrary();
}

/** Empty the panel, for when what it described is gone. */
function clearPanel() {
  panel.items = [];
  panel.fileIds = [];
  panel.filter = "";
  $("panel-filter").value = "";
  $("panel-tbody").replaceChildren();
  $("panel-count").textContent = "";
  $("panel-notices").classList.add("hidden");
  updateReloadButton();
  setPanelVisible(false);
}

/* ------------------------------------------------------------------ */
/* markdown (small, safe subset)                                       */
/* ------------------------------------------------------------------ */

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function renderMarkdown(source) {
  const blocks = [];
  // Pull fenced code out first so nothing inside it gets re-processed.
  let text = source.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return `\u0000${blocks.length - 1}\u0000`;
  });

  text = escapeHtml(text);
  text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/^### (.+)$/gm, "<h3>$1</h3>");
  text = text.replace(/^## (.+)$/gm, "<h3>$1</h3>");
  text = text.replace(/^# (.+)$/gm, "<h3>$1</h3>");

  return text
    .split(/\n{2,}/)
    .map((para) => {
      const restored = para.replace(/\u0000(\d+)\u0000/g, (_, i) => blocks[Number(i)]);
      if (/^(<pre|<h3|<ul|<ol)/.test(restored.trim())) return restored;
      const lines = restored.split("\n").map((line) => {
        const bullet = line.match(/^\s*[-*]\s+(.*)$/);
        return bullet ? `• ${bullet[1]}` : line;
      });
      return `<p>${lines.join("<br>")}</p>`;
    })
    .join("");
}

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

async function api(path, options) {
  const response = await fetch(path, options);

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch { /* not JSON; keep the status text */ }

    // Some auth failures carry a structured detail so the caller can react to
    // the reason (for example, prompting for a two-factor code).
    if (detail && typeof detail === "object") {
      const error = new Error(detail.message || "请求失败");
      error.code = detail.code || "";
      error.status = response.status;
      throw error;
    }

    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }

  return response.json();
}

function scrollToBottom() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function setStreaming(on) {
  state.streaming = on;
  $("send").disabled = on;
  $("send").textContent = on ? "生成中" : "发送";
}

/* ------------------------------------------------------------------ */
/* empty-state suggestions                                             */
/* ------------------------------------------------------------------ */

// All of these are things the tool can actually answer from a drawing: either
// from the text entities ezdxf pulls out, or from the rendered image.
const SAMPLE_QUESTIONS = [
  "这份图纸里有哪些房间？",
  "提取所有尺寸标注并列表",
  "图纸有哪些图层？分别是什么用途？",
  "图中有哪些标高标注？",
  "统计图中门窗块的数量",
  "这张图的防火等级和耐火要求是什么？",
  "把图上的文字标注按图层归类",
  "图纸的绘图单位和图纸范围是多少？",
  "图中有哪些轴网编号？",
  "把我上传的几份图纸的内容对比一下",
];

function pickQuestions(count = 3) {
  const pool = [...SAMPLE_QUESTIONS];
  const chosen = [];
  while (chosen.length < count && pool.length) {
    chosen.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  return chosen;
}

function fillEmptyHints() {
  const box = $("empty-hints");
  if (!box) return;

  box.replaceChildren();
  for (const question of pickQuestions()) {
    const chip = document.createElement("span");
    chip.textContent = `“${question}”`;
    // Clicking one should get you going, not just sit there looking pretty.
    chip.onclick = () => {
      const input = $("input");
      input.value = question;
      input.focus();
      input.dispatchEvent(new Event("input")); // let the height auto-fit run
    };
    box.append(chip);
  }
}

/** Modal yes/no. Resolves true only when the user explicitly confirms. */
function confirmDialog({ title, text, confirmLabel = "确定" }) {
  return new Promise((resolve) => {
    const overlay = $("confirm-overlay");
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-ok").textContent = confirmLabel;
    overlay.classList.remove("hidden");
    $("confirm-cancel").focus();

    const finish = (answer) => {
      overlay.classList.add("hidden");
      $("confirm-ok").onclick = null;
      $("confirm-cancel").onclick = null;
      overlay.onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(answer);
    };
    const onKey = (event) => {
      if (event.key === "Escape") finish(false);
    };

    $("confirm-ok").onclick = () => finish(true);
    $("confirm-cancel").onclick = () => finish(false);
    // Clicking the dimmed backdrop cancels, same as Escape.
    overlay.onclick = (event) => {
      if (event.target === overlay) finish(false);
    };
    document.addEventListener("keydown", onKey);
  });
}

/* ------------------------------------------------------------------ */
/* status + sidebar                                                    */
/* ------------------------------------------------------------------ */

async function loadStatus() {
  try {
    const status = await api("/api/status");

    // Branding is set by auth.js during bootstrap; keep the avatar in sync here
    // because the chat view renders assistant avatars from it.
    state.appName = (status.app_name || "").trim() || "图阅";
    state.avatarText = [...state.appName][0] || "AI";

    $("model-label").textContent = status.model;
    $("oda-badge").classList.toggle("hidden", status.oda_available);
    if (!status.oda_available) {
      $("oda-badge").textContent = "DWG 降级模式";
      $("oda-badge").classList.add("warn");
    }

    // Whether *this* account has a key is a per-user fact, so the banner points
    // at the settings panel rather than at a server-side .env file.
    let hasKey = false;
    try {
      hasKey = (await api("/api/settings")).has_key;
    } catch { /* settings are unavailable only if the session died */ }

    const banner = $("setup-banner");
    if (hasKey) {
      banner.classList.add("hidden");
    } else {
      banner.classList.remove("hidden");
      // Provider-neutral: the settings panel lets people pick any service.
      banner.innerHTML =
        "你还没有配置自己的 API Key，现在无法对话。点右上角的「设置」选一个服务商并填入你的 Key。";
    }

    const box = $("status-box");
    box.innerHTML = `
      <div class="${hasKey ? "ok" : "bad"}">
        ${hasKey ? "● API Key 已配置" : "● 未配置 API Key"}
      </div>
      <div class="${status.oda_available ? "ok" : "bad"}">
        ${status.oda_available ? "● ODA 转换器已就绪" : "● 未装 ODA 转换器"}
      </div>
      <div title="你上传的 PDF 页面和 DWG 图纸会被渲染成图片，连同提取出的文字一起发给模型。
受接口请求体大小限制，每轮最多送 ${status.max_images_per_turn} 张，超出部分只送文字。
这个上限可以在 .env 里通过 MAX_IMAGES_PER_TURN 调整。">每轮最多送 ${status.max_images_per_turn} 张渲染图</div>`;
  } catch (error) {
    $("status-box").textContent = `状态读取失败：${error.message}`;
  }
}

async function loadConversations() {
  const list = await api("/api/conversations");
  const nav = $("conversation-list");
  nav.innerHTML = "";

  for (const conv of list) {
    const item = document.createElement("div");
    item.className = "conv-item" + (conv.id === state.conversationId ? " active" : "");

    const label = document.createElement("span");
    label.textContent = conv.title;
    label.onclick = () => openConversation(conv.id);

    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.textContent = "×";
    del.title = `删除「${conv.title}」`;
    del.setAttribute("aria-label", del.title);
    del.onclick = async (event) => {
      event.stopPropagation();
      const confirmed = await confirmDialog({
        title: "删除对话",
        text: `「${conv.title}」及其中的消息将被永久删除，无法恢复。`,
        confirmLabel: "删除",
      });
      if (!confirmed) return;
      try {
        await api(`/api/conversations/${conv.id}`, { method: "DELETE" });
        if (conv.id === state.conversationId) startNewChat();
        loadConversations();
      } catch (error) {
        alert(`删除失败：${error.message}`);
      }
    };

    item.append(label, del);
    nav.append(item);
  }
}

/* ------------------------------------------------------------------ */
/* rendering                                                           */
/* ------------------------------------------------------------------ */

function fileChips(fileIds) {
  const wrap = document.createElement("div");
  wrap.className = "msg-files";
  for (const id of fileIds) {
    const cached = (state.fileCache ||= {})[id];
    const chip = document.createElement("div");
    chip.className = "file-chip";
    chip.innerHTML = `<span>📄</span><span>${escapeHtml(cached ? cached.filename : id)}</span>`;
    if (cached && cached.images.length) {
      const img = document.createElement("img");
      img.src = `/api/files/${id}/images/0`;
      img.alt = "";
      chip.prepend(img);
    }
    wrap.append(chip);
  }
  return wrap;
}

function appendMessage(role, content, fileIds, warnings) {
  const empty = $("messages").querySelector(".empty-state");
  if (empty) empty.remove();

  const node = document.createElement("div");
  node.className = `msg ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = role === "user" ? "我" : state.avatarText;

  const body = document.createElement("div");
  body.className = "msg-body";

  if (fileIds && fileIds.length) body.append(fileChips(fileIds));

  if (role === "user") {
    const text = document.createElement("div");
    text.className = "msg-text";
    text.textContent = content;
    body.append(text);
  } else {
    const text = document.createElement("div");
    text.className = "msg-text";
    text.innerHTML = renderMarkdown(content || "");
    body.append(text);
  }

  if (warnings && warnings.length) {
    const warn = document.createElement("div");
    warn.className = "msg-warnings";
    warn.textContent = warnings.join("；");
    body.append(warn);
  }

  node.append(avatar, body);
  $("messages").append(node);
  scrollToBottom();
  return node;
}

async function openConversation(id) {
  if (state.streaming) return;
  const data = await api(`/api/conversations/${id}`);
  state.conversationId = id;
  rememberLastConversation(id);
  $("chat-title").textContent = data.title;
  $("messages").innerHTML = "";
  state.fileCache = {};

  // Warm the filename/thumbnail cache so history renders with real names.
  const allIds = [...new Set(data.messages.flatMap((m) => m.file_ids || []))];
  await Promise.all(allIds.map(async (fileId) => {
    try {
      state.fileCache[fileId] = await api(`/api/files/${fileId}`);
    } catch { /* file may have been cleaned up */ }
  }));

  for (const message of data.messages) {
    appendMessage(message.role, message.content, message.file_ids);
  }

  // Re-select the files this conversation was about, so a follow-up question
  // still carries them.
  state.selectedIds = allIds.filter((fileId) => libraryEntry(fileId));
  renderFileLibrary();
  renderSelection();

  // Bring back the data table for those files, if one was ever extracted. This
  // reads the stored copy -- no model call.
  await restoreExtractionFor(allIds);

  loadConversations();
}

/** Show the stored extraction for these files, if there is one. */
async function restoreExtractionFor(fileIds) {
  panel.items = [];
  panel.fileIds = [];
  if (!fileIds.length) {
    setPanelVisible(false);
    return;
  }
  try {
    const cached = await api(`/api/extract?file_ids=${encodeURIComponent(fileIds.join(","))}`);
    if (cached?.items?.length) {
      applyExtraction(cached, { open: false });
      return;
    }
  } catch { /* nothing stored for this set */ }
  setPanelVisible(false);
}

function startNewChat() {
  state.conversationId = null;
  state.fileCache = {};
  forgetLastConversation();
  setPanelVisible(false);
  panel.items = [];
  panel.fileIds = [];
  clearSelection();
  $("chat-title").textContent = "新对话";
  // Same markup as the initial page, so a new chat does not look different
  // from a freshly loaded one.
  $("messages").innerHTML = `
    <div class="empty-state">
      <h2>上传 PDF 或 DWG，然后开始提问</h2>
      <p>支持 PDF 文档、DWG/DXF 图纸，也会渲染成图片让模型直接看图。</p>
      <div class="empty-hints" id="empty-hints"></div>
    </div>`;
  fillEmptyHints();
  loadConversations();
}

/* ------------------------------------------------------------------ */
/* uploads                                                             */
/* ------------------------------------------------------------------ */

/** Chips above the composer: the files chosen for the next message. */
function renderSelection() {
  const box = $("attachments");
  box.replaceChildren();

  const chosen = state.selectedIds.map(libraryEntry).filter(Boolean);
  if (!chosen.length) return;

  for (const item of chosen) {
    const chip = document.createElement("div");
    chip.className = "file-chip";

    if (item.thumbnail) {
      const thumb = document.createElement("img");
      thumb.src = item.thumbnail;
      thumb.alt = "";
      chip.append(thumb);
    } else {
      const icon = document.createElement("span");
      icon.textContent = "📄";
      chip.append(icon);
    }

    const label = document.createElement("span");
    label.textContent = item.filename;
    chip.append(label);

    // Extraction sits with the file, not in a toolbar far away from it.
    const extract = document.createElement("button");
    extract.type = "button";
    extract.className = "chip-action";
    extract.textContent = "提取数据";
    extract.title = "让模型读出这份文件里的数据";
    extract.onclick = () => extractFor([item.file_id]);
    chip.append(extract);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-remove";
    remove.textContent = "×";
    remove.title = "取消选择";
    remove.onclick = () => toggleFileSelection(item.file_id);
    chip.append(remove);

    box.append(chip);
  }
}

/** Upload into the library. Nothing is attached to a message until you pick it. */
async function uploadFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;

  for (const file of files) {
    const status = document.createElement("div");
    status.className = "file-chip";
    status.textContent = `⏳ 正在解析 ${file.name}…`;
    $("attachments").append(status);

    try {
      const form = new FormData();
      form.append("file", file);
      const record = await api("/api/upload", { method: "POST", body: form });
      (state.fileCache ||= {})[record.file_id] = record;

      status.textContent = `✓ ${file.name} 已存入文件库`;
      status.classList.add("chip-ok");
      setTimeout(() => status.remove(), 4000);
    } catch (error) {
      status.classList.remove("chip-ok");
      status.style.borderColor = "var(--danger)";
      status.textContent = `✕ ${file.name}：${error.message}`;
      setTimeout(() => status.remove(), 8000);
    }
  }

  await loadFileLibrary();
}

/* ------------------------------------------------------------------ */
/* sending                                                             */
/* ------------------------------------------------------------------ */

async function send() {
  if (state.streaming) return;

  const input = $("input");
  const text = input.value.trim();
  if (!text && !state.selectedIds.length) return;

  if (!state.conversationId && !text) {
    alert("请先输入一个问题");
    return;
  }

  const fileIds = [...state.selectedIds];
  const warnings = fileIds
    .map(libraryEntry)
    .filter(Boolean)
    .flatMap((item) => item.warnings || []);

  appendMessage("user", text, fileIds, warnings);
  input.value = "";
  input.style.height = "auto";
  // The selection is kept on purpose: a follow-up question is about the same
  // files, and the model has no memory of them between calls.
  setStreaming(true);

  const answerNode = appendMessage("assistant", "", []);
  const answerText = answerNode.querySelector(".msg-text");
  answerText.classList.add("cursor");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        conversation_id: state.conversationId,
        file_ids: fileIds,
      }),
    });

    if (!response.ok) {
      throw new Error(`服务返回 ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let accumulated = "";
    let hadError = false;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      buffer = events.pop();

      for (const raw of events) {
        const line = raw.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));

        if (event.type === "start") {
          state.conversationId = event.conversation_id;
          // A brand new conversation is created server-side, so this is the
          // first place its id is known -- remember it for the next visit.
          rememberLastConversation(state.conversationId);
          $("chat-title").textContent = event.title;
        } else if (event.type === "delta") {
          accumulated += event.text;
          answerText.innerHTML = renderMarkdown(accumulated);
          scrollToBottom();
        } else if (event.type === "notice") {
          const notice = document.createElement("div");
          notice.className = "notice";
          notice.textContent = event.text;
          answerNode.querySelector(".msg-body").prepend(notice);
        } else if (event.type === "error") {
          hadError = true;
          answerText.classList.remove("cursor");
          answerText.innerHTML = `<span style="color:var(--danger)">${escapeHtml(event.text)}</span>`;
        }
      }
    }

    answerText.classList.remove("cursor");
    if (!hadError && !accumulated) answerText.textContent = "（模型没有返回内容）";
    loadConversations();

    // Extraction is deliberately NOT triggered here. It is a second model call
    // over the same images, and the data is only useful when someone asks for
    // it -- the button next to the file is where that happens.
  } catch (error) {
    answerText.classList.remove("cursor");
    answerText.innerHTML = `<span style="color:var(--danger)">请求失败：${escapeHtml(error.message)}</span>`;
  } finally {
    setStreaming(false);
  }
}

/* ------------------------------------------------------------------ */
/* wiring                                                              */
/* ------------------------------------------------------------------ */

const input = $("input");
input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});

$("send").onclick = send;
$("new-chat").onclick = startNewChat;

// initPanelResizer() is called at the very end of this file, not here: it reads
// constants that are declared further down, and `const` is not hoisted.

$("panel-close").onclick = () => setPanelVisible(false);
$("panel-reload").onclick = () => {
  if (panel.fileIds.length) extractFor([...panel.fileIds], { force: true });
};
$("panel-filter").addEventListener("input", (event) => {
  panel.filter = event.target.value;
  renderPanel();
});
for (const header of document.querySelectorAll(".panel-table th")) {
  header.onclick = () => {
    const key = header.dataset.sort;
    // Same column again flips direction; a new column starts ascending.
    if (panel.sortKey === key) {
      panel.sortDir = -panel.sortDir;
    } else {
      panel.sortKey = key;
      panel.sortDir = 1;
    }
    renderPanel();
  };
}
$("file-input").onchange = (event) => {
  uploadFiles(event.target.files);
  event.target.value = "";
};

const dropZone = $("drop-zone");
["dragenter", "dragover"].forEach((name) =>
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  })
);
["dragleave", "drop"].forEach((name) =>
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  })
);
dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));

// Paste a screenshot straight into the composer.
input.addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.files || []);
  if (files.length) {
    event.preventDefault();
    uploadFiles(files);
  }
});

/* ------------------------------------------------------------------ */
/* right-hand data panel                                               */
/* ------------------------------------------------------------------ */

// The order the categories are shown in by default; anything unexpected sorts
// to the end rather than jumping to the top.
const CATEGORY_ORDER = ["尺寸标注", "技术要求", "图纸信息", "图层与块"];

const panel = {
  items: [],
  fileIds: [],
  sortKey: "category",
  sortDir: 1,
  filter: "",
  busy: false,
};

const PANEL_MIN_WIDTH = 320;   // keep the table readable
const PANEL_MIN_CHAT = 420;    // room for the header, composer and messages
const PANEL_WIDTH_KEY = "yue.panelWidth";
const LAST_CONVERSATION_KEY = "yue.lastConversation";

/** Which conversation to reopen on the next visit. */
function rememberLastConversation(id) {
  try {
    if (id) localStorage.setItem(LAST_CONVERSATION_KEY, id);
    else localStorage.removeItem(LAST_CONVERSATION_KEY);
  } catch { /* storage can be blocked; the session just will not be restored */ }
}

function forgetLastConversation() {
  rememberLastConversation(null);
}

function lastConversationId() {
  try {
    return localStorage.getItem(LAST_CONVERSATION_KEY) || "";
  } catch {
    return "";
  }
}

function setPanelVisible(visible) {
  $("data-panel").classList.toggle("hidden", !visible);
  // The grab strip belongs to the panel, so it comes and goes with it.
  $("panel-resizer").classList.toggle("hidden", !visible);
  // Restoring has to wait until here: the app sits behind a login gate at load,
  // and a hidden layout has no width to clamp the saved size against.
  if (visible) restorePanelWidth();
}

function applyPanelWidth(px) {
  const shell = document.querySelector(".layout");
  if (!shell.clientWidth) return; // nothing to measure against yet

  // The sidebar and the grab strip are siblings of the panel, so the space left
  // for the chat is the shell minus those minus the panel itself. Ignoring the
  // sidebar here let the panel grow until the header stacked vertically.
  const sidebar = document.querySelector(".sidebar");
  const resizer = $("panel-resizer");
  const taken = (sidebar?.offsetWidth || 0) + (resizer?.offsetWidth || 0);

  const widest = Math.max(PANEL_MIN_WIDTH, shell.clientWidth - taken - PANEL_MIN_CHAT);
  const width = Math.min(Math.max(px, PANEL_MIN_WIDTH), widest);

  const target = $("data-panel");
  // Both properties: flex-basis decides the size, max-width stops the CSS rule
  // from pinning it back to half the window.
  target.style.flexBasis = `${width}px`;
  target.style.maxWidth = `${width}px`;

  // Drop the username before the toolbar starts wrapping.
  document.body.classList.toggle(
    "tight-header", shell.clientWidth - taken - width < PANEL_MIN_CHAT + 140
  );
}

function initPanelResizer() {
  const handle = $("panel-resizer");
  const shell = document.querySelector(".layout");
  let dragging = false;

  handle.addEventListener("mousedown", (event) => {
    event.preventDefault();
    dragging = true;
    handle.classList.add("dragging");
    // Stops the cursor flickering and the page selecting text mid-drag.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    applyPanelWidth(shell.getBoundingClientRect().right - event.clientX);
  });

  const stop = () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    try {
      localStorage.setItem(PANEL_WIDTH_KEY, $("data-panel").style.flexBasis);
    } catch { /* storage can be unavailable; the width just will not persist */ }
  };
  window.addEventListener("mouseup", stop);

  handle.addEventListener("dblclick", () => {
    $("data-panel").style.flexBasis = "";
    $("data-panel").style.maxWidth = "";
    try { localStorage.removeItem(PANEL_WIDTH_KEY); } catch { /* fine */ }
  });

}

let panelWidthRestored = false;

/** Put back the width chosen last time, once there is a layout to apply it to. */
function restorePanelWidth() {
  if (panelWidthRestored) return;
  // Only the storage read is guarded: wrapping the apply as well would hide
  // real bugs behind a silent catch, which is how this stayed broken once.
  let saved = NaN;
  try {
    saved = parseInt(localStorage.getItem(PANEL_WIDTH_KEY) || "", 10);
  } catch { /* storage can be blocked; there is simply nothing to restore */ }
  if (Number.isFinite(saved)) applyPanelWidth(saved);
  panelWidthRestored = true;
}

function formatNumber(value) {
  if (value === null || value === undefined) return "";
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000);
}

function compareBy(a, b, key) {
  if (key === "category") {
    const indexOf = (item) => {
      const found = CATEGORY_ORDER.indexOf(item.category);
      return found === -1 ? CATEGORY_ORDER.length : found;
    };
    return indexOf(a) - indexOf(b);
  }
  return String(a[key] ?? "").localeCompare(String(b[key] ?? ""), "zh-Hans-CN");
}

function sortItems(items, key, direction) {
  return [...items].sort((a, b) => {
    // Rows with no number stay at the bottom either way: on a dimension list a
    // blank sorting above "10" would be actively misleading.
    if (key === "value") {
      const aBlank = a.value === null || a.value === undefined;
      const bBlank = b.value === null || b.value === undefined;
      if (aBlank || bBlank) {
        if (aBlank && bBlank) return 0;
        return aBlank ? 1 : -1;
      }
      return (a.value - b.value) * direction;
    }
    return compareBy(a, b, key) * direction;
  });
}

function updateSortIndicators() {
  for (const header of document.querySelectorAll(".panel-table th")) {
    const active = header.dataset.sort === panel.sortKey;
    header.classList.toggle("sorted", active);
    header.dataset.dir = active ? (panel.sortDir === 1 ? "asc" : "desc") : "";
  }
}

function renderPanel() {
  const needle = panel.filter.trim().toLowerCase();
  const matching = needle
    ? panel.items.filter((item) =>
        [item.category, item.name, item.detail, item.note, item.source]
          .some((field) => String(field ?? "").toLowerCase().includes(needle)))
    : panel.items;

  const rows = sortItems(matching, panel.sortKey, panel.sortDir);
  const body = $("panel-tbody");
  body.replaceChildren();

  for (const item of rows) {
    const tr = document.createElement("tr");
    if (item.category === "尺寸标注") tr.classList.add("row-dimension");

    const cells = [
      item.category,
      item.name,
      item.value === null || item.value === undefined ? "" : formatNumber(item.value),
      item.unit,
      item.detail,
      item.note,
      item.source,
    ];
    cells.forEach((value, index) => {
      const td = document.createElement("td");
      td.textContent = value;
      if (index === 2) td.className = "numeric";
      tr.append(td);
    });
    body.append(tr);
  }

  $("panel-count").textContent = needle
    ? `${rows.length} / ${panel.items.length} 条`
    : `${panel.items.length} 条`;
  updateSortIndicators();
}

async function showPanelImage(fileIds) {
  const strip = $("panel-files");
  const image = $("panel-image");
  strip.replaceChildren();

  const records = [];
  for (const fileId of fileIds) {
    let record = state.fileCache?.[fileId];
    if (!record) {
      try {
        record = await api(`/api/files/${fileId}`);
        (state.fileCache ||= {})[fileId] = record;
      } catch { /* the preview is optional; the table still works */ }
    }
    if (record) records.push(record);
  }

  const show = (record) => {
    const source = record?.images?.length ? `/api/files/${record.file_id}/images/0` : "";
    if (source) {
      image.src = source;
      image.classList.remove("hidden");
    } else {
      image.removeAttribute("src");
      image.classList.add("hidden");
    }
  };

  if (records.length > 1) {
    records.forEach((record, index) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "panel-file" + (index === 0 ? " active" : "");
      chip.textContent = record.filename;
      chip.onclick = () => {
        show(record);
        for (const sibling of strip.children) sibling.classList.remove("active");
        chip.classList.add("active");
      };
      strip.append(chip);
    });
  }

  show(records[0]);
}

/** Paint the panel from an extraction result, wherever it came from. */
function applyExtraction(data, { open = true } = {}) {
  panel.items = data.items || [];
  panel.fileIds = data.file_ids || [];
  panel.filter = "";
  $("panel-filter").value = "";

  const notices = data.notices || [];
  $("panel-notices").textContent = notices.join("；");
  $("panel-notices").classList.toggle("hidden", !notices.length);

  if (open) setPanelVisible(true);
  showPanelImage(panel.fileIds);
  renderPanel();
  updateReloadButton();
}

/* Offered whenever the table is about known files -- a cached read wants a
   re-read as much as a fresh one does, and a failed read wants a retry. */
function updateReloadButton() {
  $("panel-reload").classList.toggle("hidden", !panel.fileIds.length);
}

async function cachedExtraction(fileIds) {
  try {
    const query = encodeURIComponent(fileIds.join(","));
    return await api(`/api/extract?file_ids=${query}`);
  } catch {
    return {};
  }
}

/**
 * Show the data for these files.
 *
 * Checks the stored copy first: the same drawings rarely need reading twice,
 * and a second read costs another model call over the same images. Pass
 * `force` to read them again anyway.
 */
async function extractFor(fileIds, { force = false } = {}) {
  if (panel.busy || !fileIds.length) return;
  panel.busy = true;
  panel.fileIds = fileIds;
  panel.items = [];
  panel.filter = "";
  $("panel-filter").value = "";
  $("panel-tbody").replaceChildren();
  $("panel-notices").classList.add("hidden");
  $("panel-reload").classList.add("hidden");
  $("panel-count").textContent = "正在读取…";
  setPanelVisible(true);

  showPanelImage(fileIds);

  try {
    if (!force) {
      const cached = await cachedExtraction(fileIds);
      if (cached?.items?.length) {
        applyExtraction(cached);
        return;
      }
    }

    const data = await api("/api/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_ids: fileIds }),
    });
    applyExtraction({ ...data, file_ids: fileIds });
  } catch (error) {
    $("panel-count").textContent = "";
    const box = $("panel-notices");
    box.textContent = `提取失败：${error.message}`;
    box.classList.remove("hidden");
    updateReloadButton();
  } finally {
    panel.busy = false;
  }
}

/* Called by auth.js once a session is confirmed. Nothing here runs on load,
   because every endpoint below now requires an authenticated account. */
function startApp() {
  loadStatus();
  loadFileLibrary();
  loadConversations();
  fillEmptyHints();
  restoreSession();
  input.focus();
}

/** Bring back what was on screen last time: the conversation, and its table. */
async function restoreSession() {
  const lastId = lastConversationId();
  if (lastId) {
    try {
      await openConversation(lastId);
      return;
    } catch {
      // Deleted from another tab, or the id is stale; just start clean.
      forgetLastConversation();
    }
  }

  // No conversation to reopen. Nothing is selected on purpose: which drawings
  // to talk about is the user's call, and a pre-ticked file would decide it
  // for them.
}

// Last line of the file on purpose: the resizer reads module constants, and
// calling it before they are initialised throws inside a try/catch and fails
// silently -- which is exactly how the width restore stayed broken.
initPanelResizer();
