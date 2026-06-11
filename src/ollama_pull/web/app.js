"use strict";

const state = {
  snapshot: null,
  selectedId: null,
  searchResults: [],
  searchBusy: false,
  sources: [
    { id: "library", label: "Official", namespace: "library" },
    { id: "mlx-community", label: "MLX Community", namespace: "mlx-community" },
  ],
  cleanupReport: null,
};

const elements = {
  target: document.getElementById("target"),
  appError: document.getElementById("app-error"),
  searchForm: document.getElementById("search-form"),
  sourceSelect: document.getElementById("source-select"),
  modelInput: document.getElementById("model-input"),
  addDirect: document.getElementById("add-direct"),
  searchStatus: document.getElementById("search-status"),
  searchResults: document.getElementById("search-results"),
  installed: document.getElementById("installed"),
  cleanupScan: document.getElementById("cleanup-scan"),
  cleanupPartials: document.getElementById("cleanup-partials"),
  cleanupDelete: document.getElementById("cleanup-delete"),
  cleanupResults: document.getElementById("cleanup-results"),
  active: document.getElementById("active"),
  queue: document.getElementById("queue"),
  start: document.getElementById("start"),
  pause: document.getElementById("pause"),
  stopAfterBlob: document.getElementById("stop-after-blob"),
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

function messageText(message) {
  if (typeof message === "string") {
    return message;
  }
  return message?.text || "";
}

function renderMessage(message) {
  const text = messageText(message);
  if (message && typeof message === "object" && Number.isFinite(message.timestamp)) {
    return `<li><span class="message-time">${escapeHtml(formatTime(message.timestamp))}</span>${escapeHtml(text)}</li>`;
  }
  return `<li>${escapeHtml(text)}</li>`;
}

function formatBytes(value) {
  if (!Number.isFinite(value)) {
    return "Unknown";
  }
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Number(value);
  for (const unit of units) {
    if (Math.abs(size) < 1000 || unit === units[units.length - 1]) {
      return unit === "B" ? `${Math.round(size)}B` : `${size.toFixed(1)}${unit}`;
    }
    size /= 1000;
  }
  return `${size.toFixed(1)}TB`;
}

function formatRate(value) {
  return Number.isFinite(value) ? `${formatBytes(value)}/s` : "Unknown";
}

function formatEta(seconds) {
  if (!Number.isFinite(seconds)) {
    return "Unknown";
  }
  const safeSeconds = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remaining = safeSeconds % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    const mins = minutes % 60;
    return `${hours}h ${mins}m`;
  }
  return `${minutes}m ${String(remaining).padStart(2, "0")}s`;
}

function clampPercent(value) {
  if (!Number.isFinite(value)) {
    return null;
  }
  return Math.max(0, Math.min(100, value));
}

function percentText(value) {
  const percent = clampPercent(value);
  return percent === null ? "" : `${percent.toFixed(1)}%`;
}

function progressAmountText(progress) {
  if (!progress) {
    return "Waiting for progress";
  }
  const downloaded = formatBytes(progress.downloaded);
  if (Number.isFinite(progress.total)) {
    return `${downloaded} of ${formatBytes(progress.total)}`;
  }
  return `${downloaded} downloaded`;
}

function progressBar(progress, label) {
  const percent = clampPercent(progress?.percent);
  if (percent === null) {
    return "";
  }
  return `
    <div class="progress-track" role="progressbar" aria-label="${escapeHtml(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${escapeHtml(percent.toFixed(0))}">
      <span class="progress-fill" style="width: ${escapeHtml(percent.toFixed(2))}%"></span>
    </div>
  `;
}

function blobPositionText(currentFile) {
  if (Number.isFinite(currentFile?.index) && Number.isFinite(currentFile?.total_files)) {
    return `Current blob ${currentFile.index} of ${currentFile.total_files}`;
  }
  return "Current blob";
}

function renderProgressBlock(progress, options = {}) {
  const overall = progress?.overall || {};
  const currentFile = progress?.current_file || {};
  const overallPercent = percentText(overall.percent);
  const currentPercent = percentText(currentFile.percent);
  const speed = overall.bytes_per_second;
  const eta = overall.eta_seconds;
  const currentSpeed = currentFile.bytes_per_second;
  return `
    <div class="${escapeHtml(options.compact ? "progress-block compact" : "progress-block")}">
      ${progressBar(overall, "Overall download progress")}
      <div class="progress-summary">
        <span>${escapeHtml(overallPercent || progressAmountText(overall))}</span>
        <span>
          ${escapeHtml(progressAmountText(overall))}
          ${Number.isFinite(speed) ? ` &middot; ${escapeHtml(formatRate(speed))}` : ""}
          ${Number.isFinite(eta) ? ` &middot; ETA ${escapeHtml(formatEta(eta))}` : ""}
        </span>
      </div>
      ${
        options.showFile === false
          ? ""
          : `<div class="progress-file">
              <span class="field-label">${escapeHtml(blobPositionText(currentFile))}</span>
              <span class="blob">${escapeHtml(currentFile.digest || "Waiting for file")}</span>
              ${progressBar(currentFile, "Current file progress")}
              <span class="row-subtitle">
                ${escapeHtml(currentPercent || progressAmountText(currentFile))}
                ${Number.isFinite(currentSpeed) ? ` &middot; ${escapeHtml(formatRate(currentSpeed))}` : ""}
              </span>
            </div>`
      }
    </div>
  `;
}

