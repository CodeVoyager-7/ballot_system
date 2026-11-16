const DEFAULT_INVALID_MARK_RULES = ["circle", "x_mark", "filled_block", "black_dot", "scribble", "heavy_shading", "line"];
const INVALID_MARK_RULE_LABELS = {
  circle: "画圈 / O",
  x_mark: "打叉 / X",
  cross: "十字 / 叉号",
  filled_block: "涂黑块",
  black_dot: "黑点",
  scribble: "涂抹乱画",
  heavy_shading: "重度阴影",
  line: "线条",
};
const INVALID_MARK_RULE_ALIASES = {
  圆圈: "circle",
  画圈: "circle",
  圈: "circle",
  o: "circle",
  O: "circle",
  打叉: "x_mark",
  叉: "x_mark",
  叉号: "x_mark",
  x: "x_mark",
  X: "x_mark",
  十字: "cross",
  涂黑块: "filled_block",
  黑块: "filled_block",
  实心黑块: "filled_block",
  黑方块: "filled_block",
  黑点: "black_dot",
  点: "black_dot",
  涂抹: "scribble",
  乱画: "scribble",
  涂抹乱画: "scribble",
  重度阴影: "heavy_shading",
  阴影: "heavy_shading",
  线条: "line",
  线: "line",
};

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindRefresh();
  bindConfirmDialog();
  bindHistoryActions();
  bindCreateActivityForm();
  bindUploadForm();
  bindReviewForm();
  bindFilters();
  bindResetButton();
  bindAutoRefresh();
  loadOverview().catch(showGlobalError);
});

function bindNavigation() {
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.addEventListener("click", () => switchToSection(button.dataset.section));
  });
}

function bindRefresh() {
  document.getElementById("refresh-overview").addEventListener("click", () => {
    loadOverview().catch(showGlobalError);
  });
}

function bindHistoryActions() {
  document.getElementById("history-manage").addEventListener("click", () => {
    state.historySelectionMode = true;
    state.selectedActivityIds.clear();
    renderSidebar();
  });

  document.getElementById("history-select-all").addEventListener("click", () => {
    const activityIds = (state.overview?.activities || []).map((activity) => activity.activity_id).filter(Boolean);
    const allSelected = activityIds.length > 0 && activityIds.every((id) => state.selectedActivityIds.has(id));

    state.selectedActivityIds = allSelected ? new Set() : new Set(activityIds);
    renderSidebar();
  });

  document.getElementById("history-cancel").addEventListener("click", () => {
    state.historySelectionMode = false;
    state.selectedActivityIds.clear();
    renderSidebar();
  });

  document.getElementById("history-delete").addEventListener("click", async () => {
    const activityIds = Array.from(state.selectedActivityIds);
    if (activityIds.length === 0) {
      await showNoticeDialog({
        title: "未选择活动",
        message: "请先选择要删除的历史活动。",
      });
      return;
    }

    const confirmed = await showConfirmDialog({
      title: "删除历史活动",
      message: `将删除选中的 ${activityIds.length} 个活动，相关上传图片、识别结果、复核记录也会一并删除。此操作不可撤销。`,
      confirmText: "确认删除",
      variant: "danger",
    });
    if (!confirmed) {
      return;
    }

    try {
      const payload = await apiDeleteActivities(activityIds);
      state.historySelectionMode = false;
      state.selectedActivityIds.clear();
      applyOverview(payload.overview);
      await showNoticeDialog({
        title: "删除完成",
        message: `已删除 ${payload.deleted_count || 0} 个活动。`,
      });
    } catch (error) {
      await showNoticeDialog({
        title: "删除失败",
        message: error.message,
      });
    }
  });
}

let confirmDialogResolver = null;

