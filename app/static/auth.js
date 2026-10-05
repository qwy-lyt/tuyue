/* Login gate, registration, two-factor setup, and the per-user settings panel.
   Loaded after app.js, which provides $, api, escapeHtml and the chat startup. */

const auth = {
  user: null,
};

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

function showAuthMessage(kind, text) {
  const box = kind === "error" ? $("auth-error") : $("auth-notice");
  const other = kind === "error" ? $("auth-notice") : $("auth-error");
  other.classList.add("hidden");
  box.textContent = text || "";
  box.classList.toggle("hidden", !text);
}

function showLoginForm() {
  $("login-form").classList.remove("hidden");
  $("register-form").classList.add("hidden");
  $("to-register").classList.remove("hidden");
  $("to-login").classList.add("hidden");
  showAuthMessage("error", "");
  $("login-username").focus();
}

function showRegisterForm() {
  $("login-form").classList.add("hidden");
  $("register-form").classList.remove("hidden");
  $("to-register").classList.add("hidden");
  $("to-login").classList.remove("hidden");
  showAuthMessage("error", "");
  $("register-username").focus();
}

function enterApp(user) {
  auth.user = user;
  $("auth-screen").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("current-user").textContent = user.username;
  $("open-admin").classList.toggle("hidden", !user.is_admin);
  startApp();
}


function leaveApp() {
  auth.user = null;
  $("app").classList.add("hidden");
  $("auth-screen").classList.remove("hidden");
  $("current-user").textContent = "";
  showLoginForm();
}

/* ------------------------------------------------------------------ */
/* login / register                                                    */
/* ------------------------------------------------------------------ */

async function submitLogin(event) {
  if (event) event.preventDefault();
  await doLogin();
}

async function doLogin() {
  const button = $("login-submit");
  button.disabled = true;
  showAuthMessage("error", "");

  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("login-username").value.trim(),
        password: $("login-password").value,
      }),
    });

    $("login-password").value = "";
    enterApp(data);
  } catch (error) {
    showAuthMessage("error", error.message);
  } finally {
    button.disabled = false;
  }
}

async function submitRegister(event) {
  event.preventDefault();
  showAuthMessage("error", "");

  const username = $("register-username").value.trim();
  const password = $("register-password").value;
  const confirm = $("register-password2").value;

  if (password !== confirm) {
    showAuthMessage("error", "两次输入的密码不一致");
    return;
  }

  try {
    const user = await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username,
        password,
        invite_code: $("register-invite").value.trim(),
      }),
    });
    $("register-password").value = "";
    $("register-password2").value = "";
    $("register-invite").value = "";
    enterApp(user);
  } catch (error) {
    showAuthMessage("error", error.message);
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    /* the session is gone either way */
  }
  leaveApp();
}

/* ------------------------------------------------------------------ */
/* settings                                                            */
/* ------------------------------------------------------------------ */

function providerById(id) {
  return auth.providers.find((item) => item.id === id) || auth.providers[0];
}

function renderModelOptions(providerId, selected) {
  const provider = providerById(providerId);
  const select = $("settings-model-select");
  select.replaceChildren();

  const models = provider ? provider.models : [];
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    // Flag the ones that cannot see, since this app leans on images.
    option.textContent = model.vision ? model.name : `${model.name}（不支持看图）`;
    select.append(option);
  }
  if (selected && models.some((m) => m.id === selected)) {
    select.value = selected;
  }

  // A provider with no listed models is the escape hatch.
  const custom = !models.length;
  $("settings-model-field").classList.toggle("hidden", custom);
  $("settings-custom-fields").classList.toggle("hidden", !custom);
  $("settings-provider-note").textContent = provider?.note || "";
  $("settings-key-link").innerHTML = provider?.key_url
    ? `Key 从这里获取：<a href="${provider.key_url}" target="_blank" rel="noopener">${provider.key_url}</a>`
    : "";
}

function updateVisionWarning() {
  const provider = providerById($("settings-provider").value);
  const box = $("settings-vision-warning");

  let vision = null;
  if (provider && provider.models.length) {
    const chosen = provider.models.find((m) => m.id === $("settings-model-select").value);
    vision = chosen ? chosen.vision : null;
  }

  if (vision === false) {
    box.textContent = "这个模型不能读图片，图纸和扫描件里画出来的内容它会看不到，"
      + "只能根据提取出的文字回答。";
    box.classList.remove("hidden");
  } else {
    box.classList.add("hidden");
  }
}

