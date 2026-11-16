async function apiCreateActivity(payload) {
  const response = await fetch("/api/activities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiCorrectBallot(activityId, ballotId, payload) {
  const response = await fetch(
    `/api/activities/${encodeURIComponent(activityId)}/ballots/${encodeURIComponent(ballotId)}/correction`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiDeleteActivities(activityIds) {
  const response = await fetch("/api/activities/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ activity_ids: activityIds }),
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiDeleteBallot(activityId, ballotId) {
  const response = await fetch(`/api/activities/${encodeURIComponent(activityId)}/ballots/${encodeURIComponent(ballotId)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiGetOverview() {
  const response = await fetch("/api/overview");
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiGetActivity(activityId) {
  const response = await fetch(`/api/activities/${encodeURIComponent(activityId)}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiActivateActivity(activityId) {
  const response = await fetch(`/api/activities/${encodeURIComponent(activityId)}/activate`, { method: "POST" });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiResetCurrentActivity() {
  const response = await fetch("/api/current/reset", { method: "POST" });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiUploadToActivity(activityId, formData) {
  const response = await fetch(`/api/activities/${encodeURIComponent(activityId)}/upload`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiGetTask(taskId) {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
}

async function apiReviewBallot(activityId, ballotId, payload) {
  const response = await fetch(
    `/api/activities/${encodeURIComponent(activityId)}/ballots/${encodeURIComponent(ballotId)}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  return response.json();
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
