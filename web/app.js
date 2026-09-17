const form = document.getElementById("score-form");
const roleSelect = document.getElementById("role-select");
const roleHelp = document.getElementById("role-help");
const fileInput = document.getElementById("file-input");
const fileName = document.getElementById("file-name");
const dropzone = document.getElementById("dropzone");
const formError = document.getElementById("form-error");
const submitBtn = document.getElementById("submit-btn");
const progressPanel = document.getElementById("progress-panel");
const progressMessage = document.getElementById("progress-message");
const progressBar = document.getElementById("progress-bar");
const progressFill = document.getElementById("progress-fill");
const result = document.getElementById("result");

let selectedFile = null;
let pollTimer = null;

function showError(message) {
  formError.hidden = !message;
  formError.textContent = message || "";
}

function setProgress(percent, message) {
  progressPanel.hidden = false;
  progressFill.style.width = `${percent}%`;
  progressBar.setAttribute("aria-valuenow", String(percent));
  progressMessage.textContent = message;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadRoles() {
  const response = await fetch("/api/roles");
  if (!response.ok) {
    throw new Error("Could not load scoring rubrics.");
  }
  const data = await response.json();
  roleSelect.innerHTML = "";
  for (const role of data.roles) {
    const option = document.createElement("option");
    option.value = role.name;
    option.textContent = role.position_title;
    roleSelect.appendChild(option);
  }
  if (data.roles.length) {
    updateRoleHelp(data.roles[0]);
    roleSelect.addEventListener("change", () => {
      const selected = data.roles.find((role) => role.name === roleSelect.value);
      updateRoleHelp(selected);
    });
  }
}

function updateRoleHelp(role) {
  if (!role) {
    roleHelp.textContent = "";
    return;
  }
  const weights = role.categories
    .map((category) => `${category.label} ${category.max}`)
    .join(" · ");
  roleHelp.textContent = weights;
}

function bindDropzone() {
  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-drag");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-drag");
    });
  });
  dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      assignFile(file);
    }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files?.[0]) {
      assignFile(fileInput.files[0]);
    }
  });
}

function assignFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showError("Upload a PDF resume.");
    return;
  }
  selectedFile = file;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
  fileName.textContent = file.name;
  showError("");
}

function scoreColor(score, max) {
  const ratio = max ? score / max : 0;
  if (ratio >= 0.7) return "var(--good)";
  if (ratio >= 0.4) return "var(--warn)";
  return "var(--danger)";
}

function renderReport(job) {
  progressPanel.hidden = true;
  const report = job.result;
  const max = report.category_max || 100;
  const overall = Math.round(report.overall_score * 10) / 10;
  const ratio = Math.max(0, Math.min(1, overall / max));
  const circumference = 2 * Math.PI * 54;
  const offset = circumference * (1 - ratio);
  const github = report.github;

  const categories = (report.categories || [])
    .map((category) => {
      const width = category.max ? (category.score / category.max) * 100 : 0;
      return `
        <article class="category">
          <div class="category-head">
            <strong>${escapeHtml(category.label)}</strong>
            <span>${escapeHtml(category.score)} / ${escapeHtml(category.max)}</span>
          </div>
          <div class="bar" aria-hidden="true"><span style="width:${width}%; background:${scoreColor(category.score, category.max)}"></span></div>
          <p class="evidence">${escapeHtml(category.evidence)}</p>
        </article>
      `;
    })
    .join("");

  const improvements = (report.areas_for_improvement || [])
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");
  const strengths = (report.key_strengths || [])
    .map((item) => `<li>${escapeHtml(item)}</li>`)
    .join("");

  result.hidden = false;
  result.innerHTML = `
    <article class="panel result-card">
      <div class="score-hero">
        <svg class="ring" viewBox="0 0 140 140" role="img" aria-label="Overall score ${overall} out of ${max}">
          <circle cx="70" cy="70" r="54" fill="none" stroke="#1a2438" stroke-width="12" />
          <circle cx="70" cy="70" r="54" fill="none" stroke="${scoreColor(overall, max)}" stroke-width="12"
            stroke-linecap="round" stroke-dasharray="${circumference}" stroke-dashoffset="${offset}"
            transform="rotate(-90 70 70)" />
          <text class="ring-label" x="70" y="74">${escapeHtml(overall)}</text>
          <text class="ring-sub" x="70" y="94">/ ${escapeHtml(max)}</text>
        </svg>
        <div>
          <p class="eyebrow">Report</p>
          <h2 class="candidate">${escapeHtml(report.candidate_name || "Candidate")}</h2>
          <p class="meta">${escapeHtml(report.role?.position_title || "")}</p>
          <p class="disclaimer">${escapeHtml(report.disclaimer || "")}</p>
          ${
            github?.username
              ? `<p class="github-chip">GitHub @${escapeHtml(github.username)} · ${escapeHtml(github.public_repos || 0)} public repos · ${escapeHtml(github.open_source_count || 0)} classified open source</p>`
              : `<p class="github-chip">No GitHub profile was found on the resume.</p>`
          }
        </div>
      </div>
    </article>
    <article class="panel result-card">
      <h2>Where to improve</h2>
      <ul class="improve">${improvements || "<li>No improvement notes were returned.</li>"}</ul>
      ${
        report.deductions
          ? `<p class="evidence">Deductions: -${escapeHtml(report.deductions)}. ${escapeHtml(report.deduction_reasons)}</p>`
          : ""
      }
    </article>
    <div class="split">
      <article class="panel result-card">
        <h2>Category scores</h2>
        <div class="categories">${categories}</div>
        ${
          report.bonus_points
            ? `<p class="evidence">Bonus: +${escapeHtml(report.bonus_points)}. ${escapeHtml(report.bonus_breakdown)}</p>`
            : ""
        }
      </article>
      <article class="panel result-card">
        <h2>Strengths</h2>
        <ul>${strengths || "<li>No strengths were returned.</li>"}</ul>
        <button class="cta again" type="button" id="again-btn">Score another resume</button>
      </article>
    </div>
  `;
  document.getElementById("again-btn")?.addEventListener("click", resetForm);
  result.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });
}

