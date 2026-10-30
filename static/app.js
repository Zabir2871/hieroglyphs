const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res;
}

// ---------- feature 11: page counter ----------
$("#btn-count").addEventListener("click", async () => {
  const base_url = $("#base-url").value.trim();
  if (!base_url) return;
  $("#page-count-result").textContent = "Counting pages\u2026";
  try {
    const res = await api("/api/count_pages", { method: "POST", body: JSON.stringify({ base_url }) });
    const data = await res.json();
    $("#page-count-result").textContent = `${data.page_count.toLocaleString()} article pages found at ${data.base_url}`;
  } catch (e) {
    $("#page-count-result").textContent = "Could not reach that wiki's API. Check the URL and try again.";
  }
});

// ---------- crawl start + progress polling (features 12, 13) ----------
let pollTimer = null;
let currentJobId = null;

$("#btn-start").addEventListener("click", async () => {
  const base_url = $("#base-url").value.trim();
  const dict_name = $("#dict-name").value.trim() || "My Fandom Dictionary";
  const apply_tense_fix = $("#tense-fix").checked;
  if (!base_url) { alert("Enter a wiki base URL first."); return; }

  $("#progress-wrap").classList.remove("hidden");
  $("#progress-status").textContent = "Starting\u2026";
  $("#progress-retry").classList.add("hidden");
  $("#btn-start").disabled = true;
  $("#btn-pause").classList.remove("hidden");
  $("#btn-resume").classList.add("hidden");
  $("#btn-stop").classList.remove("hidden");
  $("#btn-stop").textContent = "\u23f9 Stop & keep progress";

  const res = await api("/api/crawl/start", {
    method: "POST",
    body: JSON.stringify({ base_url, dict_name, apply_tense_fix }),
  });
  const { job_id } = await res.json();
  currentJobId = job_id;

  pollTimer = setInterval(() => pollCrawl(job_id), 900);
});

$("#btn-pause").addEventListener("click", async () => {
  if (!currentJobId) return;
  await api(`/api/crawl/pause/${currentJobId}`, { method: "POST" });
});

$("#btn-resume").addEventListener("click", async () => {
  if (!currentJobId) return;
  await api(`/api/crawl/resume/${currentJobId}`, { method: "POST" });
  if (!pollTimer) pollTimer = setInterval(() => pollCrawl(currentJobId), 900);
});

$("#btn-stop").addEventListener("click", async () => {
  if (!currentJobId) return;
  if (!confirm("Stop the crawl now? Everything found so far will still be available to keep.")) return;
  await api(`/api/crawl/stop/${currentJobId}`, { method: "POST" });
});

async function pollCrawl(job_id) {
  const res = await api(`/api/crawl/status/${job_id}`);
  const s = await res.json();

  $("#progress-fill").style.width = `${s.percent}%`;
  $("#progress-text").textContent =
    `${s.processed_pages} / ${s.total_pages} pages processed \u2014 ${s.entries_created} entries, ${s.redirect_pages} aliases found` +
    (s.current_title ? ` (current: ${s.current_title})` : "");

  if (s.retry_info) {
    $("#progress-retry").textContent = s.retry_info;
    $("#progress-retry").classList.remove("hidden");
  } else {
    $("#progress-retry").classList.add("hidden");
  }

  $("#progress-status").textContent =
    { counting: "Counting pages\u2026", crawling: "Extracting definitions and aliases\u2026",
      paused: "Paused \u2014 resume any time, nothing is lost.",
      done: "Done!", stopped: "Stopped \u2014 you can still keep everything found so far.",
      error: s.can_resume
        ? `Hit a snag: ${s.error} \u2014 your progress is saved, click Resume to continue from here.`
        : `Error: ${s.error}` }[s.status] || s.status;

  if (s.status === "paused") {
    $("#btn-pause").classList.add("hidden");
    $("#btn-resume").classList.remove("hidden");
  } else if (s.status === "crawling" || s.status === "counting") {
    $("#btn-pause").classList.remove("hidden");
    $("#btn-resume").classList.add("hidden");
  }

  if (s.status === "done" || s.status === "stopped") {
    clearInterval(pollTimer);
    pollTimer = null;
    $("#btn-start").disabled = false;
    $("#btn-pause").classList.add("hidden");
    $("#btn-resume").classList.add("hidden");
    $("#btn-stop").classList.add("hidden");
    await api(`/api/crawl/commit/${job_id}`, { method: "POST" });
    await refreshProject();
    await refreshEntries("");
  } else if (s.status === "error") {
    clearInterval(pollTimer);
    pollTimer = null;
    $("#btn-start").disabled = false;
    $("#btn-pause").classList.add("hidden");
    $("#btn-stop").classList.add("hidden");
    if (s.can_resume) {
      $("#btn-resume").classList.remove("hidden");
      $("#btn-resume").textContent = "\u25b6 Resume from here";
    }
  }
}

