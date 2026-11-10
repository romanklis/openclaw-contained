/* Email Intelligence dashboard — dependency-free. */
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const state = { tab: "overview", searchTimer: null, scheduler: null };

  const INTENT_LABELS = {
    new_pitch: "New pitch",
    investor_thesis: "Investor thesis",
    follow_up: "Follow-up",
    meeting_request: "Meeting request",
    other: "Other",
  };

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));

  const badge = (value, cssClass = "") =>
    `<span class="badge ${escapeHtml(cssClass || value || "unknown")}">${escapeHtml(value || "unknown")}</span>`;

  const formatDate = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  };

  const truncate = (value, length = 60) => {
    const text = String(value || "");
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  };

  const intentLabel = (value) => INTENT_LABELS[value] || value || "—";

  const safeUrl = (value) => {
    try {
      const url = new URL(String(value));
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch {
      return "";
    }
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const text = await response.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { detail: text };
    }
    if (!response.ok) {
      throw new Error(data.detail || `${response.status} ${response.statusText}`);
    }
    return data;
  }

  function showError(message) {
    const banner = $("#errorBanner");
    banner.textContent = message;
    banner.classList.remove("hidden");
  }

  function hideError() {
    $("#errorBanner").classList.add("hidden");
  }

  function setSettingsStatus(message) {
    $("#settingsStatus").textContent = message;
  }

  function setOverviewRunStatus(message) {
    const element = $("#overviewRunStatus");
    if (element) element.textContent = message;
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  function renderSummary(data) {
    const status = data.by_status || {};
    const lastRun = data.last_run;
    const cards = [
      {
        label: "Total emails",
        value: data.total ?? 0,
        hint: `${status.stored ?? 0} stored · ${status.failed ?? 0} failed`,
      },
      {
        label: "Stored",
        value: status.stored ?? 0,
        hint: "Extractions persisted to Postgres",
      },
      {
        label: "Failed",
        value: status.failed ?? 0,
        hint: "Needs attention",
      },
      {
        label: "Target DAG",
        value: data.target_dag_id ? "Set" : "Not set",
        hint: data.target_dag_id || "Paste a DAG id in Settings",
      },
      {
        label: "Last run",
        value: lastRun ? lastRun.status : "None",
        hint: lastRun ? formatDate(lastRun.started_at) : "Waiting for a target DAG",
      },
    ];
    $("#summaryCards").innerHTML = cards
      .map(
        (card) => `
        <div class="card">
          <div class="label">${escapeHtml(card.label)}</div>
          <div class="value">${escapeHtml(card.value)}</div>
          <div class="hint" title="${escapeHtml(card.hint)}">${escapeHtml(card.hint)}</div>
        </div>`
      )
      .join("");
  }

  function bindEmailRows(container) {
    container.querySelectorAll("[data-email-id]").forEach((element) => {
      element.addEventListener("click", () => openEmail(element.dataset.emailId));
    });
  }

  function renderEmails(container, emails) {
    if (!emails || emails.length === 0) {
      container.innerHTML = '<div class="empty">No emails yet. Build and run the workflow, then refresh.</div>';
      return;
    }
    container.innerHTML = `
      <table class="clickable">
        <thead>
          <tr>
            <th>Subject</th><th>From</th><th>Intent</th><th>Status</th>
            <th>Company</th><th>Stage</th><th>Received</th>
          </tr>
        </thead>
        <tbody>
          ${emails
            .map(
              (email) => `
            <tr data-email-id="${escapeHtml(email.id)}">
              <td class="subject">${escapeHtml(truncate(email.subject, 70))}</td>
              <td>${escapeHtml(truncate(email.from_addr, 38))}</td>
              <td>${escapeHtml(intentLabel(email.intent))}</td>
              <td>${badge(email.status)}</td>
              <td>${escapeHtml(email.company_name || "—")}</td>
              <td>${escapeHtml(email.stage || "—")}</td>
              <td>${escapeHtml(formatDate(email.received_at))}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    bindEmailRows(container);
  }

  function renderRuns(container, runs) {
    if (!runs || runs.length === 0) {
      container.innerHTML = '<div class="empty">No scheduler runs yet. Set a target DAG, choose Manual or Interval, then press Run now or wait for the interval.</div>';
      return;
    }
    container.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Started</th><th>Trigger</th><th>Status</th><th>Target DAG</th>
            <th>Finished</th><th>Error</th>
          </tr>
        </thead>
        <tbody>
          ${runs
            .map(
              (run) => `
            <tr>
              <td>${escapeHtml(formatDate(run.started_at))}</td>
              <td>${badge(run.trigger)}</td>
              <td>${badge(run.status)}</td>
              <td class="mono">${escapeHtml(truncate(run.source_dag_id || run.instance_dag_id, 32))}</td>
              <td>${escapeHtml(formatDate(run.finished_at))}</td>
              <td>${escapeHtml(truncate(run.error, 60))}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderKanban(board) {
    const root = $("#kanbanBoard");
    root.innerHTML = board.columns
      .map(
        (column) => `
        <div class="kanban-column">
          <h3>${escapeHtml(column.label)} <span class="kanban-count">${column.cards.length}</span></h3>
          ${
            column.cards.length
              ? column.cards
                  .map(
                    (card) => `
                <div class="kanban-card" data-email-id="${escapeHtml(card.id)}">
                  <div class="k-subject">${escapeHtml(truncate(card.subject, 56))}</div>
                  ${card.company_name ? `<div class="k-company">${escapeHtml(card.company_name)}</div>` : ""}
                  <div class="k-meta">${escapeHtml(truncate(card.from_addr, 40))}</div>
                  <div class="k-meta">
                    ${escapeHtml(intentLabel(card.intent))}
                    ${card.stage ? ` · ${escapeHtml(card.stage)}` : ""}
                    ${card.capital_raise ? ` · ${escapeHtml(card.capital_raise)}` : ""}
                  </div>
                </div>`
                  )
                  .join("")
              : '<div class="kanban-empty">No emails</div>'
          }
        </div>`
      )
      .join("");
    bindEmailRows(root);
  }

  function renderHealth(health) {
    const target = health.target_dag_id || "not set";
    const active = health.active_instance_id || "none";
    const interval = health.interval_seconds ?? 60;
    const modeText = health.mode === "interval" ? `Interval ${interval}s` : "Manual only";
    const nextText =
      health.mode === "interval"
        ? health.target_dag_id
          ? health.next_run_at
            ? formatDate(health.next_run_at)
            : "scheduled"
          : "set a target"
        : "— (manual)";
    const items = [
      ["Control plane", health.control_plane ? "reachable" : "unreachable"],
      ["Database", health.db ? "reachable" : "unreachable"],
      ["Mode", modeText],
      ["Next run", nextText],
      ["Target DAG", target],
      ["Active run", active],
    ];
    $("#healthDetails").innerHTML = items
      .map(
        ([key, value]) => `
        <div class="health-item">
          <div class="k">${escapeHtml(key)}</div>
          <div class="v mono">${escapeHtml(value)}</div>
        </div>`
      )
      .join("");

    const pill = $("#healthPill");
    pill.textContent = health.ok ? "healthy" : "degraded";
    pill.className = `pill ${health.ok ? "pill-ok" : "pill-bad"}`;
  }

  function renderScheduler(scheduler) {
    state.scheduler = scheduler || {};
    const mode = scheduler.mode === "interval" ? "interval" : "manual";
    const interval = scheduler.interval_seconds ?? 60;
    const target = scheduler.target_dag_id || "";
    const next = scheduler.next_run_at ? formatDate(scheduler.next_run_at) : "";
    const last = scheduler.last_run;
    const modeText = mode === "interval" ? `Automatic every ${interval}s` : "Manual only";
    const nextText =
      mode === "manual"
        ? "— (manual only)"
        : target
          ? next || "scheduled after save"
          : "set a target DAG";
    const lastText = last ? `${last.status} · ${formatDate(last.started_at)}` : "—";

    const topPill = $("#schedulerPill");
    topPill.textContent = mode === "interval" ? `auto ${interval}s` : "manual";
    topPill.className = `pill ${mode === "interval" ? "pill-ok" : "pill-muted"}`;

    const overviewPill = $("#overviewSchedulerPill");
    overviewPill.textContent = mode === "interval" ? `interval ${interval}s` : "manual";
    overviewPill.className = `badge badge-mode-${mode}`;

    $("#overviewSchedulerMode").textContent = modeText;
    $("#overviewSchedulerTarget").textContent = target || "not set";
    $("#overviewSchedulerNext").textContent = nextText;
    $("#overviewSchedulerLast").textContent = lastText;
    $("#schedulerHint").textContent =
      mode === "manual"
        ? "No automatic runs. Press Run now when you want to process the mailbox."
        : target
          ? "Run now starts immediately and reschedules the next automatic run."
          : "Interval mode is enabled but no target DAG is set.";

    const details = [
      ["Mode", modeText],
      ["Target DAG", target || "not set"],
      ["Next run", nextText],
      ["Active run", scheduler.active_instance_id || "none"],
      ["Last run", lastText],
    ];
    const detailsElement = $("#schedulerDetails");
    if (detailsElement) {
      detailsElement.innerHTML = details
        .map(
          ([key, value]) => `
          <div class="health-item">
            <div class="k">${escapeHtml(key)}</div>
            <div class="v mono">${escapeHtml(value)}</div>
          </div>`
        )
        .join("");
    }
  }

  async function loadScheduler() {
    const scheduler = await api("/api/scheduler");
    renderScheduler(scheduler);
    return scheduler;
  }

  async function refreshHealth() {
    const pill = $("#healthPill");
    try {
      const health = await api("/healthz");
      renderHealth(health);
      return health;
    } catch {
      pill.textContent = "unreachable";
      pill.className = "pill pill-bad";
      return null;
    }
  }

  // ------------------------------------------------------------------
  // Detail drawer
  // ------------------------------------------------------------------

  function jsonBlock(value) {
    if (value === null || value === undefined) return "";
    return `<pre class="json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  }

  function renderEmailDetail(data) {
    const email = data.email || {};
    const attachments = data.attachments || [];
    const extraction = data.extraction;

    const attachmentHtml = attachments.length
      ? `<table>
          <thead><tr><th>File</th><th>Type</th><th>Size</th><th>Parse</th></tr></thead>
          <tbody>
            ${attachments
              .map(
                (attachment) => `
              <tr>
                <td>${escapeHtml(attachment.filename)}</td>
                <td>${escapeHtml(attachment.content_type || "—")}</td>
                <td>${attachment.size_bytes != null ? `${Math.round(attachment.size_bytes / 1024)} KB` : "—"}</td>
                <td>${badge(attachment.parse_status)}${attachment.error ? `<div class="muted small">${escapeHtml(attachment.error)}</div>` : ""}</td>
              </tr>
              ${
                attachment.extracted_text_preview
                  ? `<tr><td colspan="4">
                      <details><summary>Extracted text preview</summary>
                        <pre class="body-text">${escapeHtml(attachment.extracted_text_preview)}</pre>
                      </details>
                    </td></tr>`
                  : ""
              }`
              )
              .join("")}
          </tbody>
        </table>`
      : '<div class="empty">No attachments</div>';

    const extractionHtml = extraction
      ? (() => {
          const website = safeUrl(extraction.website);
          return `
        <div class="detail-section">
          <h3>Structured extraction</h3>
          <div class="kv">
            <div class="k">Company</div><div class="v">${escapeHtml(extraction.company_name || "—")}</div>
            <div class="k">Website</div><div class="v">${website ? `<a href="${escapeHtml(website)}" target="_blank" rel="noreferrer">${escapeHtml(extraction.website)}</a>` : escapeHtml(extraction.website || "—")}</div>
            <div class="k">Sector</div><div class="v">${escapeHtml(extraction.sector || "—")}</div>
            <div class="k">Stage</div><div class="v">${escapeHtml(extraction.stage || "—")}</div>
            <div class="k">Capital raise</div><div class="v">${escapeHtml(extraction.capital_raise || "—")}</div>
            <div class="k">Valuation</div><div class="v">${escapeHtml(extraction.valuation || "—")}</div>
            <div class="k">Thesis</div><div class="v">${escapeHtml(extraction.thesis || "—")}</div>
            <div class="k">Confidence</div><div class="v">${escapeHtml(extraction.confidence ?? "—")}</div>
            <div class="k">Model</div><div class="v">${escapeHtml(extraction.model || "—")}</div>
          </div>
          <h3 style="margin-top:14px">Traction metrics</h3>
          ${jsonBlock(extraction.traction_metrics || {})}
          <h3 style="margin-top:14px">Links</h3>
          ${jsonBlock(extraction.links || [])}
          <details><summary>Raw extraction JSON</summary>${jsonBlock(extraction.raw_json)}</details>
        </div>`;
        })()
      : '<div class="detail-section"><h3>Structured extraction</h3><div class="empty">Not extracted yet.</div></div>';

    $("#drawerContent").innerHTML = `
      <div class="detail-title">${escapeHtml(email.subject || "(no subject)")}</div>
      <div class="detail-meta">
        <span>${escapeHtml(email.from_addr || "—")}</span>
        <span>to ${escapeHtml(email.to_addr || "—")}</span>
        <span>${escapeHtml(formatDate(email.received_at))}</span>
      </div>
      <div class="detail-meta">
        ${badge(email.status)}
        ${email.intent ? badge(intentLabel(email.intent), "intent") : ""}
        <span class="mono">${escapeHtml(email.message_id || "")}</span>
      </div>
      ${email.error ? `<div class="error">${escapeHtml(email.error)}</div>` : ""}
      <div class="detail-section">
        <h3>Body</h3>
        <pre class="body-text">${escapeHtml(email.body_text || "(no body stored)")}</pre>
      </div>
      <div class="detail-section">
        <h3>Attachments</h3>
        ${attachmentHtml}
      </div>
      ${extractionHtml}
    `;
  }

  async function openEmail(id) {
    const drawer = $("#drawer");
    drawer.classList.remove("hidden");
    $("#drawerContent").innerHTML = '<div class="empty">Loading…</div>';
    try {
      const data = await api(`/api/emails/${encodeURIComponent(id)}`);
      renderEmailDetail(data);
    } catch (error) {
      $("#drawerContent").innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
    }
  }

  function closeDrawer() {
    $("#drawer").classList.add("hidden");
  }

  // ------------------------------------------------------------------
  // Loaders
  // ------------------------------------------------------------------

  async function loadOverview() {
    const [summary, emails, runs] = await Promise.all([
      api("/api/summary"),
      api("/api/emails?limit=8"),
      api("/api/runs?limit=5"),
    ]);
    renderSummary(summary);
    renderEmails($("#recentEmails"), emails.emails);
    renderRuns($("#recentRuns"), runs.runs);
  }

  async function loadEmails() {
    const params = new URLSearchParams({ limit: "200" });
    const status = $("#statusFilter").value;
    const intent = $("#intentFilter").value;
    const search = $("#searchFilter").value.trim();
    if (status) params.set("status", status);
    if (intent) params.set("intent", intent);
    if (search) params.set("search", search);
    const data = await api(`/api/emails?${params.toString()}`);
    renderEmails($("#emailsTable"), data.emails);
  }

  async function loadKanban() {
    const board = await api("/api/board");
    renderKanban(board);
  }

  async function loadRuns() {
    const data = await api("/api/runs?limit=100");
    renderRuns($("#runsTable"), data.runs);
  }

  async function loadSettings() {
    const [scheduler, health] = await Promise.all([api("/api/scheduler"), api("/healthz")]);
    renderScheduler(scheduler);
    $("#targetInput").value = scheduler.target_dag_id || "";
    $("#intervalInput").value = scheduler.interval_seconds ?? 60;
    const mode = scheduler.mode === "interval" ? "interval" : "manual";
    document.querySelectorAll('input[name="schedulerMode"]').forEach((radio) => {
      radio.checked = radio.value === mode;
    });
    $("#intervalInput").disabled = mode !== "interval";
    renderHealth(health);
  }

  async function refresh() {
    if (document.visibilityState === "hidden") return;
    try {
      hideError();
      await refreshHealth();
      await loadScheduler();
      if (state.tab === "overview") await loadOverview();
      else if (state.tab === "emails") await loadEmails();
      else if (state.tab === "kanban") await loadKanban();
      else if (state.tab === "runs") await loadRuns();
      else if (state.tab === "settings") await loadSettings();
      $("#refreshedAt").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      showError(error.message);
    }
  }

  // ------------------------------------------------------------------
  // Events
  // ------------------------------------------------------------------

  function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tabs button").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === tab);
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.id !== `tab-${tab}`);
    });
    refresh();
  }

  document.querySelectorAll(".tabs button").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });

  document.querySelectorAll("[data-goto]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.goto));
  });

  $("#statusFilter").addEventListener("change", () => {
    loadEmails().catch((error) => showError(error.message));
  });
  $("#intentFilter").addEventListener("change", () => {
    loadEmails().catch((error) => showError(error.message));
  });
  $("#searchFilter").addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => {
      loadEmails().catch((error) => showError(error.message));
    }, 250);
  });

  document.querySelectorAll('input[name="schedulerMode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const intervalInput = $("#intervalInput");
      intervalInput.disabled = radio.value !== "interval";
      if (radio.value === "interval" && !intervalInput.value) intervalInput.value = "60";
    });
  });

  $("#schedulerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const mode =
      document.querySelector('input[name="schedulerMode"]:checked')?.value === "interval"
        ? "interval"
        : "manual";
    const interval = Number($("#intervalInput").value || 60);
    const target = $("#targetInput").value.trim();
    if (
      mode === "interval" &&
      (!Number.isFinite(interval) || interval < 10 || interval > 86400)
    ) {
      setSettingsStatus("Interval must be between 10 and 86400 seconds.");
      return;
    }
    setSettingsStatus("Saving…");
    try {
      const result = await api("/api/scheduler", {
        method: "PATCH",
        body: JSON.stringify({
          mode,
          interval_seconds: Number.isFinite(interval) ? interval : 60,
          target_dag_id: target,
        }),
      });
      renderScheduler(result);
      setSettingsStatus(
        mode === "manual"
          ? "Saved. Manual only — press Run now when ready."
          : result.next_run_at
            ? `Saved. Next automatic run: ${formatDate(result.next_run_at)}.`
            : "Saved. Interval mode is on; set a target DAG to schedule runs."
      );
      await loadSettings();
    } catch (error) {
      setSettingsStatus(`Error: ${error.message}`);
    }
  });

  async function triggerNow() {
    setSettingsStatus("Triggering…");
    setOverviewRunStatus("Triggering…");
    try {
      const result = await api("/api/run-now", { method: "POST" });
      let outcome;
      if (result.ok) {
        outcome = `Started ${result.source_dag_id || "target DAG"} (${result.status}).`;
      } else if (result.skipped) {
        outcome = `Skipped: ${result.reason || "a run is already active"}.`;
      } else {
        outcome = `Failed: ${result.error || "unknown error"}.`;
      }
      setSettingsStatus(outcome);
      setOverviewRunStatus(outcome);
      await loadScheduler();
      await loadSettings();
    } catch (error) {
      setSettingsStatus(`Error: ${error.message}`);
      setOverviewRunStatus(`Error: ${error.message}`);
    }
  }

  $("#runNow").addEventListener("click", triggerNow);
  $("#overviewRunNow").addEventListener("click", triggerNow);

  document.querySelectorAll("[data-close-drawer]").forEach((element) => {
    element.addEventListener("click", closeDrawer);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refresh();
  });

  window.setInterval(refresh, 5000);
  refresh();
})();
