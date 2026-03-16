const state = {
  mode: "unknown",
  envs: [],
  favorites: [],
  home: null,
  pollTimer: null,
  pushTimer: null,
  realtime: {
    initialized: false,
    lastSignalKey: "",
  },
};

function showToast(text) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = text;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2200);
}

async function apiGet(url) {
  const resp = await fetch(url, { headers: { "Content-Type": "application/json" } });
  return resp.json();
}

async function apiPost(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  return resp.json();
}

function tableHtml(columns, rows) {
  const head = columns.map((c) => `<th>${c.label}</th>`).join("");
  const body = rows
    .map(
      (row) =>
        `<tr>${columns
          .map((c) => `<td>${row[c.key] === undefined || row[c.key] === null ? "" : row[c.key]}</td>`)
          .join("")}</tr>`
    )
    .join("");
  return `<table class="table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderHome(data) {
  state.home = data;
  const runtimeMode = document.getElementById("runtimeMode");
  if (runtimeMode) runtimeMode.textContent = String(data.mode || "mock").toUpperCase();

  const gatewaySelect = document.getElementById("gatewaySelect");
  if (gatewaySelect) {
    gatewaySelect.innerHTML = "";
    (data.gateways || []).forEach((gw) => {
      const op = document.createElement("option");
      op.value = gw;
      op.textContent = gw;
      gatewaySelect.appendChild(op);
    });
  }

  const tabAccounts = document.getElementById("tab-accounts");
  if (tabAccounts) tabAccounts.innerHTML = tableHtml(
    [
      { key: "accountid", label: "账号" },
      { key: "balance", label: "余额" },
      { key: "frozen", label: "冻结" },
      { key: "available", label: "可用" },
      { key: "gateway_name", label: "账户" },
    ],
    data.accounts || []
  );

  const tabPortfolio = document.getElementById("tab-portfolio");
  if (tabPortfolio) tabPortfolio.innerHTML =
    `<h4>组合盈亏</h4>` +
    tableHtml(
      [
        { key: "reference", label: "组合" },
        { key: "gateway_name", label: "账户" },
        { key: "trading_pnl", label: "交易盈亏" },
        { key: "holding_pnl", label: "持仓盈亏" },
        { key: "total_pnl", label: "总盈亏" },
        { key: "commission", label: "手续费" },
      ],
      data.portfolio_totals || []
    ) +
    `<h4>合约盈亏</h4>` +
    tableHtml(
      [
        { key: "reference", label: "组合" },
        { key: "vt_symbol", label: "合约" },
        { key: "gateway_name", label: "账户" },
        { key: "last_pos", label: "当前仓位" },
        { key: "total_pnl", label: "总盈亏" },
        { key: "commission", label: "手续费" },
      ],
      data.portfolio_contracts || []
    );

  const tabRisk = document.getElementById("tab-risk");
  if (tabRisk) tabRisk.innerHTML = tableHtml(
    [
      { key: "name", label: "规则" },
      { key: "parameters", label: "参数" },
      { key: "variables", label: "变量" },
    ],
    (data.risk_rules || []).map((x) => ({
      name: x.name || "",
      parameters: JSON.stringify(x.parameters || {}),
      variables: JSON.stringify(x.variables || {}),
    }))
  );

  const tabLogs = document.getElementById("tab-logs");
  if (tabLogs) tabLogs.innerHTML = tableHtml(
    [
      { key: "time", label: "时间" },
      { key: "level", label: "级别" },
      { key: "source", label: "来源" },
      { key: "msg", label: "信息" },
    ],
    (data.logs || []).slice().reverse()
  );

  const tabPositions = document.getElementById("tab-positions");
  if (tabPositions) tabPositions.innerHTML = tableHtml(
    [
      { key: "symbol", label: "代码" },
      { key: "direction", label: "方向" },
      { key: "volume", label: "数量" },
      { key: "frozen", label: "冻结" },
      { key: "price", label: "均价" },
      { key: "pnl", label: "盈亏" },
      { key: "gateway_name", label: "账户" },
    ],
    data.positions || []
  );

  const tabOrders = document.getElementById("tab-orders");
  if (tabOrders) tabOrders.innerHTML = tableHtml(
    [
      { key: "symbol", label: "代码" },
      { key: "direction", label: "方向" },
      { key: "offset", label: "开平" },
      { key: "price", label: "价格" },
      { key: "volume", label: "数量" },
      { key: "traded", label: "已成交" },
      { key: "status", label: "状态" },
      { key: "datetime", label: "时间" },
      { key: "gateway_name", label: "账户" },
    ],
    data.orders || []
  );

  const tabTrades = document.getElementById("tab-trades");
  if (tabTrades) tabTrades.innerHTML = tableHtml(
    [
      { key: "symbol", label: "代码" },
      { key: "direction", label: "方向" },
      { key: "offset", label: "开平" },
      { key: "price", label: "价格" },
      { key: "volume", label: "数量" },
      { key: "datetime", label: "时间" },
      { key: "gateway_name", label: "账户" },
    ],
    data.trades || []
  );
}

async function refreshHome() {
  const resp = await apiGet("/api/home");
  if (resp.ok) {
    renderHome(resp.data);
  }
}

function bindTabs() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabName = btn.dataset.tab;
      const parent = btn.closest(".panel");
      parent.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      parent.querySelectorAll(".tab-pane").forEach((pane) => pane.classList.remove("active"));
      btn.classList.add("active");
      const pane = parent.querySelector(`#tab-${tabName}`);
      if (pane) pane.classList.add("active");
    });
  });
}