function bindConfirmDialog() {
  const dialog = document.getElementById("confirm-dialog");
  const cancelButton = document.getElementById("confirm-dialog-cancel");
  const confirmButton = document.getElementById("confirm-dialog-confirm");

  const close = (confirmed) => {
    dialog.classList.add("hidden");
    const resolver = confirmDialogResolver;
    confirmDialogResolver = null;
    resolver?.(confirmed);
  };

  cancelButton.addEventListener("click", () => close(false));
  confirmButton.addEventListener("click", () => close(true));
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      close(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (!dialog.classList.contains("hidden") && event.key === "Escape") {
      close(false);
    }
  });
}

function showConfirmDialog({ title, message, confirmText, cancelText, variant = "danger", showCancel = true }) {
  const dialog = document.getElementById("confirm-dialog");
  const cancelButton = document.getElementById("confirm-dialog-cancel");
  const confirmButton = document.getElementById("confirm-dialog-confirm");

  document.getElementById("confirm-dialog-title").textContent = title || "确认操作";
  document.getElementById("confirm-dialog-message").textContent = message || "请确认是否继续。";
  confirmButton.textContent = confirmText || "确认";
  confirmButton.className = variant === "danger" ? "danger-btn" : "primary-btn";
  cancelButton.textContent = cancelText || "取消";
  cancelButton.classList.toggle("hidden", !showCancel);
  dialog.classList.remove("hidden");
  (showCancel ? cancelButton : confirmButton).focus();

  return new Promise((resolve) => {
    confirmDialogResolver = resolve;
  });
}

function showNoticeDialog({ title, message, confirmText }) {
  return showConfirmDialog({
    title,
    message,
    confirmText: confirmText || "知道了",
    variant: "primary",
    showCancel: false,
  });
}

function bindCreateActivityForm() {
  const form = document.getElementById("create-activity-form");
  const status = document.getElementById("create-activity-status");
  const input = document.getElementById("new-activity-name");
  const candidatesInput = document.getElementById("new-activity-candidates");
  const optionsInput = document.getElementById("new-activity-options");
  const invalidRuleOptions = document.getElementById("invalid-rule-options");
  const invalidRuleToggle = document.getElementById("invalid-rule-toggle");
  const templateSelect = document.getElementById("activity-template-select");

  invalidRuleToggle?.addEventListener("click", () => {
    const picker = invalidRuleToggle.closest(".rule-select");
    const isOpen = !invalidRuleOptions.classList.contains("hidden");
    invalidRuleOptions.classList.toggle("hidden", isOpen);
    picker?.classList.toggle("open", !isOpen);
  });

  invalidRuleOptions?.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
    checkbox.addEventListener("change", updateInvalidRuleSummary);
  });

  document.addEventListener("click", (event) => {
    const picker = invalidRuleToggle?.closest(".rule-select");
    if (!picker || picker.contains(event.target)) {
      return;
    }
    invalidRuleOptions.classList.add("hidden");
    picker.classList.remove("open");
  });

  updateInvalidRuleSummary();

  templateSelect?.addEventListener("change", () => {
    const templateId = templateSelect.value;
    const saved = (state.overview?.activity_templates || []).find((item) => item.template_id === templateId);
    if (!saved) return;
    input.value = saved.activity_name || "";
    candidatesInput.value = (saved.template?.candidates || []).join("\n");
    optionsInput.value = (saved.template?.options || []).join("、");
    applyInvalidRuleSelection(saved.template?.invalid_mark_rules || []);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const activityName = input.value.trim();
    status.textContent = "正在创建活动...";
    try {
      const payload = await createActivity({
        activity_name: activityName,
        candidates: parseListInput(candidatesInput.value),
        options: parseListInput(optionsInput.value),
        invalid_mark_rules: collectInvalidMarkRules(),
      });
      const activityId = payload?.activity?.activity_id;
      if (!activityId) {
        throw new Error("创建接口未返回活动编号。");
      }

      await loadActivity(activityId, true);
      await loadOverview();
      input.value = "";
      candidatesInput.value = "";
      optionsInput.value = "";
      applyInvalidRuleSelection(DEFAULT_INVALID_MARK_RULES);
      if (templateSelect) templateSelect.value = "";
      status.textContent = "活动已创建。";
      switchToSection("upload");
    } catch (error) {
      status.textContent = `创建失败：${error.message}`;
    }
  });
}

