"use strict";

const state = {
  snapshot: null,
  selectedId: null,
  searchResults: [],
  searchBusy: false,
};

const elements = {
  target: document.getElementById("target"),
  appError: document.getElementById("app-error"),
  searchForm: document.getElementById("search-form"),
  modelInput: document.getElementById("model-input"),
  addDirect: document.getElementById("add-direct"),
  searchStatus: document.getElementById("search-status"),
  searchResults: document.getElementById("search-results"),
  active: document.getElementById("active"),
  queue: document.getElementById("queue"),
  start: document.getElementById("start"),
  pause: document.getElementById("pause"),
  details: document.getElementById("details"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    const replacements = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return replacements[character];
  });
}

async function api(path, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  const requestOptions = { ...options, headers };

  if (requestOptions.body && typeof requestOptions.body !== "string") {
    headers["Content-Type"] = "application/json";
    requestOptions.body = JSON.stringify(requestOptions.body);
  }

  const response = await fetch(path, requestOptions);
  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      throw new Error("Server returned invalid JSON");
    }
  }

  if (!response.ok) {
    throw new Error(payload?.error || `Request failed with HTTP ${response.status}`);
  }
  if (payload && payload.error) {
    throw new Error(payload.error);
  }
  return payload;
}

function showError(message) {
  if (!message) {
    elements.appError.hidden = true;
    elements.appError.textContent = "";
    return;
  }
  elements.appError.hidden = false;
  elements.appError.textContent = message;
}

function statusBadge(status) {
  const value = status || "unknown";
  const className = String(value).toLowerCase().replace(/[^a-z0-9_-]/g, "-");
  return `<span class="badge ${escapeHtml(className)}">${escapeHtml(value)}</span>`;
}

function formatTime(seconds) {
  if (!seconds) {
    return "Not recorded";
  }
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) {
    return "Not recorded";
  }
  return date.toLocaleString();
}

function itemById(id) {
  return state.snapshot?.items?.find((item) => item.id === id) || null;
}

function queueableName(result) {
  return result?.name || result?.model || result?.heading || "";
}

function renderTarget() {
  const snapshot = state.snapshot;
  const modelsDir = snapshot?.models_dir || "Waiting for server state";
  const registry = snapshot?.registry || "registry unavailable";
  elements.target.innerHTML = `
    <span class="target-label">Models path</span>
    <div>${escapeHtml(modelsDir)}</div>
    <span class="target-label">Registry</span>
    <div>${escapeHtml(registry)}</div>
  `;
}

function renderSearchResults() {
  if (state.searchBusy) {
    elements.searchStatus.textContent = "Searching...";
    return;
  }

  if (state.searchResults.length === 0) {
    elements.searchResults.innerHTML = "";
    return;
  }

  elements.searchStatus.textContent = `${state.searchResults.length} result${state.searchResults.length === 1 ? "" : "s"}. Click a row to queue it.`;
  elements.searchResults.innerHTML = state.searchResults
    .map((result, index) => {
      const name = queueableName(result);
      const heading = result.heading && result.heading !== name ? result.heading : "";
      const description = result.description || "No description provided.";
      const tags = Array.isArray(result.tags) ? result.tags : [];
      const tagHtml = tags
        .slice(0, 8)
        .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
        .join("");
      return `
        <button class="result-row" type="button" data-result-index="${index}">
          <span class="row-title">${escapeHtml(name || "Unnamed model")}</span>
          ${heading ? `<span class="row-subtitle">${escapeHtml(heading)}</span>` : ""}
          <span class="row-subtitle">${escapeHtml(description)}</span>
          ${tagHtml ? `<span class="tag-list">${tagHtml}</span>` : ""}
        </button>
      `;
    })
    .join("");
}