function activatePanelTab(panel, tabName) {
  if (!panel || !tabName) return;
  const tabBtn = panel.querySelector(`.tab[data-tab="${tabName}"]`);
  const tabPane = panel.querySelector(`#tab-${tabName}`);
  if (!tabBtn || !tabPane) return;
  panel.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
  panel.querySelectorAll(".tab-pane").forEach((pane) => pane.classList.remove("active"));
  tabBtn.classList.add("active");
  tabPane.classList.add("active");
}

function bindModuleCards() {
  document.querySelectorAll(".card-btn").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      const href = btn.getAttribute("href") || "";
      if (href && !href.startsWith("#")) {
        const clickStatus = document.getElementById("clickStatus");
        if (clickStatus) {
          clickStatus.textContent = `进入模块 ${btn.textContent.trim()}...`;
        }
        return;
      }

      event.preventDefault();
      const target = btn.dataset.target || "#panel-trading";
      const panel = document.querySelector(target);
      if (!panel) {
        showToast("页面区块不存在");
        return;
      }
      document.querySelectorAll(".card-btn").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      activatePanelTab(panel, btn.dataset.tab || "");
      const hash = target.startsWith("#") ? target : `#${target}`;
      window.location.hash = hash;
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      panel.classList.remove("flash");
      // Reflow to restart animation.
      // eslint-disable-next-line no-unused-expressions
      panel.offsetHeight;
      panel.classList.add("flash");
      const txt = `已定位到 ${btn.textContent.trim()}`;
      const clickStatus = document.getElementById("clickStatus");
      if (clickStatus) {
        clickStatus.textContent = txt;
      }
      showToast(txt);
    });
  });
}

function bindClock() {
  const el = document.getElementById("runtimeClock");
  if (!el) return;
  setInterval(() => {
    const now = new Date();
    const parts = [now.getHours(), now.getMinutes(), now.getSeconds()].map((x) => String(x).padStart(2, "0"));
    el.textContent = parts.join(":");
  }, 1000);
}