function bindUploadForm() {
  const fileInput = document.getElementById("file-input");
  const queue = document.getElementById("upload-queue");
  const status = document.getElementById("upload-status");
  const select = document.getElementById("activity-select");
  const dropzone = document.querySelector(".upload-dropzone");
  const resetButton = document.getElementById("reset-upload");
  const copyButton = document.getElementById("copy-mobile-link");
  const form = document.getElementById("upload-form");

  const renderQueue = () => {
    queue.innerHTML = "";
    state.uploadFiles.forEach((file) => {
      const item = document.createElement("div");
      item.className = "upload-item";
      item.textContent = `${file.name} · ${formatFileSize(file.size)}`;
      queue.appendChild(item);
    });
    if (!state.uploadFiles.length) {
      status.textContent = "";
    }
  };

  const setFiles = (files, append = false) => {
    const incoming = Array.from(files || []).filter(isSupportedImage);
    state.uploadFiles = append ? mergeFiles(state.uploadFiles, incoming) : incoming;
    if (files?.length && !incoming.length) {
      status.textContent = "只支持 png、jpg、jpeg、bmp、tif、tiff 格式。";
    }
    renderQueue();
  };

  fileInput.addEventListener("change", () => setFiles(fileInput.files));

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragging");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    setFiles(event.dataTransfer.files, true);
  });

  resetButton.addEventListener("click", () => {
    state.uploadFiles = [];
    fileInput.value = "";
    renderQueue();
    status.textContent = "已清空待上传文件。";
  });

  copyButton.addEventListener("click", async () => {
    const link = state.currentActivity?.mobile_entry_url;
    if (!link) {
      await showNoticeDialog({
        title: "未选择活动",
        message: "请先选择或创建活动，再复制手机上传链接。",
      });
      return;
    }
    const absoluteLink = toAbsoluteUrl(link);
    try {
      await copyTextToClipboard(absoluteLink);
      await showNoticeDialog({
        title: "复制成功",
        message: "手机上传链接已复制，可以直接发送给投票人使用。",
      });
    } catch (error) {
      await showNoticeDialog({
        title: "复制失败",
        message: `请手动复制：${absoluteLink}`,
      });
    }
  });

  select.addEventListener("change", async () => {
    const activityId = select.value;
    if (!activityId) {
      return;
    }
    try {
      await loadActivity(activityId, true);
    } catch (error) {
      showGlobalError(error);
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.uploadFiles.length) {
      status.textContent = "请先选择要上传的投票单图片。";
      return;
    }

    let targetActivityId = select.value || state.currentActivity?.activity_id;
    if (!targetActivityId) {
      const pendingName = document.getElementById("new-activity-name").value.trim();
      if (!pendingName) {
        status.textContent = "请先创建活动，或先在目标活动中选择一个活动。";
        return;
      }
      const payload = await createActivity({ activity_name: pendingName });
      const activityId = payload?.activity?.activity_id;
      if (!activityId) {
        throw new Error("创建接口未返回活动编号。");
      }
      document.getElementById("new-activity-name").value = "";
      await loadActivity(activityId, true);
      await loadOverview();
      targetActivityId = activityId;
    }

    status.textContent = "正在上传并识别，请稍候...";
    const formData = new FormData();
    formData.append("async_mode", "true");
    state.uploadFiles.forEach((file) => formData.append("files", file));

    try {
      let payload = await apiUploadToActivity(targetActivityId, formData);
      if (payload.task_id) {
        payload = await waitForTask(payload.task_id, status);
      }

      state.currentActivity = payload;
      state.selectedBallot = payload.ballots?.[0] || null;
      state.uploadFiles = [];
      fileInput.value = "";
      renderQueue();
      status.textContent = `识别完成，共处理 ${payload.ballot_count} 张投票单。`;
      await loadOverview();
      switchToSection("dashboard");
    } catch (error) {
      status.textContent = `上传失败：${error.message}`;
    }
  });
}

