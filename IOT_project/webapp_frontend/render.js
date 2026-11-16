function renderAll() {
  renderSidebar();
  renderActivitySelect();
  renderTemplateSelect();
  renderDashboard();
  renderUploadHero();
  renderBallotTable();
  renderReviewPanel();
  renderStats();
}

function renderSidebar() {
  const label = document.getElementById("current-activity-label");
  const historyList = document.getElementById("history-list");
  const selectionStatus = document.getElementById("history-selection-status");
  const manageButton = document.getElementById("history-manage");
  const selectAllButton = document.getElementById("history-select-all");
  const deleteButton = document.getElementById("history-delete");
  const cancelButton = document.getElementById("history-cancel");
  const activities = state.overview?.activities || [];
  const selectableActivityIds = activities.map((activity) => activity.activity_id).filter(Boolean);
  const allSelected =
    selectableActivityIds.length > 0 && selectableActivityIds.every((id) => state.selectedActivityIds.has(id));

  label.textContent = state.currentActivity
    ? `当前活动：${state.currentActivity.activity_name}`
    : "当前未选中活动";

  manageButton.classList.toggle("hidden", state.historySelectionMode);
  selectAllButton.classList.toggle("hidden", !state.historySelectionMode);
  deleteButton.classList.toggle("hidden", !state.historySelectionMode);
  cancelButton.classList.toggle("hidden", !state.historySelectionMode);
  selectAllButton.disabled = selectableActivityIds.length === 0;
  selectAllButton.textContent = allSelected ? "取消全选" : "全选";
  deleteButton.disabled = state.selectedActivityIds.size === 0;

  if (state.historySelectionMode) {
    selectionStatus.classList.remove("hidden");
    selectionStatus.textContent = `已选中 ${state.selectedActivityIds.size} 个活动`;
  } else {
    selectionStatus.classList.add("hidden");
    selectionStatus.textContent = "";
  }

  if (!activities.length) {
    historyList.innerHTML = '<div class="history-empty">还没有历史活动</div>';
    return;
  }

  historyList.innerHTML = activities
    .map((activity) => {
      const active = state.currentActivity?.activity_id === activity.activity_id;
      const checked = state.selectedActivityIds.has(activity.activity_id);
      return `
        <button class="history-item ${active ? "active" : ""} ${state.historySelectionMode ? "selection-mode" : ""}" data-activity-id="${escapeHtml(activity.activity_id)}" type="button">
          ${
            state.historySelectionMode
              ? `<span class="history-check"><input class="history-checkbox" type="checkbox" data-activity-id="${escapeHtml(activity.activity_id)}" ${
                  checked ? "checked" : ""
                } /></span>`
              : ""
          }
          <span class="history-status">${active ? "当前" : "历史"}</span>
          <strong>${escapeHtml(activity.activity_name)}</strong>
          <span class="history-meta">${escapeHtml(activity.updated_at || activity.created_at || "")}</span>
          <span class="history-stats">${activity.ballot_count || 0} 张投票单 · ${activity.abnormal_ballot_count || 0} 张异常</span>
        </button>
      `;
    })
    .join("");

  historyList.querySelectorAll(".history-item").forEach((button) => {
    button.addEventListener("click", async (event) => {
      const activityId = button.dataset.activityId;
      if (state.historySelectionMode) {
        if (event.target instanceof HTMLInputElement) {
          toggleHistorySelection(activityId, event.target.checked);
        } else {
          toggleHistorySelection(activityId, !state.selectedActivityIds.has(activityId));
        }
        return;
      }

      try {
        await loadActivity(activityId, true);
        switchToSection("dashboard");
      } catch (error) {
        showGlobalError(error);
      }
    });
  });

  historyList.querySelectorAll(".history-checkbox").forEach((checkbox) => {
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      toggleHistorySelection(checkbox.dataset.activityId, checkbox.checked);
    });
  });
}

function toggleHistorySelection(activityId, checked) {
  if (!activityId) return;
  if (checked) {
    state.selectedActivityIds.add(activityId);
  } else {
    state.selectedActivityIds.delete(activityId);
  }
  renderSidebar();
}