function bindForm() {
  const symbolSelect = document.getElementById("symbolSelect");
  const exchangeInput = document.getElementById("exchangeInput");
  if (symbolSelect && exchangeInput) {
    symbolSelect.addEventListener("change", (e) => {
      const selected = state.favorites.find((x) => x.symbol === e.target.value);
      if (selected) {
        exchangeInput.value = selected.exchange || "";
      }
    });
  }

  const orderForm = document.getElementById("orderForm");
  if (orderForm) {
    orderForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payload = {
        symbol: document.getElementById("symbolSelect")?.value || "",
        exchange: document.getElementById("exchangeInput")?.value || "",
        direction: document.getElementById("directionInput")?.value || "多",
        offset: document.getElementById("offsetInput")?.value || "开",
        type: document.getElementById("typeInput")?.value || "限价",
        price: Number(document.getElementById("priceInput")?.value || 0),
        volume: Number(document.getElementById("volumeInput")?.value || 1),
        gateway: document.getElementById("gatewaySelect")?.value || "",
      };
      const resp = await apiPost("/api/order", payload);
      if (resp.ok) {
        showToast(`委托成功: ${resp.vt_orderid || ""}`);
        refreshHome();
        return;
      }
      showToast(`委托失败: ${resp.error || "未知错误"}`);
    });
  }

  const btnCancelAll = document.getElementById("btnCancelAll");
  if (btnCancelAll) {
    btnCancelAll.addEventListener("click", async () => {
      const resp = await apiPost("/api/order/cancel_all", {});
      if (resp.ok) {
        showToast(`全撤完成: ${resp.count || 0}`);
        refreshHome();
      }
    });
  }
}

function bindConnections() {
  const btnConnect = document.getElementById("btnConnect");
  const btnDisconnect = document.getElementById("btnDisconnect");
  const envSelect = document.getElementById("envSelect");

  if (btnConnect) {
    btnConnect.addEventListener("click", async () => {
      const env = envSelect?.value || "";
      const resp = await apiPost("/api/connect", { env });
      showToast(resp.ok ? `连接指令已发送: ${env}` : `连接失败: ${resp.error || "未知错误"}`);
      refreshHome();
    });
  }

  if (btnDisconnect) {
    btnDisconnect.addEventListener("click", async () => {
      const env = envSelect?.value || "";
      const resp = await apiPost("/api/disconnect", { env });
      showToast(resp.ok ? `断开指令已发送: ${env}` : `断开失败: ${resp.error || "未知错误"}`);
      refreshHome();
    });
  }
}

function ensureAiPushMeta() {
  let el = document.getElementById("aiPushMeta");
  if (el) return el;
  const panel = document.getElementById("panel-ai");
  if (!panel) return null;
  el = document.createElement("div");
  el.id = "aiPushMeta";
  el.className = "push-meta";
  el.textContent = "实时推送未连接";
  const chat = document.getElementById("aiChatBox");
  if (chat && chat.parentElement === panel) {
    panel.insertBefore(el, chat);
  } else {
    panel.appendChild(el);
  }
  return el;
}

function ensureAiChatBox() {
  let box = document.getElementById("aiChatBox");
  if (box) return box;
  const panel = document.getElementById("panel-ai");
  if (!panel) return null;
  box = document.createElement("div");
  box.id = "aiChatBox";
  box.className = "chat-box";
  panel.appendChild(box);
  return box;
}