function renderActive() {
  const running = state.snapshot?.items?.find((item) => item.status === "running");
  if (!running) {
    elements.active.innerHTML = `
      <div class="active-grid">
        <span class="muted">No active download.</span>
        <span class="row-subtitle">Start the queue when models are waiting.</span>
      </div>
    `;
    return;
  }

  const messages = Array.isArray(running.messages) ? running.messages : [];
  const lastMessage = messages.length ? messages[messages.length - 1] : "Waiting for progress";
  elements.active.innerHTML = `
    <div class="active-grid">
      <div class="row-meta">
        ${statusBadge(running.status)}
        <span class="row-title">${escapeHtml(running.model)}</span>
      </div>
      <div class="blob">${escapeHtml(running.current_blob || "No blob reported yet")}</div>
      <div class="row-subtitle">${escapeHtml(lastMessage)}</div>
    </div>
  `;
}

function renderQueue() {
  const items = state.snapshot?.items || [];
  if (!items.length) {
    elements.queue.innerHTML = `<div class="empty muted">Queue is empty.</div>`;
    state.selectedId = null;
    renderDetails();
    return;
  }

  if (!itemById(state.selectedId)) {
    const active = items.find((item) => item.status === "running");
    state.selectedId = (active || items[0]).id;
  }

  elements.queue.innerHTML = items
    .map((item) => {
      const selected = item.id === state.selectedId ? " selected" : "";
      const removable = item.status !== "running";
      const retryable = item.status === "failed";
      const actionHtml = `
        <span class="row-actions">
          ${retryable ? `<button class="small" type="button" data-action="retry" data-id="${escapeHtml(item.id)}">Retry</button>` : ""}
          ${removable ? `<button class="small danger" type="button" data-action="remove" data-id="${escapeHtml(item.id)}">Remove</button>` : ""}
        </span>
      `;
      return `
        <div class="queue-row${selected}" data-action="select" data-id="${escapeHtml(item.id)}">
          <div>
            <div class="row-meta">
              ${statusBadge(item.status)}
              <span class="row-title">${escapeHtml(item.model)}</span>
            </div>
            <div class="row-subtitle">Updated ${escapeHtml(formatTime(item.updated_at))}</div>
            ${item.error ? `<div class="row-subtitle">${escapeHtml(item.error)}</div>` : ""}
          </div>
          ${actionHtml}
        </div>
      `;
    })
    .join("");
  renderDetails();
}

function renderDetails() {
  const item = itemById(state.selectedId);
  if (!item) {
    elements.details.innerHTML = `<div class="empty muted">Select a queue item to inspect download details.</div>`;
    return;
  }

  const messages = Array.isArray(item.messages) ? item.messages.slice(-8) : [];
  const messageHtml = messages.length
    ? `<ul class="message-list">${messages.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>`
    : `<span class="muted">No messages yet.</span>`;
  const retryButton =
    item.status === "failed"
      ? `<button type="button" data-action="retry" data-id="${escapeHtml(item.id)}">Retry</button>`
      : "";
  const removeButton =
    item.status !== "running"
      ? `<button class="danger" type="button" data-action="remove" data-id="${escapeHtml(item.id)}">Remove</button>`
      : "";

  elements.details.innerHTML = `
    <div class="details-body">
      <div class="detail-field">
        <span class="field-label">Model</span>
        <span class="detail-value">${escapeHtml(item.model)}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Status</span>
        <span class="detail-value">${statusBadge(item.status)}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Registry</span>
        <span class="detail-value">${escapeHtml(state.snapshot?.registry || "Unknown")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Retries</span>
        <span class="detail-value">${escapeHtml(state.snapshot?.retries ?? "Unknown")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Current blob</span>
        <span class="detail-value blob">${escapeHtml(item.current_blob || "None")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Error</span>
        <span class="detail-value">${escapeHtml(item.error || "None")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Recent messages</span>
        ${messageHtml}
      </div>
      ${
        retryButton || removeButton
          ? `<div class="detail-actions">${retryButton}${removeButton}</div>`
          : ""
      }
    </div>
  `;
}

async function handleItemAction(action, id) {
  try {
    if (action === "retry") {
      const item = await api(`/api/retry/${encodeURIComponent(id)}`, { method: "POST" });
      state.selectedId = item.id;
    } else if (action === "remove") {
      await api(`/api/remove/${encodeURIComponent(id)}`, { method: "POST" });
      if (state.selectedId === id) {
        state.selectedId = null;
      }
    }
    showError("");
    await refreshState();
  } catch (error) {
    showError(`${action === "retry" ? "Retry" : "Remove"} failed: ${error.message}`);
  }
}