function renderActivitySelect() {
  const select = document.getElementById("activity-select");
  const activities = state.overview?.activities || [];
  const currentId = state.currentActivity?.activity_id || "";

  select.innerHTML = activities.length
    ? activities
        .map(
          (activity) =>
            `<option value="${escapeHtml(activity.activity_id)}" ${activity.activity_id === currentId ? "selected" : ""}>${escapeHtml(activity.activity_name)}</option>`,
        )
        .join("")
    : '<option value="">暂无活动，请先创建</option>';
  select.disabled = activities.length === 0;
}

function renderTemplateSelect() {
  const select = document.getElementById("activity-template-select");
  if (!select) return;
  const templates = state.overview?.activity_templates || [];
  select.innerHTML = [
    '<option value="">不使用模板</option>',
    ...templates.map(
      (item) =>
        `<option value="${escapeHtml(item.template_id)}">${escapeHtml(item.template_name || item.activity_name || item.template_id)}</option>`,
    ),
  ].join("");
  select.disabled = templates.length === 0;
}

function renderDashboardLegacy() {
  const globalSummary = document.getElementById("global-summary");
  const kpiGrid = document.getElementById("kpi-grid");
  const optionChart = document.getElementById("option-chart");
  const recentUploadCard = document.getElementById("recent-upload-card");
  const exportCsv = document.getElementById("export-csv");
  const exportXlsx = document.getElementById("export-xlsx");
  const overview = state.overview;
  const activity = state.currentActivity;

  if (!overview) {
    return;
  }

  globalSummary.innerHTML = `
    <div class="summary-chip"><strong>${overview.activity_count || 0}</strong><br /><span class="muted">历史活动总数</span></div>
    <div class="summary-chip"><strong>${overview.ballot_count || 0}</strong><br /><span class="muted">累计投票单数量</span></div>
    <div class="summary-chip"><strong>${overview.abnormal_ballot_count || 0}</strong><br /><span class="muted">累计异常投票单</span></div>
  `;

  if (!activity) {
    kpiGrid.innerHTML = "";
    optionChart.innerHTML = '<div class="muted">请选择一个活动查看结果分布。</div>';
    recentUploadCard.innerHTML = "当前没有选中的活动。";
    exportCsv.classList.add("hidden");
    exportXlsx.classList.add("hidden");
    return;
  }

  exportCsv.classList.remove("hidden");
  exportXlsx.classList.remove("hidden");
  exportCsv.href = `/api/activities/${encodeURIComponent(activity.activity_id)}/export.csv`;
  exportXlsx.href = `/api/activities/${encodeURIComponent(activity.activity_id)}/export.xlsx`;

  const kpis = [
    ["当前活动投票单", activity.ballot_count, "当前活动累计识别的投票单数量"],
    ["异常投票单", activity.abnormal_ballot_count, "包含缺勾、多勾或识别失败的投票单"],
    ["上传批次", activity.upload_count, "该活动下累计上传次数"],
    ["候选人数量", Object.keys(activity.candidate_totals || {}).length, "参与统计的候选人条目数"],
  ];

  kpiGrid.innerHTML = kpis
    .map(
      ([label, value, description]) =>
        `<article class="kpi-card"><span class="muted">${label}</span><strong>${value}</strong><p class="muted">${description}</p></article>`,
    )
    .join("");

  optionChart.innerHTML = buildBarChart(activity.option_totals, "当前活动还没有识别结果。");
  if (!activity.latest_upload) {
    recentUploadCard.innerHTML = "当前活动还没有上传记录。";
    return;
  }

  recentUploadCard.innerHTML = `
    <div class="summary-grid">
      <div class="summary-chip"><strong>${escapeHtml(activity.latest_upload.upload_id)}</strong><br /><span class="muted">最新批次编号</span></div>
      <div class="summary-chip"><strong>${activity.latest_upload.ballot_count || 0}</strong><br /><span class="muted">本次投票单数量</span></div>
      <div class="summary-chip"><strong>${activity.latest_upload.abnormal_ballot_count || 0}</strong><br /><span class="muted">本次异常数量</span></div>
      <div class="summary-chip"><strong>${escapeHtml(activity.latest_upload.created_at || "")}</strong><br /><span class="muted">上传时间</span></div>
    </div>
  `;
}