function addBubble(role, text) {
  const box = ensureAiChatBox();
  if (!box) return;
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function signalKey(row) {
  if (!row) return "";
  return row.key || `${row.symbol || ""}|${row.trading_day || ""}|${row.signal_time || ""}`;
}

function formatSignalText(row) {
  const symbol = row.symbol || "-";
  const name = row.name || "";
  const time = row.signal_time || "-";
  const price = row.signal_price || "-";
  const r1 = `${row.r1_time || "--"}@${row.r1_price || "--"}`;
  const r2 = `${row.r2_time || "--"}@${row.r2_price || "--"}`;
  const l1 = `${row.l1_time || "--"}@${row.l1_price || "--"}`;
  const l2 = `${row.l2_time || "--"}@${row.l2_price || "--"}`;
  return `【信号】${symbol} ${name} | 买入:${time} @ ${price} | 左1:${l1} 左2:${l2} | 右1:${r1} 右2:${r2}`;
}

function setPushMeta(data) {
  const el = ensureAiPushMeta();
  if (!el || !data) return;
  el.textContent =
    `推送中 自选:${data.watchlist_count || 0}` +
    ` API总:${data.api_total_count || 0}` +
    ` 已分配API:${data.assigned_api_count || 0}` +
    ` 信号:${data.signal_total_count || 0}` +
    ` 更新:${data.updated_at || "--"}`;
}

async function refreshRealtimePush() {
  let resp = null;
  try {
    resp = await apiGet("/api/realtime/signals?limit=300");
  } catch (_e) {
    const el = ensureAiPushMeta();
    if (el) el.textContent = "实时推送连接中断，正在重试...";
    return;
  }
  if (!resp.ok || !resp.data) return;

  const payload = resp.data;
  const rows = payload.signals || [];
  setPushMeta(payload);

  if (!state.realtime.initialized) {
    state.realtime.initialized = true;
    if (!rows.length) {
      addBubble("ai", "实时推送已连接，当前暂无策略信号。");
      return;
    }
    const warmRows = rows.slice(-Math.min(rows.length, 5));
    addBubble("ai", `实时推送已连接，当前累计信号 ${payload.signal_total_count || rows.length} 条，先展示最近 ${warmRows.length} 条。`);
    warmRows.forEach((row) => addBubble("ai", formatSignalText(row)));
    state.realtime.lastSignalKey = signalKey(warmRows[warmRows.length - 1]);
    return;
  }

  if (!rows.length) return;
  const lastKey = state.realtime.lastSignalKey;
  let newRows = [];
  if (!lastKey) {
    newRows = rows.slice(-1);
  } else {
    const idx = rows.findIndex((row) => signalKey(row) === lastKey);
    newRows = idx >= 0 ? rows.slice(idx + 1) : rows.slice(-Math.min(rows.length, 3));
  }

  if (!newRows.length) return;
  newRows.forEach((row) => addBubble("ai", formatSignalText(row)));
  state.realtime.lastSignalKey = signalKey(newRows[newRows.length - 1]);
}

function bindAI() {
  const modelEl = document.getElementById("aiModel");
  const inputEl = document.getElementById("aiInput");
  const sendEl = document.getElementById("aiSend");
  if (!modelEl || !inputEl || !sendEl) return;

  const send = async () => {
    const text = inputEl.value.trim();
    if (!text) return;
    const model = modelEl.value;
    addBubble("user", text);
    inputEl.value = "";
    const resp = await apiPost("/api/ai/chat", { message: text, model });
    if (resp.ok) {
      addBubble("ai", resp.content || "");
    } else {
      addBubble("ai", `AI 调用失败: ${resp.error || "未知错误"}`);
    }
  };

  sendEl.addEventListener("click", send);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
}

async function bootstrap() {
  bindClock();
  bindTabs();
  bindModuleCards();
  bindForm();
  bindConnections();
  bindAI();

  const meta = await apiGet("/api/meta");
  if (meta.ok) {
    state.mode = meta.mode;
    state.envs = meta.envs || [];
    state.favorites = meta.favorites || [];

    const envSelect = document.getElementById("envSelect");
    if (envSelect) {
      envSelect.innerHTML = "";
      state.envs.forEach((env) => {
        const op = document.createElement("option");
        op.value = env;
        op.textContent = env;
        envSelect.appendChild(op);
      });
    }

    const symbolSelect = document.getElementById("symbolSelect");
    if (symbolSelect) {
      symbolSelect.innerHTML = "";
      state.favorites.forEach((item) => {
        const op = document.createElement("option");
        op.value = item.symbol;
        op.textContent = `${item.name} ${item.symbol}`;
        symbolSelect.appendChild(op);
      });
    }

    const exchangeInput = document.getElementById("exchangeInput");
    if (exchangeInput && state.favorites.length) {
      exchangeInput.value = state.favorites[0].exchange || "";
    }
  }

  await refreshHome();
  await refreshRealtimePush();
  state.pollTimer = setInterval(refreshHome, 3000);
  state.pushTimer = setInterval(refreshRealtimePush, 2000);
}

window.addEventListener("error", (e) => {
  showToast(`前端异常: ${e.message}`);
});

bootstrap().catch((err) => {
  // Keep UI usable and show explicit feedback when bootstrap fails.
  // eslint-disable-next-line no-console
  console.error(err);
  showToast(`初始化失败: ${err.message || err}`);
});
