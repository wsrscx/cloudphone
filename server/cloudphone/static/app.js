"use strict";

let csrfToken = "";
const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) {
    showLogin();
    throw new Error("请先登录");
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail?.message || "请求失败 (" + response.status + ")");
  }
  return response.status === 204 ? null : response.json();
}

function showLogin() {
  const dialog = $("#login-dialog");
  if (!dialog.open) dialog.showModal();
  $("#password").focus();
}

async function load() {
  try {
    const [status, listing] = await Promise.all([
      api("/api/admin/status"),
      api("/api/admin/devices"),
    ]);
    $("#server-address").textContent = status.publicAddress + " / " + status.streamTransport;
    $("#binding").textContent = status.clientBound
      ? "已绑定 " + status.boundClientId
      : "尚未绑定客户端";
    $("#session-state").textContent = status.activeSession?.state || "无";
    renderDevices(listing.devices);
  } catch (error) {
    if (error.message !== "请先登录") notify(error.message, true);
  }
}

function renderDevices(devices) {
  const list = $("#device-list");
  list.replaceChildren();
  $("#empty").hidden = devices.length !== 0;
  $("#device-count").textContent = String(devices.length);
  $("#authorized-count").textContent = String(devices.filter((item) => item.authorized).length);
  for (const device of devices) {
    const row = document.createElement("tr");
    const online = device.state === "device";
    row.innerHTML =
      '<td><input class="device-name" maxlength="80"><div class="minor"></div></td>' +
      '<td><div class="android"></div></td>' +
      '<td><span class="state"></span></td>' +
      '<td><span class="assistant"></span></td>' +
      '<td><label class="toggle"><input type="checkbox"><span></span></label></td>';
    const name = row.querySelector(".device-name");
    name.value = device.name;
    row.querySelector(".minor").textContent = device.model || "未知型号";
    row.querySelector(".android").textContent = device.androidVersion
      ? "Android " + device.androidVersion
      : "未知";
    const state = row.querySelector(".state");
    state.textContent = online
      ? "在线"
      : device.state === "unauthorized" ? "等待 USB 授权" : "离线";
    state.classList.toggle("online", online);
    row.querySelector(".assistant").textContent = device.assistantReady ? "已连接" : "未连接";
    const toggle = row.querySelector('input[type="checkbox"]');
    toggle.checked = device.authorized;
    toggle.disabled = !online && !device.authorized;
    name.addEventListener("change", () => updateDevice(device.id, { alias: name.value }));
    toggle.addEventListener("change", () => updateDevice(device.id, { authorized: toggle.checked }));
    list.append(row);
  }
}

async function updateDevice(id, change) {
  try {
    await api("/api/admin/devices/" + encodeURIComponent(id), {
      method: "PUT",
      body: JSON.stringify(change),
    });
    notify("设备设置已保存");
    await load();
  } catch (error) {
    notify(error.message, true);
    await load();
  }
}

function notify(message, error = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.style.color = error ? "#a72828" : "#167251";
  window.setTimeout(() => {
    if (notice.textContent === message) notice.textContent = "";
  }, 3500);
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    const result = await fetch("/api/admin/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("#password").value }),
    });
    if (!result.ok) throw new Error("管理密码错误");
    csrfToken = (await result.json()).csrfToken;
    $("#password").value = "";
    $("#login-dialog").close();
    await load();
  } catch (error) {
    $("#login-error").textContent = error.message;
  }
});

$("#refresh").addEventListener("click", async () => {
  try {
    await api("/api/admin/devices/refresh", { method: "POST" });
    await load();
    notify("设备列表已刷新");
  } catch (error) {
    notify(error.message, true);
  }
});

$("#reset-client").addEventListener("click", async () => {
  if (!window.confirm("重置后，当前客户端需要重新完成首次绑定。确定继续？")) return;
  try {
    await api("/api/admin/client/reset", { method: "POST" });
    await load();
    notify("客户端绑定已重置");
  } catch (error) {
    notify(error.message, true);
  }
});

showLogin();