// ---------- project panel ----------
async function refreshProject() {
  const res = await api("/api/project");
  const p = await res.json();
  $("#project-name").textContent = p.name;
  $("#project-stats").textContent =
    `${p.entry_count} entries, ${p.alias_count} aliases` + (p.base_url ? ` \u2014 sourced from ${p.base_url}` : "");
}

$("#btn-rename").addEventListener("click", async () => {
  const name = prompt("New dictionary name:");
  if (!name) return;
  await api("/api/project/rename", { method: "POST", body: JSON.stringify({ name }) });
  refreshProject();
});

$("#btn-new").addEventListener("click", async () => {
  if (!confirm("Start a new blank project? Unsaved changes to the current one will be lost from memory (export first if needed).")) return;
  const name = prompt("Name for the new dictionary:", "Untitled Dictionary") || "Untitled Dictionary";
  await api("/api/project/new", { method: "POST", body: JSON.stringify({ name }) });
  await refreshProject();
  await refreshEntries("");
});

// Quality-of-life: jump to a random entry for browsing/discovery.
$("#btn-random").addEventListener("click", async () => {
  try {
    const res = await api("/api/project/random");
    const e = await res.json();
    $("#search-box").value = e.headword;
    await showQuickView(e);
    await refreshEntries(e.headword);
  } catch (err) {
    alert("No entries yet - build or import a dictionary first.");
  }
});

// ---------- feature 9: import ----------
$("#btn-import").addEventListener("click", async () => {
  const fileInput = $("#import-file");
  if (!fileInput.files.length) { alert("Choose a file first."); return; }
  const mode = $("#import-mode").value;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  $("#import-result").textContent = "Importing\u2026";
  try {
    const res = await api(`/api/project/import?mode=${mode}`, { method: "POST", body: fd });
    const data = await res.json();
    $("#import-result").textContent = `Imported ${data.imported_count} entries. Project now has ${data.total_entries} entries.`;
    await refreshProject();
    await refreshEntries("");
  } catch (e) {
    $("#import-result").textContent = "Import failed: " + e.message;
  }
});

// ---------- export ----------
document.querySelectorAll(".export-row button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const url = btn.dataset.fmt === "all" ? "/api/project/export_all" : `/api/project/export/${btn.dataset.fmt}`;
    window.location.href = url;
  });
});