function renderControls() {
  const snapshot = state.snapshot;
  const hasWaiting = Boolean(snapshot?.items?.some((item) => item.status === "waiting"));
  elements.start.disabled = !hasWaiting;
  elements.pause.disabled = !snapshot?.running;
}

function renderState() {
  renderTarget();
  renderActive();
  renderQueue();
  renderControls();
}

async function refreshState() {
  try {
    state.snapshot = await api("/api/state");
    renderState();
  } catch (error) {
    showError(`State refresh failed: ${error.message}`);
  }
}

async function queueModel(model) {
  const trimmed = model.trim();
  if (!trimmed) {
    elements.searchStatus.textContent = "Enter a model name or reference first.";
    return;
  }

  try {
    const item = await api("/api/queue", {
      method: "POST",
      body: { model: trimmed },
    });
    state.selectedId = item.id;
    elements.searchStatus.textContent = `Queued ${trimmed}.`;
    elements.modelInput.value = "";
    showError("");
    await refreshState();
  } catch (error) {
    showError(`Queue failed: ${error.message}`);
  }
}

async function searchModels(query) {
  const trimmed = query.trim();
  if (!trimmed) {
    elements.searchStatus.textContent = "Enter a search term or direct model reference.";
    state.searchResults = [];
    renderSearchResults();
    return;
  }

  state.searchBusy = true;
  elements.searchResults.innerHTML = "";
  renderSearchResults();
  try {
    const payload = await api(`/api/search?q=${encodeURIComponent(trimmed)}`);
    state.searchResults = Array.isArray(payload?.results) ? payload.results : [];
    if (payload?.available === false && payload?.error) {
      elements.searchStatus.textContent = payload.error;
    } else if (state.searchResults.length === 0) {
      elements.searchStatus.textContent = "No matching models found.";
    }
    showError("");
  } catch (error) {
    state.searchResults = [];
    elements.searchStatus.textContent = "";
    showError(`Search failed: ${error.message}`);
  } finally {
    state.searchBusy = false;
    renderSearchResults();
  }
}

elements.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  searchModels(elements.modelInput.value);
});

elements.addDirect.addEventListener("click", () => {
  queueModel(elements.modelInput.value);
});

elements.searchResults.addEventListener("click", (event) => {
  const row = event.target.closest("[data-result-index]");
  if (!row) {
    return;
  }
  const result = state.searchResults[Number(row.dataset.resultIndex)];
  const name = queueableName(result);
  queueModel(name);
});

elements.queue.addEventListener("click", async (event) => {
  const actionTarget = event.target.closest("[data-action]");
  if (!actionTarget) {
    return;
  }

  const action = actionTarget.dataset.action;
  const id = actionTarget.dataset.id;
  if (!id) {
    return;
  }

  if (action === "select") {
    state.selectedId = id;
    renderQueue();
    return;
  }

  event.stopPropagation();
  await handleItemAction(action, id);
});

elements.details.addEventListener("click", async (event) => {
  const actionTarget = event.target.closest("[data-action]");
  if (!actionTarget) {
    return;
  }

  const action = actionTarget.dataset.action;
  const id = actionTarget.dataset.id;
  if (!id || (action !== "retry" && action !== "remove")) {
    return;
  }

  await handleItemAction(action, id);
});

elements.start.addEventListener("click", async () => {
  try {
    await api("/api/start", { method: "POST" });
    showError("");
    await refreshState();
  } catch (error) {
    showError(`Start failed: ${error.message}`);
  }
});

elements.pause.addEventListener("click", async () => {
  try {
    await api("/api/pause", { method: "POST" });
    showError("");
    await refreshState();
  } catch (error) {
    showError(`Pause failed: ${error.message}`);
  }
});

refreshState();
setInterval(refreshState, 1000);