function bindReviewForm() {
  document.getElementById("review-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.currentActivity || !state.selectedBallot) {
      return;
    }

    const payload = {
      reviewed: document.getElementById("reviewed-checkbox").checked,
      comment: document.getElementById("review-comment").value,
      rows: collectCorrectionRows(),
    };

    try {
      const result = await apiCorrectBallot(state.currentActivity.activity_id, state.selectedBallot.ballot_id, payload);
      state.currentActivity = result.activity;
      state.selectedBallot = result.activity.ballots.find((item) => item.ballot_id === result.ballot.ballot_id) || result.ballot;
      await loadOverview();
      renderAll();
    } catch (error) {
      window.alert(`保存复核失败：${error.message}`);
    }
  });
}

function bindFilters() {
  document.getElementById("ballot-filter").addEventListener("change", renderBallotTable);
}

async function deleteDuplicateBallot(ballotId) {
  if (!state.currentActivity || !ballotId) return;
  const ballot = state.currentActivity.ballots.find((item) => item.ballot_id === ballotId);
  if (!ballot?.is_duplicate) {
    await showNoticeDialog({
      title: "不能删除",
      message: "只有后续重复票可以在这里删除，首次出现的原始票会保留。",
    });
    return;
  }

  const confirmed = await showConfirmDialog({
    title: "删除重复票",
    message: `将删除 ${ballot.file_name || ballot.ballot_id}，该票的识别结果会从统计分析中移除。此操作不可撤销。`,
    confirmText: "确认删除",
    variant: "danger",
  });
  if (!confirmed) return;

  try {
    const result = await apiDeleteBallot(state.currentActivity.activity_id, ballotId);
    state.currentActivity = result.activity;
    state.selectedBallot =
      state.selectedBallot?.ballot_id === ballotId
        ? state.currentActivity.ballots?.[0] || null
        : state.currentActivity.ballots.find((item) => item.ballot_id === state.selectedBallot?.ballot_id) || state.currentActivity.ballots?.[0] || null;
    await loadOverview();
    renderAll();
    await showNoticeDialog({
      title: "删除完成",
      message: "重复票已删除，统计分析已重新计算。",
    });
  } catch (error) {
    await showNoticeDialog({
      title: "删除失败",
      message: error.message,
    });
  }
}

function bindResetButton() {
  document.getElementById("reset-current").addEventListener("click", async () => {
    try {
      await apiResetCurrentActivity();
      state.currentActivity = null;
      state.selectedBallot = null;
      await loadOverview();
      switchToSection("upload");
    } catch (error) {
      showGlobalError(error);
    }
  });
}

function bindAutoRefresh() {
  window.setInterval(() => {
    refreshOverviewSilently();
  }, 10000);

  window.addEventListener("focus", () => {
    refreshOverviewSilently();
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      refreshOverviewSilently();
    }
  });
}

async function createActivity(payload) {
  return apiCreateActivity(payload);
}

async function loadOverview() {
  const payload = await apiGetOverview();
  applyOverview(payload);
}

async function refreshOverviewSilently() {
  if (!shouldAutoRefresh() || state.autoRefreshInFlight) {
    return;
  }

  state.autoRefreshInFlight = true;
  try {
    await loadOverview();
  } catch (error) {
    console.warn("自动刷新活动统计失败", error);
  } finally {
    state.autoRefreshInFlight = false;
  }
}

function shouldAutoRefresh() {
  if (document.hidden || !state.overview) {
    return false;
  }

  const activePage = document.querySelector(".page.active")?.id;
  if (activePage === "review") {
    return false;
  }

  return true;
}