async function openSettings() {
  $("settings-error").classList.add("hidden");
  $("settings-ok").classList.add("hidden");
  $("settings-key").value = "";

  try {
    if (!auth.providers) {
      auth.providers = await api("/api/providers");
      const select = $("settings-provider");
      select.replaceChildren();
      for (const provider of auth.providers) {
        const option = document.createElement("option");
        option.value = provider.id;
        option.textContent = provider.name;
        select.append(option);
      }
    }

    const data = await api("/api/settings");
    $("settings-provider").value = data.provider;
    $("settings-base-url").value = data.base_url || "";
    $("settings-model").value = data.model || "";
    $("settings-base-url").placeholder = data.default_base_url;
    $("settings-model").placeholder = data.default_model;
    $("settings-key-state").textContent = data.has_key ? "（已保存）" : "（尚未设置）";

    renderModelOptions(data.provider, data.model || data.default_model);
    updateVisionWarning();
  } catch (error) {
    $("settings-error").textContent = error.message;
    $("settings-error").classList.remove("hidden");
  }

  // Appearance lives in this browser rather than on the account, so it is read
  // from what theme.js stored instead of from the settings endpoint.
  $("settings-theme").value = window.appTheme ? window.appTheme.choice() : "system";

  $("settings-overlay").classList.remove("hidden");
  $("settings-key").focus();
}

async function saveSettings() {
  const errorBox = $("settings-error");
  const okBox = $("settings-ok");
  errorBox.classList.add("hidden");
  okBox.classList.add("hidden");

  const provider = providerById($("settings-provider").value);
  const isCustom = !provider.models.length;

  const typed = $("settings-key").value.trim();
  // Picking a provider fills in its address and the chosen model; "自定义"
  // leaves both to the text inputs.
  const body = isCustom
    ? {
        base_url: $("settings-base-url").value.trim(),
        model: $("settings-model").value.trim(),
      }
    : { base_url: provider.base_url, model: $("settings-model-select").value };

  // Only touch the stored key when something was actually typed.
  if (typed) body.api_key = typed;

  try {
    const result = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("settings-key").value = "";
    $("settings-key-state").textContent = result.has_key ? "（已保存）" : "（尚未设置）";
    okBox.textContent = "已保存";
    okBox.classList.remove("hidden");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

async function verifySettings() {
  const errorBox = $("settings-error");
  const okBox = $("settings-ok");
  errorBox.classList.add("hidden");
  okBox.classList.add("hidden");
  okBox.textContent = "正在测试…";
  okBox.classList.remove("hidden");

  try {
    await api("/api/settings/verify", { method: "POST" });
    okBox.textContent = "连通正常，模型有响应";
  } catch (error) {
    okBox.classList.add("hidden");
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

/* ------------------------------------------------------------------ */
/* bootstrap                                                           */
/* ------------------------------------------------------------------ */

async function bootstrap() {
  let status = {};
  try {
    status = await api("/api/status");
  } catch {
    /* fall through with defaults */
  }

  const brand = status.app_name || "图阅";
  document.title = brand;
  $("auth-name").textContent = brand;
  $("auth-mark").textContent = [...brand][0] || "图";
  $("brand-name").textContent = brand;
  $("brand-mark").textContent = [...brand][0] || "AI";

  if (!status.registration_open) {
    $("to-register").classList.add("hidden");
  }

  try {
    const user = await api("/api/auth/me");
    enterApp(user);
  } catch {
    $("auth-screen").classList.remove("hidden");
    showLoginForm();
  }
}

/* ------------------------------------------------------------------ */
/* wiring                                                              */
/* ------------------------------------------------------------------ */

$("login-form").addEventListener("submit", submitLogin);
$("register-form").addEventListener("submit", submitRegister);
$("to-register").onclick = (event) => { event.preventDefault(); showRegisterForm(); };
$("to-login").onclick = (event) => { event.preventDefault(); showLoginForm(); };
$("logout").onclick = logout;
$("open-settings").onclick = openSettings;
$("settings-provider").addEventListener("change", () => {
  renderModelOptions($("settings-provider").value, "");
  updateVisionWarning();
});
$("settings-model-select").addEventListener("change", updateVisionWarning);
$("settings-save").onclick = saveSettings;
$("settings-verify").onclick = verifySettings;
$("settings-cancel").onclick = () => $("settings-overlay").classList.add("hidden");

/* Appearance takes effect the moment it is picked -- there is nothing to save. */
$("settings-theme").addEventListener("change", () => {
  window.appTheme.set($("settings-theme").value);
});

bootstrap();
