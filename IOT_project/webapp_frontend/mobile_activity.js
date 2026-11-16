const mobileState = {
  activityId: null,
  activity: null,
  files: [],
};

document.addEventListener("DOMContentLoaded", () => {
  mobileState.activityId = parseActivityId();
  bindMobileUploadForm();
  loadMobileActivity().catch(showMobileError);
});

function parseActivityId() {
  const match = window.location.pathname.match(/\/m\/activities\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function bindMobileUploadForm() {
  const fileInput = document.getElementById("mobile-file-input");
  const fileList = document.getElementById("mobile-file-list");
  const form = document.getElementById("mobile-upload-form");
  const status = document.getElementById("mobile-status");

  const renderFiles = () => {
    fileList.innerHTML = "";
    mobileState.files.forEach((file) => {
      const item = document.createElement("div");
      item.className = "file-item";
      item.textContent = `${file.name} · ${formatFileSize(file.size)}`;
      fileList.appendChild(item);
    });
  };

  fileInput.addEventListener("change", () => {
    mobileState.files = Array.from(fileInput.files || []).filter(isSupportedImage);
    renderFiles();
    if (fileInput.files?.length && !mobileState.files.length) {
      status.textContent = "只支持图片格式文件。";
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!mobileState.activityId) {
      status.textContent = "未找到活动编号。";
      return;
    }
    if (!mobileState.files.length) {
      status.textContent = "请先选择图片。";
      return;
    }

    const formData = new FormData();
    formData.append("async_mode", "true");
    mobileState.files.forEach((file) => formData.append("files", file));
    status.textContent = "正在上传并识别，请稍候...";

    try {
      const response = await fetch(`/api/activities/${encodeURIComponent(mobileState.activityId)}/upload`, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      let payload = await response.json();
      if (payload.task_id) {
        payload = await waitForMobileTask(payload.task_id, status);
      }

      mobileState.activity = payload;
      mobileState.files = [];
      fileInput.value = "";
      renderFiles();
      status.textContent = `识别完成，共累计 ${payload.ballot_count} 张投票单。`;
      renderMobileActivity();
    } catch (error) {
      status.textContent = `上传失败：${error.message}`;
    }
  });
}

async function loadMobileActivity() {
  const response = await fetch(`/api/activities/${encodeURIComponent(mobileState.activityId)}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  mobileState.activity = await response.json();
  renderMobileActivity();
}

async function waitForMobileTask(taskId, statusElement) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
    if (!response.ok) {
      throw new Error(await readErrorMessage(response));
    }
    const task = await response.json();
    statusElement.textContent = task.message || "识别中...";
    if (task.status === "completed") {
      return task.result;
    }
    if (task.status === "failed") {
      throw new Error(task.error || "识别任务失败。");
    }
  }
}

function renderMobileActivity() {
  const activity = mobileState.activity;
  if (!activity) {
    return;
  }

  document.getElementById("activity-title").textContent = activity.activity_name || "投票活动";
  document.getElementById("activity-subtitle").textContent = `累计上传 ${activity.upload_count || 0} 次，累计识别 ${activity.ballot_count || 0} 张投票单。`;

  document.getElementById("mobile-summary").innerHTML = `
    <div class="summary-chip"><strong>${activity.ballot_count || 0}</strong><br /><span>投票单总数</span></div>
    <div class="summary-chip"><strong>${activity.abnormal_ballot_count || 0}</strong><br /><span>异常投票单</span></div>
    <div class="summary-chip"><strong>${activity.upload_count || 0}</strong><br /><span>上传批次</span></div>
    <div class="summary-chip"><strong>${Object.keys(activity.candidate_totals || {}).length}</strong><br /><span>候选人条目</span></div>
  `;

  document.getElementById("mobile-option-chart").innerHTML = buildBarChart(activity.option_totals, "当前还没有识别结果。");

  const uploads = activity.uploads || [];
  document.getElementById("mobile-upload-history").innerHTML = uploads.length
    ? uploads
        .map(
          (upload) => `
            <div class="history-item">
              <strong>${escapeHtml(upload.upload_id)}</strong>
              <span>${escapeHtml(upload.created_at || "")}</span>
              <span>${upload.ballot_count || 0} 张投票单 · ${upload.abnormal_ballot_count || 0} 张异常</span>
            </div>
          `,
        )
        .join("")
    : '<div class="empty-tip">暂无上传记录</div>';
}

function buildBarChart(data, emptyText) {
  const entries = Object.entries(data || {}).filter(([, value]) => Number(value) > 0);
  if (!entries.length) {
    return `<div class="empty-tip">${escapeHtml(emptyText || "暂无数据")}</div>`;
  }
  const maxValue = Math.max(...entries.map(([, value]) => Number(value)), 1);
  return entries
    .map(([label, value]) => {
      const width = Math.max(5, Math.round((Number(value) / maxValue) * 100));
      return `
        <div class="bar-row">
          <div class="bar-meta"><span>${escapeHtml(label)}</span><strong>${value}</strong></div>
          <div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div>
        </div>
      `;
    })
    .join("");
}

function isSupportedImage(file) {
  const name = file.name.toLowerCase();
  return [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"].some((suffix) => name.endsWith(suffix));
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

async function readErrorMessage(response) {
  try {
    const payload = await response.json();
    return payload.detail || "请求失败";
  } catch (_) {
    const text = await response.text();
    return text || "请求失败";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function showMobileError(error) {
  document.getElementById("activity-title").textContent = "页面加载失败";
  document.getElementById("activity-subtitle").textContent = error.message;
}