function renderUploadHero() {
  const hero = document.getElementById("activity-hero");
  const text = document.getElementById("mobile-link-text");
  const activity = state.currentActivity;

  if (!activity) {
    hero.classList.add("empty-state");
    hero.innerHTML = "还没有选中活动。可以先创建活动，再上传投票单。";
    text.textContent = "请先选择或创建活动。";
    return;
  }

  hero.classList.remove("empty-state");
  hero.innerHTML = `
    <div class="summary-grid">
      <div class="summary-chip"><strong>${escapeHtml(activity.activity_name)}</strong><br /><span class="muted">当前活动</span></div>
      <div class="summary-chip"><strong>${activity.ballot_count || 0}</strong><br /><span class="muted">累计投票单</span></div>
      <div class="summary-chip"><strong>${activity.upload_count || 0}</strong><br /><span class="muted">累计上传批次</span></div>
      <div class="summary-chip"><strong>${activity.abnormal_ballot_count || 0}</strong><br /><span class="muted">异常投票单</span></div>
    </div>
  `;
  text.textContent = toAbsoluteUrl(activity.mobile_entry_url);
}

function renderBallotTableLegacy() {
  const tbody = document.getElementById("ballot-table-body");
  const activity = state.currentActivity;
  if (!activity) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">请先选择一个活动。</td></tr>';
    return;
  }

  const filter = document.getElementById("ballot-filter").value;
  const ballots = (activity.ballots || []).filter((ballot) => {
    if (filter === "abnormal") return ballot.is_abnormal;
    if (filter === "normal") return !ballot.is_abnormal;
    return true;
  });

  if (!ballots.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">当前筛选条件下没有投票单。</td></tr>';
    return;
  }

  tbody.innerHTML = ballots
    .map((ballot) => {
      const review = ballot.review || {};
      const abnormalText = ballot.abnormal_types?.length ? ballot.abnormal_types.join(" / ") : "正常";
      return `
        <tr>
          <td>${escapeHtml(ballot.file_name)}</td>
          <td>${escapeHtml(ballot.upload_id || "-")}</td>
          <td><span class="badge ${ballot.is_abnormal ? "abnormal" : "normal"}">${ballot.is_abnormal ? "异常" : "正常"}</span></td>
          <td>${escapeHtml(abnormalText)}</td>
          <td><span class="badge ${review.reviewed ? "reviewed" : "normal"}">${review.reviewed ? "已复核" : "未复核"}</span></td>
          <td><button class="secondary-btn ballot-detail-btn" data-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">查看详情</button></td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".ballot-detail-btn").forEach((button) => {
    button.addEventListener("click", () => {
      selectBallot(button.dataset.ballotId);
      switchToSection("review");
    });
  });
}

function renderReviewPanel() {
  const empty = document.getElementById("review-empty");
  const layout = document.getElementById("review-layout");
  const ballot = state.selectedBallot;

  if (!ballot) {
    empty.classList.remove("hidden");
    layout.classList.add("hidden");
    return;
  }

  empty.classList.add("hidden");
  layout.classList.remove("hidden");

  document.getElementById("review-title").textContent = ballot.file_name || "投票单图片";
  document.getElementById("review-image").src = ballot.original_url;
  document.getElementById("reviewed-checkbox").checked = Boolean(ballot.review?.reviewed);
  document.getElementById("review-comment").value = ballot.review?.comment || "";

  const abnormalText = ballot.abnormal_types?.length ? ballot.abnormal_types.join(" / ") : "正常";
  const validVoteCount = ballot.abnormal_details?.valid_vote_count ?? Object.values(ballot.summary || {}).reduce((sum, value) => sum + value, 0);
  const errorMessage = ballot.abnormal_details?.error_message;

  document.getElementById("review-summary").innerHTML = `
    <div class="summary-grid">
      <div class="summary-chip"><strong>${escapeHtml(abnormalText)}</strong><br /><span class="muted">异常类型</span></div>
      <div class="summary-chip"><strong>${validVoteCount}</strong><br /><span class="muted">有效勾选数量</span></div>
      <div class="summary-chip"><strong>${ballot.review?.reviewed ? "已复核" : "未复核"}</strong><br /><span class="muted">复核状态</span></div>
      <div class="summary-chip"><strong>${escapeHtml(ballot.upload_id || "-")}</strong><br /><span class="muted">所属上传批次</span></div>
      ${errorMessage ? `<div class="summary-chip summary-error"><strong>识别报错</strong><br /><span class="muted">${escapeHtml(errorMessage)}</span></div>` : ""}
    </div>
  `;

  const tbody = document.getElementById("review-rows-body");
  if (!ballot.rows?.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">当前投票单没有返回逐行识别结果。</td></tr>';
    return;
  }

  const optionChoices = correctionOptionChoices(ballot);
  const issueChoices = [
    ["", "正常"],
    ["missing_mark", "缺少勾选"],
    ["multiple_marks", "多个勾选"],
    ["invalid_mark", "无效标记"],
    ["unclear_mark", "无法判断"],
    ["recognition_failed", "识别失败"],
  ];

  tbody.innerHTML = ballot.rows
    .map((row) => {
      const rowClass = row.issue === "multiple_marks" ? "row-danger" : row.issue ? "row-warning" : "";
      const selectedLabel = displayOption(row.selected_option, ballot.option_labels);
      return `
        <tr class="${rowClass}" data-row-index="${row.row_index}">
          <td>${row.row_index}</td>
          <td>
            ${correctionCandidateControl(row.candidate_name)}
          </td>
          <td>
            <select class="correction-option compact-input">
              <option value="">未识别/不计票</option>
              ${optionChoices
                .map((option) => `<option value="${escapeHtml(option)}" ${option === selectedLabel ? "selected" : ""}>${escapeHtml(option)}</option>`)
                .join("")}
            </select>
          </td>
          <td>
            <select class="correction-issue compact-input">
              ${issueChoices
                .map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === (row.issue || "") ? "selected" : ""}>${escapeHtml(label)}</option>`)
                .join("")}
            </select>
          </td>
          <td><input class="correction-comment compact-input" value="${escapeHtml(row.correction?.comment || "")}" placeholder="可选" /></td>
        </tr>
      `;
    })
    .join("");
}