function resetForm() {
  selectedFile = null;
  fileInput.value = "";
  fileName.textContent = "PDF only, up to 8 MB";
  progressPanel.hidden = true;
  result.hidden = true;
  result.innerHTML = "";
  submitBtn.disabled = false;
  showError("");
}

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    throw new Error("Lost the scoring job. Try again.");
  }
  const job = await response.json();
  setProgress(job.percent || 0, job.message || "Working…");
  if (job.status === "done") {
    clearInterval(pollTimer);
    pollTimer = null;
    submitBtn.disabled = false;
    progressPanel.hidden = true;
    renderReport(job);
    return;
  }
  if (job.status === "error") {
    clearInterval(pollTimer);
    pollTimer = null;
    submitBtn.disabled = false;
    showError(job.message || "Scoring failed.");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  const file = selectedFile || fileInput.files?.[0];
  if (!file) {
    showError("Choose a PDF resume first.");
    return;
  }
  const body = new FormData();
  body.append("role", roleSelect.value);
  body.append("file", file);

  submitBtn.disabled = true;
  result.hidden = true;
  setProgress(4, "Uploading resume…");

  try {
    const response = await fetch("/api/evaluate", { method: "POST", body });
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok) {
      let message = "Upload failed.";
      try {
        const data = await response.json();
        const detail = data.detail;
        message =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map((item) => item.msg || item).join(" ")
              : message;
      } catch {
        message = `Upload failed (${response.status}).`;
      }
      throw new Error(message);
    }
    if (contentType.includes("text/event-stream") && response.body) {
      await readScoreStream(response.body);
      return;
    }
    const data = await response.json();
    if (pollTimer) {
      clearInterval(pollTimer);
    }
    pollTimer = setInterval(() => {
      pollJob(data.job_id).catch((error) => {
        clearInterval(pollTimer);
        pollTimer = null;
        submitBtn.disabled = false;
        showError(error.message);
      });
    }, 1500);
    await pollJob(data.job_id);
  } catch (error) {
    submitBtn.disabled = false;
    showError(error.message || "Could not start scoring.");
  }
});

async function readScoreStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .find((entry) => entry.startsWith("data: "));
      if (!line) {
        continue;
      }
      const event = JSON.parse(line.slice(6));
      if (event.status === "running") {
        setProgress(event.percent || 0, event.message || "Working…");
      } else if (event.status === "done") {
        submitBtn.disabled = false;
        progressPanel.hidden = true;
        renderReport({ result: event.result });
        return;
      } else if (event.status === "error") {
        throw new Error(event.message || "Scoring failed.");
      }
    }
  }
  throw new Error("Scoring stopped before a report was returned.");
}

bindDropzone();
loadRoles().catch((error) => showError(error.message));