function itemById(id) {
  return state.snapshot?.items?.find((item) => item.id === id) || null;
}

function queueableName(result) {
  return result?.name || result?.model || result?.heading || "";
}

function variantName(variant) {
  if (typeof variant === "string") {
    return variant;
  }
  return variant?.name || "";
}

function variantLabel(variant) {
  if (typeof variant === "string") {
    return variant.split(":", 2)[1] || variant;
  }
  return variant?.label || variantName(variant);
}

function selectedSource() {
  const sourceId = elements.sourceSelect?.value || "library";
  return state.sources.find((source) => source.id === sourceId) || state.sources[0];
}

function renderSources() {
  const sources = Array.isArray(state.snapshot?.sources) && state.snapshot.sources.length
    ? state.snapshot.sources
    : state.sources;
  const previous = elements.sourceSelect.value || "library";
  state.sources = sources;
  elements.sourceSelect.innerHTML = sources
    .map((source) => `<option value="${escapeHtml(source.id)}">${escapeHtml(source.label)}</option>`)
    .join("");
  elements.sourceSelect.value = sources.some((source) => source.id === previous) ? previous : sources[0]?.id || "library";
}

function renderTarget() {
  const snapshot = state.snapshot;
  const modelsDir = snapshot?.models_dir || "Waiting for server state";
  const registry = snapshot?.registry || "registry unavailable";
  elements.target.innerHTML = `
    <span class="target-label">Models directory</span>
    <div>${escapeHtml(modelsDir)}</div>
    <span class="target-label">Source registry</span>
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

  const label = selectedSource()?.label || "Official";
  elements.searchStatus.textContent = `${state.searchResults.length} ${label} result${state.searchResults.length === 1 ? "" : "s"}. Choose a version to queue.`;
  elements.searchResults.innerHTML = state.searchResults
    .map((result, index) => {
      const name = queueableName(result);
      const heading = result.heading && result.heading !== name ? result.heading : "";
      const description = result.description || "No description provided.";
      const tags = Array.isArray(result.tags) ? result.tags : [];
      const variants = Array.isArray(result.variants) ? result.variants : [];
      const tagHtml = tags
        .slice(0, 8)
        .map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`)
        .join("");
      const variantHtml = variants.length
        ? variants
            .map((variant) => {
              const fullName = variantName(variant);
              return `
                <button class="variant-button" type="button" data-model="${escapeHtml(fullName)}" title="${escapeHtml(fullName)}">
                  ${escapeHtml(variantLabel(variant))}
                </button>
              `;
            })
            .join("")
        : `<span class="row-subtitle">Variants unavailable.</span>`;
      return `
        <div class="result-row" data-result-index="${index}">
          <span class="row-title">${escapeHtml(name || "Unnamed model")}</span>
          ${heading ? `<span class="row-subtitle">${escapeHtml(heading)}</span>` : ""}
          <span class="row-subtitle">${escapeHtml(description)}</span>
          ${tagHtml ? `<span class="tag-list">${tagHtml}</span>` : ""}
          <span class="variant-list">${variantHtml}</span>
        </div>
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
  const lastMessage = messages.length ? messageText(messages[messages.length - 1]) : "Waiting for progress";
  const pendingStop = state.snapshot?.stop_after_blob_requested
    ? `<div class="row-subtitle">Will stop after the current blob finishes.</div>`
    : "";
  elements.active.innerHTML = `
    <div class="active-grid">
      <div class="row-meta">
        ${statusBadge(running.status)}
        <span class="row-title">${escapeHtml(running.model)}</span>
      </div>
      ${renderProgressBlock(running.progress)}
      <div class="row-subtitle">${escapeHtml(lastMessage)}</div>
      ${pendingStop}
    </div>
  `;
}

function renderInstalled() {
  const installed = state.snapshot?.installed_models || [];
  if (!installed.length) {
    elements.installed.innerHTML = `<div class="empty muted">No installed model manifests found.</div>`;
    return;
  }

  elements.installed.innerHTML = installed
    .map((model) => {
      const namespace = model.namespace || "library";
      const subtitle = namespace === "library" ? "official library" : namespace;
      return `
        <div class="installed-row">
          <span class="row-title">${escapeHtml(model.name || "Unnamed model")}</span>
          <span class="row-subtitle">${escapeHtml(subtitle)}</span>
        </div>
      `;
    })
    .join("");
}

function cleanupOptions() {
  return {
    include_partials: Boolean(elements.cleanupPartials.checked),
    older_than_days: 7,
  };
}

function cleanupCandidateCount(report) {
  if (!report) {
    return 0;
  }
  return Number(report.orphan_blob_count || 0) + Number(report.stale_partial_count || 0);
}

function renderCleanupReport(report) {
  state.cleanupReport = report;
  if (!report) {
    elements.cleanupResults.textContent = "Scan to find complete blobs that are not referenced by installed manifests.";
    elements.cleanupDelete.disabled = true;
    return;
  }

  const candidateCount = cleanupCandidateCount(report);
  const mode = report.dry_run ? "Dry run" : "Deleted";
  elements.cleanupResults.innerHTML = `
    <div>${escapeHtml(mode)}: ${escapeHtml(report.referenced_count || 0)} referenced blobs.</div>
    <div>Complete orphan blobs: ${escapeHtml(report.orphan_blob_count || 0)} (${escapeHtml(formatBytes(report.orphan_blob_bytes || 0))}).</div>
    <div>Stale partial downloads: ${escapeHtml(report.stale_partial_count || 0)} (${escapeHtml(formatBytes(report.stale_partial_bytes || 0))}).</div>
    <div>Shared blobs are kept. Partial downloads are included only when stale partials are enabled.</div>
    ${report.deleted?.length ? `<div>Deleted files: ${escapeHtml(report.deleted.length)}.</div>` : ""}
  `;
  elements.cleanupDelete.disabled = candidateCount === 0 || !report.dry_run;
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
      const progressHtml =
        item.status === "running" ? renderProgressBlock(item.progress, { compact: true, showFile: false }) : "";
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
            ${progressHtml}
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
    ? `<ul class="message-list">${messages.map(renderMessage).join("")}</ul>`
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
        <span class="field-label">Source registry</span>
        <span class="detail-value">${escapeHtml(state.snapshot?.registry || "Unknown")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Retries</span>
        <span class="detail-value">${escapeHtml(state.snapshot?.retries ?? "Unknown")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Progress</span>
        ${renderProgressBlock(item.progress)}
      </div>
      <div class="detail-field">
        <span class="field-label">Error</span>
        <span class="detail-value">${escapeHtml(item.error || "None")}</span>
      </div>
      <div class="detail-field">
        <span class="field-label">Activity</span>
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
  elements.stopAfterBlob.disabled = !snapshot?.running || Boolean(snapshot?.stop_after_blob_requested);
}

function renderState() {
  renderSources();
  renderTarget();
  renderInstalled();
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
    const sourceId = elements.sourceSelect.value || "library";
    const payload = await api(`/api/search?q=${encodeURIComponent(trimmed)}&source=${encodeURIComponent(sourceId)}`);
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

elements.sourceSelect.addEventListener("change", () => {
  state.searchResults = [];
  elements.searchStatus.textContent = "";
  renderSearchResults();
});

elements.searchResults.addEventListener("click", (event) => {
  const variant = event.target.closest("[data-model]");
  if (!variant) {
    return;
  }
  queueModel(variant.dataset.model || "");
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

elements.stopAfterBlob.addEventListener("click", async () => {
  try {
    await api("/api/stop-after-blob", { method: "POST" });
    showError("");
    await refreshState();
  } catch (error) {
    showError(`Stop after blob failed: ${error.message}`);
  }
});

elements.cleanupScan.addEventListener("click", async () => {
  try {
    const report = await api("/api/cleanup/scan", { method: "POST", body: cleanupOptions() });
    renderCleanupReport(report);
    showError("");
  } catch (error) {
    showError(`Cleanup scan failed: ${error.message}`);
  }
});

elements.cleanupDelete.addEventListener("click", async () => {
  const report = state.cleanupReport;
  if (!report || cleanupCandidateCount(report) === 0) {
    return;
  }
  const includePartials = Boolean(elements.cleanupPartials.checked);
  const message = includePartials
    ? "Delete complete orphan blobs and stale partial downloads? Shared blobs are kept."
    : "Delete complete orphan blobs? Shared blobs are kept and partial downloads are left in place.";
  if (!window.confirm(message)) {
    return;
  }
  try {
    const deleted = await api("/api/cleanup/delete", { method: "POST", body: cleanupOptions() });
    renderCleanupReport(deleted);
    showError("");
    await refreshState();
  } catch (error) {
    showError(`Cleanup delete failed: ${error.message}`);
  }
});

elements.cleanupPartials.addEventListener("change", () => {
  renderCleanupReport(null);
});

renderCleanupReport(null);
refreshState();
setInterval(refreshState, 1000);