function renderStats() {
  const candidateChart = document.getElementById("candidate-chart");
  const abnormalChart = document.getElementById("abnormal-chart");
  const head = document.getElementById("stats-table-head");
  const body = document.getElementById("stats-table-body");
  const activity = state.currentActivity;

  if (!activity) {
    candidateChart.innerHTML = '<div class="muted">请先选择一个活动。</div>';
    abnormalChart.innerHTML = '<div class="muted">请先选择一个活动。</div>';
    head.innerHTML = "";
    body.innerHTML = '<tr><td class="muted">请先选择一个活动。</td></tr>';
    return;
  }

  const ranked = Object.entries(activity.candidate_totals || {})
    .map(([name, counts]) => ({ name, counts, total: statsDisplayedTotal(counts, activity) }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 12);

  candidateChart.innerHTML = ranked.length
    ? ranked
        .map(
          (item, index) => `
            <div class="rank-item">
              <div class="rank-index">${index + 1}</div>
              <div><strong>${escapeHtml(item.name)}</strong><br /><span class="muted">${escapeHtml(
                formatCandidateCounts(item.counts, activity),
              )}</span></div>
              <strong>${item.total}</strong>
            </div>
          `,
        )
        .join("")
    : '<div class="muted">暂无候选人统计。</div>';

  abnormalChart.innerHTML = buildBarChart(activity.abnormal_type_totals, "暂无异常类型统计。");

  abnormalChart.innerHTML = buildBarChart(translateAbnormalTotals(activity.abnormal_type_totals), "暂无异常类型统计。");

  const optionKeys = statsOptionKeys(activity);
  head.innerHTML = optionKeys.length
    ? `<tr><th>候选人</th>${optionKeys.map((key) => `<th>${escapeHtml(key)}</th>`).join("")}<th>总计</th></tr>`
    : "<tr><th>候选人</th><th>总计</th></tr>";

  const rows = Object.entries(activity.candidate_totals || {});
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="99" class="muted">暂无统计明细。</td></tr>';
    return;
  }

  body.innerHTML = rows
    .map(([name, counts]) => {
      const total = statsDisplayedTotal(counts, activity);
      if (!optionKeys.length) {
        return `<tr><td>${escapeHtml(name)}</td><td>${total}</td></tr>`;
      }
      return `<tr><td>${escapeHtml(name)}</td>${optionKeys.map((key) => `<td>${statsOptionCount(counts, key)}</td>`).join("")}<td>${total}</td></tr>`;
    })
    .join("");
}