// ---------- entries: search + list ----------
async function refreshEntries(q) {
  const res = await api(`/api/project/entries?q=${encodeURIComponent(q || "")}`);
  const entries = await res.json();
  const list = $("#entry-list");
  list.innerHTML = "";
  if (!entries.length) {
    list.innerHTML = `<p class="hint">No entries yet. Build a dictionary above, or import one.</p>`;
    return;
  }
  for (const e of entries) {
    const card = document.createElement("div");
    card.className = "entry-card";
    if (e.redirect_to) {
      // Only ever hit for a broken/unresolved redirect (its target isn't
      // in the dictionary at all) - a normal alias like "Roque" is
      // already resolved server-side to the real "Roque au Fabii" entry
      // before it gets here, so this branch is a rare fallback, not the
      // everyday path.
      card.innerHTML = `<span class="headword">${escapeHtml(e.headword)}</span>
        <span class="redirect">\u2192 ${escapeHtml(e.redirect_to)}</span>`;
    } else {
      const snippet = e.body.length > 160 ? e.body.slice(0, 160) + "\u2026" : e.body;
      card.innerHTML = `<span class="headword">${escapeHtml(e.headword)}</span>
        ${e.aliases.length ? `<span class="aliases">aka ${e.aliases.map(escapeHtml).join(", ")}</span>` : ""}
        <div class="snippet">${escapeHtml(snippet)}</div>`;
    }
    card.addEventListener("click", () => openEditor(e));
    list.appendChild(card);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Render one entry's full text into the quick-view panel, with its
// "See More" siblings as tappable links that jump straight to that
// entry's own definition (rather than dumping plain text you can't act
// on).
async function showQuickView(e) {
  const box = $("#quick-view");
  const seeMoreHtml = (e.see_more && e.see_more.length)
    ? `<div class="see-more">
         <div class="see-more-title">See More:</div>
         ${e.see_more.map((h) => `<span class="see-more-link" data-word="${escapeHtml(h)}">${escapeHtml(h)}</span>`).join("")}
       </div>`
    : "";
  box.innerHTML = `
    <span class="headword">${escapeHtml(e.headword)}</span>
    ${e.aliases && e.aliases.length ? `<span class="aliases">aka ${e.aliases.map(escapeHtml).join(", ")}</span>` : ""}
    <div class="body">${escapeHtml(e.body || "")}</div>
    ${seeMoreHtml}
    <div class="quick-view-actions">
      <span class="edit-link">Edit this entry</span>
      <span class="copy-link">Copy definition</span>
    </div>
    <div class="query-line">Query: ${escapeHtml(e.query || e.headword)}</div>
  `;
  box.classList.remove("hidden");
  box.querySelector(".edit-link").addEventListener("click", () => openEditor(e));
  box.querySelector(".copy-link").addEventListener("click", () => {
    navigator.clipboard?.writeText(`${e.headword}\n\n${e.body}`);
  });
  box.querySelectorAll(".see-more-link").forEach((el) => {
    el.addEventListener("click", () => {
      $("#search-box").value = el.dataset.word;
      runSearch(el.dataset.word);
      box.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

// A search that exactly names a headword or alias (e.g. "Roque",
// "Goliath") jumps straight to the resolved entry's full text, the same
// way Runik does it - no "redirected to" message, it just lands you on
// the real definition.
async function jumpToWord(q) {
  const box = $("#quick-view");
  if (!q) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  try {
    const res = await fetch(`/api/project/lookup?q=${encodeURIComponent(q)}`);
    if (!res.ok) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    const e = await res.json();
    await showQuickView(e);
  } catch (err) {
    box.classList.add("hidden");
    box.innerHTML = "";
  }
}

let searchDebounce = null;
async function runSearch(q) {
  await jumpToWord(q);
  await refreshEntries(q);
}

$("#btn-search").addEventListener("click", () => runSearch($("#search-box").value.trim()));
$("#search-box").addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(e.target.value.trim()); });

// Quality-of-life: live search-as-you-type (debounced) instead of
// requiring Enter/click every time.
$("#search-box").addEventListener("input", (e) => {
  clearTimeout(searchDebounce);
  const value = e.target.value.trim();
  searchDebounce = setTimeout(() => runSearch(value), 300);
});

// Quality-of-life: press "/" anywhere to jump into the search box.
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("#search-box") && document.activeElement.tagName !== "TEXTAREA") {
    e.preventDefault();
    $("#search-box").focus();
  }
  // Quality-of-life: Escape closes the entry editor modal.
  if (e.key === "Escape" && !$("#entry-editor").classList.contains("hidden")) {
    $("#entry-editor").classList.add("hidden");
  }
});

// Quality-of-life: remember the last wiki URL and dictionary name so
// returning to the app doesn't mean retyping them.
const LAST_URL_KEY = "hieroglyphs:last_base_url";
const LAST_NAME_KEY = "hieroglyphs:last_dict_name";
try {
  const savedUrl = localStorage.getItem(LAST_URL_KEY);
  const savedName = localStorage.getItem(LAST_NAME_KEY);
  if (savedUrl) $("#base-url").value = savedUrl;
  if (savedName) $("#dict-name").value = savedName;
} catch (err) { /* localStorage unavailable - ignore */ }

$("#btn-start").addEventListener("click", () => {
  try {
    localStorage.setItem(LAST_URL_KEY, $("#base-url").value.trim());
    localStorage.setItem(LAST_NAME_KEY, $("#dict-name").value.trim());
  } catch (err) { /* ignore */ }
});

// ---------- entry editor modal ----------
function openEditor(e) {
  $("#edit-id").value = e.id || "";
  $("#edit-headword").value = e.headword || "";
  $("#edit-aliases").value = (e.aliases || []).join(", ");
  $("#edit-redirect").value = e.redirect_to || "";
  $("#edit-body").value = e.body || "";
  $("#entry-editor").classList.remove("hidden");
}

$("#btn-add-entry").addEventListener("click", () => openEditor({}));
$("#btn-close-editor").addEventListener("click", () => $("#entry-editor").classList.add("hidden"));

$("#btn-save-entry").addEventListener("click", async () => {
  const payload = {
    id: $("#edit-id").value || undefined,
    headword: $("#edit-headword").value.trim(),
    aliases: $("#edit-aliases").value.split(",").map((s) => s.trim()).filter(Boolean),
    redirect_to: $("#edit-redirect").value.trim() || null,
    body: $("#edit-body").value,
  };
  if (!payload.headword) { alert("Headword is required."); return; }
  await api("/api/project/entry", { method: "POST", body: JSON.stringify(payload) });
  $("#entry-editor").classList.add("hidden");
  await refreshProject();
  await refreshEntries($("#search-box").value.trim());
});

$("#btn-delete-entry").addEventListener("click", async () => {
  const id = $("#edit-id").value;
  if (!id) { $("#entry-editor").classList.add("hidden"); return; }
  if (!confirm("Delete this entry?")) return;
  await api(`/api/project/entry/${id}`, { method: "DELETE" });
  $("#entry-editor").classList.add("hidden");
  await refreshProject();
  await refreshEntries($("#search-box").value.trim());
});

// ---------- init ----------
refreshProject();
refreshEntries("");