function applyOverview(payload) {
  state.overview = payload;
  const currentId = payload.current_activity?.activity_id || state.currentActivity?.activity_id;
  const validIds = new Set((payload.activities || []).map((item) => item.activity_id));

  if (payload.current_activity) {
    state.currentActivity = payload.current_activity;
  } else if (!currentId || !validIds.has(currentId)) {
    state.currentActivity = null;
    state.selectedBallot = null;
  }

  state.selectedActivityIds = new Set(Array.from(state.selectedActivityIds).filter((id) => validIds.has(id)));
  if (!validIds.size) {
    state.historySelectionMode = false;
  }

  if (state.currentActivity && state.selectedBallot) {
    state.selectedBallot =
      state.currentActivity.ballots.find((item) => item.ballot_id === state.selectedBallot.ballot_id) ||
      state.currentActivity.ballots[0] ||
      null;
  }

  renderAll();
}

async function loadActivity(activityId, activate) {
  const detail = await apiGetActivity(activityId);
  state.currentActivity = detail;
  state.selectedBallot = detail.ballots?.[0] || null;

  if (activate) {
    state.overview = await apiActivateActivity(activityId);
  }

  renderAll();
}

async function waitForTask(taskId, statusElement) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const task = await apiGetTask(taskId);
    statusElement.textContent = task.message || "识别中...";
    if (task.status === "completed") {
      return task.result;
    }
    if (task.status === "failed") {
      throw new Error(task.error || "识别任务失败。");
    }
  }
}

function parseListInput(value) {
  return String(value || "")
    .split(/[\n,，、;；/|]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function collectInvalidMarkRules() {
  return Array.from(document.querySelectorAll('#invalid-rule-options input[type="checkbox"]:checked')).map(
    (input) => input.value,
  );
}

function applyInvalidRuleSelection(rules) {
  const normalized = new Set((rules || []).map(normalizeInvalidMarkRule).filter(Boolean));
  document.querySelectorAll('#invalid-rule-options input[type="checkbox"]').forEach((input) => {
    input.checked = normalized.has(input.value);
  });
  updateInvalidRuleSummary();
}

function updateInvalidRuleSummary() {
  const summary = document.getElementById("invalid-rule-summary");
  if (!summary) return;
  const selected = Array.from(document.querySelectorAll('#invalid-rule-options input[type="checkbox"]:checked')).map(
    (input) => INVALID_MARK_RULE_LABELS[input.value] || input.value,
  );
  if (!selected.length) {
    summary.textContent = "未选择无效标记规则";
    return;
  }
  summary.textContent = `已选 ${selected.length} 项：${selected.slice(0, 3).join("、")}${selected.length > 3 ? " 等" : ""}`;
}

function normalizeInvalidMarkRule(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return INVALID_MARK_RULE_ALIASES[raw] || raw.toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
}

function collectCorrectionRows() {
  return Array.from(document.querySelectorAll("#review-rows-body tr[data-row-index]")).map((row) => ({
    row_index: Number(row.dataset.rowIndex),
    candidate_name: row.querySelector(".correction-candidate")?.value || "",
    selected_option: row.querySelector(".correction-option")?.value || null,
    issue: row.querySelector(".correction-issue")?.value || null,
    comment: row.querySelector(".correction-comment")?.value || "",
  }));
}

function isSupportedImage(file) {
  const name = file.name.toLowerCase();
  return [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"].some((suffix) => name.endsWith(suffix));
}

function mergeFiles(currentFiles, incomingFiles) {
  const seen = new Set(currentFiles.map(fileKey));
  const merged = [...currentFiles];
  incomingFiles.forEach((file) => {
    const key = fileKey(file);
    if (!seen.has(key)) {
      seen.add(key);
      merged.push(file);
    }
  });
  return merged;
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function toAbsoluteUrl(url) {
  if (!url) return "";
  return new URL(url, window.location.origin).toString();
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  try {
    if (!document.execCommand("copy")) {
      throw new Error("copy command failed");
    }
  } finally {
    document.body.removeChild(textarea);
  }
}

function showGlobalError(error) {
  window.alert(`发生错误：${error.message}`);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