const OPTION_ALIASES = {
  优秀: ["excellent"],
  合格: ["qualified"],
  不合格: ["unqualified"],
  基本合格: ["basic_qualified", "basically_qualified"],
  同意: ["agree"],
  不同意: ["disagree"],
  通过: ["pass"],
  未通过: ["fail", "not_passed"],
  未勾选: ["not_checked"],
};

const ABNORMAL_TYPE_LABELS = {
  blank_ballot: "空票",
  multiple_marks: "多个勾选",
  missing_marks: "缺少勾选",
  missing_mark: "缺少勾选",
  recognition_failed: "识别失败",
  invalid_mark: "无效标记",
  unclear_mark: "标记不清",
  unknown_option: "未知选项",
  duplicate: "疑似重复",
};

function statsOptionKeys(activity) {
  const templateOptions = activity?.template?.options || [];
  if (templateOptions.length) {
    return templateOptions;
  }
  const keys = Object.keys(activity?.option_totals || {});
  return keys.filter((key) => {
    const mappedLabel = Object.entries(OPTION_ALIASES).find(([, aliases]) => aliases.includes(key))?.[0];
    return !mappedLabel || !keys.includes(mappedLabel);
  });
}

function statsOptionCount(counts, option) {
  if (!counts) return 0;
  const aliases = statsOptionAliases(option);
  const aliasKeys = new Set(aliases.map(normalizeStatsOptionKey));
  return Object.entries(counts).reduce((sum, [key, value]) => {
    if (aliases.includes(key) || aliasKeys.has(normalizeStatsOptionKey(key))) {
      return sum + Number(value || 0);
    }
    return sum;
  }, 0);
}

function statsOptionAliases(option) {
  const aliases = new Set([option, ...(OPTION_ALIASES[option] || [])]);
  const optionText = String(option || "");
  const optionKey = normalizeStatsOptionKey(optionText);

  Object.entries(OPTION_ALIASES).forEach(([label, labelAliases]) => {
    const labelKey = normalizeStatsOptionKey(label);
    if (labelKey && optionKey.includes(labelKey)) {
      aliases.add(label);
      labelAliases.forEach((alias) => aliases.add(alias));
    }
  });

  if (isPositiveInstructionOption(optionText)) {
    ["同意", "赞成", "通过", "已勾选", "勾选", "agree", "agreed", "checked", "selected", "yes", "pass"].forEach((alias) =>
      aliases.add(alias),
    );
  }

  return Array.from(aliases).filter(Boolean);
}

function normalizeStatsOptionKey(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[\s_\-.,，、;；:：/|()（）[\]【】"'`]+/g, "");
}

function isPositiveInstructionOption(option) {
  const key = normalizeStatsOptionKey(option);
  return (
    (key.includes("同意") && !key.includes("不同意")) ||
    key.includes("赞成") ||
    (key.includes("通过") && !key.includes("不通过") && !key.includes("未通过")) ||
    key.includes("agree") ||
    key.includes("checked")
  );
}

function statsDisplayedTotal(counts, activity) {
  return statsOptionKeys(activity).reduce((sum, option) => sum + statsOptionCount(counts, option), 0);
}

function formatCandidateCounts(counts, activity) {
  return statsOptionKeys(activity)
    .map((option) => `${option}:${statsOptionCount(counts, option)}`)
    .join(" / ");
}

function translateAbnormalTotals(totals) {
  const translated = {};
  Object.entries(totals || {}).forEach(([key, value]) => {
    const label = ABNORMAL_TYPE_LABELS[key] || key;
    translated[label] = (translated[label] || 0) + Number(value || 0);
  });
  return translated;
}

function buildBarChart(data, emptyText) {
  const entries = Object.entries(data || {}).filter(([, value]) => Number(value) > 0);
  if (!entries.length) {
    return `<div class="muted">${escapeHtml(emptyText || "暂无数据。")}</div>`;
  }

  const maxValue = Math.max(...entries.map(([, value]) => Number(value)), 1);
  return entries
    .map(([label, value]) => {
      const width = Math.max(4, Math.round((Number(value) / maxValue) * 100));
      return `
        <div class="bar-row">
          <div class="bar-meta"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
        </div>
      `;
    })
    .join("");
}

function switchToSection(sectionId) {
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.classList.toggle("active", button.dataset.section === sectionId);
  });
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === sectionId);
  });
}

function selectBallot(ballotId) {
  if (!state.currentActivity) return;
  state.selectedBallot = state.currentActivity.ballots.find((item) => item.ballot_id === ballotId) || null;
  renderReviewPanel();
}

function displayOption(optionCode, optionLabels) {
  if (!optionCode) return "未识别";
  return optionLabels?.[optionCode] || optionCode;
}

function correctionCandidateControl(currentName) {
  const candidates = state.currentActivity?.template?.candidates || [];
  if (!candidates.length) {
    return `<input class="correction-candidate compact-input" value="${escapeHtml(currentName || "")}" />`;
  }
  const choices = Array.from(new Set([...candidates, currentName].filter(Boolean)));
  return `
    <select class="correction-candidate compact-input">
      ${choices.map((name) => `<option value="${escapeHtml(name)}" ${name === currentName ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}
    </select>
  `;
}

function correctionOptionChoices(ballot) {
  const fromTemplate = state.currentActivity?.template?.options || [];
  const fromLabels = Object.values(ballot.option_labels || {});
  const fromSummary = Object.keys(ballot.summary || {});
  return Array.from(new Set([...fromTemplate, ...fromLabels, ...fromSummary].filter(Boolean)));
}

function isVoidBallot(ballot) {
  const validVoteCount = Number(ballot?.abnormal_details?.valid_vote_count ?? 0);
  return Boolean(ballot?.is_abnormal) && validVoteCount === 0;
}

function renderDashboard() {
  const globalSummary = document.getElementById("global-summary");
  const kpiGrid = document.getElementById("kpi-grid");
  const optionChart = document.getElementById("option-chart");
  const recentUploadCard = document.getElementById("recent-upload-card");
  const exportCsv = document.getElementById("export-csv");
  const exportXlsx = document.getElementById("export-xlsx");
  const overview = state.overview;
  const activity = state.currentActivity;

  if (!overview) return;

  globalSummary.innerHTML = `
    <div class="summary-chip"><strong>${overview.activity_count || 0}</strong><br /><span class="muted">历史活动</span></div>
    <div class="summary-chip"><strong>${overview.ballot_count || 0}</strong><br /><span class="muted">累计投票单</span></div>
    <div class="summary-chip"><strong>${overview.abnormal_ballot_count || 0}</strong><br /><span class="muted">累计异常票</span></div>
  `;

  if (!activity) {
    kpiGrid.innerHTML = "";
    optionChart.innerHTML = '<div class="muted">请选择一个活动查看统计。</div>';
    recentUploadCard.innerHTML = "当前没有选中活动。";
    exportCsv.classList.add("hidden");
    exportXlsx.classList.add("hidden");
    return;
  }

  exportCsv.classList.remove("hidden");
  exportXlsx.classList.remove("hidden");
  exportCsv.href = `/api/activities/${encodeURIComponent(activity.activity_id)}/export.csv`;
  exportXlsx.href = `/api/activities/${encodeURIComponent(activity.activity_id)}/export.xlsx`;

  const kpis = [
    ["总票数", activity.ballot_count || 0, "当前活动累计识别的投票单数量", null],
    ["有效勾选", activity.valid_vote_count || 0, "所有候选人行中已计入统计的有效结果", null],
    ["异常票", activity.abnormal_ballot_count || 0, "点击查看异常票列表", "abnormal"],
    ["作废/无效票", activity.void_ballot_count || 0, "异常且没有任何有效勾选的投票单", "void"],
    ["疑似重复票", activity.duplicate_ballot_count || 0, "点击查看疑似重复票", "duplicate"],
    ["复核完成率", `${activity.review_completion_rate || 0}%`, `${activity.reviewed_ballot_count || 0}/${activity.ballot_count || 0} 张已复核`, null],
  ];

  kpiGrid.innerHTML = kpis
    .map(
      ([label, value, description, filter]) => `
        <article class="kpi-card ${filter ? "clickable" : ""}" ${filter ? `data-ballot-filter="${filter}"` : ""}>
          <span class="muted">${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <p class="muted">${escapeHtml(description)}</p>
        </article>
      `,
    )
    .join("");
  kpiGrid.querySelectorAll("[data-ballot-filter]").forEach((card) => {
    card.addEventListener("click", () => {
      const filter = card.dataset.ballotFilter;
      const select = document.getElementById("ballot-filter");
      if (select) select.value = filter;
      renderBallotTable();
      switchToSection("ballots");
    });
  });

  optionChart.innerHTML = buildBarChart(activity.option_totals, "当前活动还没有识别结果。");
  recentUploadCard.innerHTML = buildDashboardStatus(activity);
  recentUploadCard.querySelectorAll("[data-ballot-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectBallot(button.dataset.ballotId);
      switchToSection("review");
    });
  });
  recentUploadCard.querySelectorAll("[data-delete-duplicate-ballot-id]").forEach((button) => {
    button.addEventListener("click", () => {
      deleteDuplicateBallot(button.dataset.deleteDuplicateBallotId);
    });
  });
}

function buildDashboardStatus(activity) {
  const latest = activity.latest_upload
    ? `
      <div class="summary-grid">
        <div class="summary-chip"><strong>${escapeHtml(activity.latest_upload.upload_id)}</strong><br /><span class="muted">最新批次</span></div>
        <div class="summary-chip"><strong>${activity.latest_upload.ballot_count || 0}</strong><br /><span class="muted">本次投票单</span></div>
        <div class="summary-chip"><strong>${activity.latest_upload.abnormal_ballot_count || 0}</strong><br /><span class="muted">本次异常</span></div>
        <div class="summary-chip"><strong>${escapeHtml(activity.latest_upload.created_at || "")}</strong><br /><span class="muted">上传时间</span></div>
      </div>
    `
    : '<div class="empty-state">当前活动还没有上传记录。</div>';
  return `
    ${latest}
    <div class="dashboard-split">
      <section>
        <h4>异常类型分布</h4>
        ${buildBarChart(translateAbnormalTotals(activity.abnormal_type_totals), "暂无异常类型统计。")}
      </section>
      <section>
        <h4>疑似重复票</h4>
        ${buildDuplicateGroups(activity.duplicate_groups)}
      </section>
    </div>
  `;
}

function buildDuplicateGroups(groups) {
  if (!groups?.length) {
    return '<div class="muted">当前活动还没有发现疑似重复票。</div>';
  }
  return groups
    .map(
      (group) => `
        <div class="duplicate-group">
          <div><strong>${escapeHtml(group.group_id)}</strong> <span class="badge abnormal">${escapeHtml(group.confidence || "suspected")}</span></div>
          <p class="muted">${escapeHtml(group.reason || "")}</p>
          <div class="duplicate-actions">
            ${(group.ballots || [])
              .map(
                (ballot) =>
                  `<span class="duplicate-ballot-action">
                    <button class="secondary-btn compact-btn" data-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">${escapeHtml(
                      ballot.file_name || ballot.ballot_id,
                    )}</button>
                    <span class="badge ${ballot.is_original ? "normal" : "abnormal"}">${ballot.is_original ? "原始票" : "重复票"}</span>
                    ${
                      ballot.is_duplicate
                        ? `<button class="secondary-btn compact-btn danger-lite" data-delete-duplicate-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">删除重复票</button>`
                        : ""
                    }
                  </span>`,
              )
              .join("")}
          </div>
        </div>
      `,
    )
    .join("");
}

function renderBallotTable() {
  const tbody = document.getElementById("ballot-table-body");
  const activity = state.currentActivity;
  if (!activity) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">请选择一个活动。</td></tr>';
    return;
  }

  const filter = document.getElementById("ballot-filter").value;
  const ballots = (activity.ballots || []).filter((ballot) => {
    if (filter === "abnormal") return ballot.is_abnormal;
    if (filter === "normal") return !ballot.is_abnormal;
    if (filter === "void") return isVoidBallot(ballot);
    if (filter === "duplicate") return ballot.is_duplicate;
    return true;
  });

  if (!ballots.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">当前筛选条件下没有投票单。</td></tr>';
    return;
  }

  tbody.innerHTML = ballots
    .map((ballot) => {
      const review = ballot.review || {};
      const abnormalText = ballot.abnormal_types?.length ? ballot.abnormal_types.join(" / ") : "正常";
      const duplicateText = ballot.is_duplicate ? ` · 重复组 ${ballot.duplicate_group_id}` : "";
      return `
        <tr>
          <td>${escapeHtml(ballot.file_name)}</td>
          <td>${escapeHtml(ballot.upload_id || "-")}</td>
          <td><span class="badge ${ballot.is_abnormal ? "abnormal" : "normal"}">${ballot.is_abnormal ? "异常" : "正常"}</span></td>
          <td>${escapeHtml(abnormalText + duplicateText)}</td>
          <td><span class="badge ${review.reviewed ? "reviewed" : "normal"}">${review.reviewed ? "已复核" : "未复核"}</span></td>
          <td><button class="secondary-btn ballot-detail-btn" data-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">查看详情</button></td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".ballot-detail-btn").forEach((button) => {
    button.addEventListener("click", () => {
      selectBallot(button.dataset.ballotId);
      switchToSection("review");
    });
  });
}

function renderBallotTable() {
  const tbody = document.getElementById("ballot-table-body");
  const activity = state.currentActivity;
  if (!activity) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">请先选择一个活动。</td></tr>';
    return;
  }

  const filter = document.getElementById("ballot-filter").value;
  const ballots = (activity.ballots || []).filter((ballot) => {
    if (filter === "abnormal") return ballot.is_abnormal;
    if (filter === "normal") return !ballot.is_abnormal;
    if (filter === "void") return isVoidBallot(ballot);
    if (filter === "duplicate") return ballot.is_duplicate;
    return true;
  });

  if (!ballots.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">当前筛选条件下没有投票单。</td></tr>';
    return;
  }

  tbody.innerHTML = ballots
    .map((ballot) => {
      const review = ballot.review || {};
      const abnormalText = ballot.abnormal_types?.length ? ballot.abnormal_types.join(" / ") : "正常";
      const duplicateText = ballot.is_duplicate ? ` · 重复组 ${ballot.duplicate_group_id}` : "";
      const actionButtons = `
        <button class="secondary-btn ballot-detail-btn" data-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">查看详情</button>
        ${
          ballot.is_duplicate
            ? `<button class="secondary-btn danger-lite duplicate-delete-btn" data-delete-duplicate-ballot-id="${escapeHtml(ballot.ballot_id)}" type="button">删除重复票</button>`
            : ""
        }
      `;
      return `
        <tr>
          <td>${escapeHtml(ballot.file_name)}</td>
          <td>${escapeHtml(ballot.upload_id || "-")}</td>
          <td><span class="badge ${ballot.is_abnormal ? "abnormal" : "normal"}">${ballot.is_abnormal ? "异常" : "正常"}</span></td>
          <td>${escapeHtml(abnormalText + duplicateText)}</td>
          <td><span class="badge ${review.reviewed ? "reviewed" : "normal"}">${review.reviewed ? "已复核" : "未复核"}</span></td>
          <td><div class="table-actions">${actionButtons}</div></td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".ballot-detail-btn").forEach((button) => {
    button.addEventListener("click", () => {
      selectBallot(button.dataset.ballotId);
      switchToSection("review");
    });
  });

  tbody.querySelectorAll("[data-delete-duplicate-ballot-id]").forEach((button) => {
    button.addEventListener("click", () => {
      deleteDuplicateBallot(button.dataset.deleteDuplicateBallotId);
    });
  });
}
