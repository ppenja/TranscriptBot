// ── Helpers ──────────────────────────────────────────────────────────────

function $(sel) { return document.querySelector(sel); }

function toggleSection(id) {
    const el = document.getElementById(id);
    const icon = document.getElementById(id + "-icon");
    if (el.classList.contains("collapsed")) {
        el.classList.remove("collapsed");
        if (icon) icon.textContent = "\u2212";
    } else {
        el.classList.add("collapsed");
        if (icon) icon.textContent = "+";
    }
}

async function api(url, opts) {
    const resp = await fetch(url, opts);
    return resp.json();
}

// ── Stats ────────────────────────────────────────────────────────────────

async function loadStats() {
    const data = await api("/api/stats");
    const el = $("#stats");
    el.innerHTML =
        `<span><span class="stat-num">${data.channels}</span> channels</span>` +
        `<span><span class="stat-num">${data.videos}</span> videos</span>` +
        `<span><span class="stat-num">${data.transcripts}</span> transcripts</span>`;
}

// ── API key ──────────────────────────────────────────────────────────────

async function loadKeyStatus() {
    const data = await api("/api/config");
    const el = $("#key-status");
    if (data.has_key) {
        el.textContent = "API key saved: " + data.key_preview;
        el.style.color = "#22c55e";
    } else {
        el.textContent = "No API key configured yet.";
        el.style.color = "";
    }
}

async function saveApiKey() {
    const key = $("#api-key").value.trim();
    if (!key) return;
    const data = await api("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
    });
    if (data.ok) {
        $("#api-key").value = "";
        loadKeyStatus();
    }
}

// ── Archive ──────────────────────────────────────────────────────────────

let pollTimer = null;

async function startArchive() {
    const url = $("#channel-url").value.trim();
    if (!url) return;

    const btn = $("#archive-btn");
    btn.disabled = true;
    btn.textContent = "Starting...";

    const resp = await api("/api/archive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_url: url }),
    });

    if (resp.error) {
        alert(resp.error);
        btn.disabled = false;
        btn.textContent = "Archive";
        return;
    }

    $("#progress-area").classList.remove("hidden");
    pollTimer = setInterval(() => pollArchive(resp.job_id), 1500);
}

async function pollArchive(jobId) {
    const data = await api("/api/archive/status/" + jobId);

    const bar = $("#progress-bar");
    const txt = $("#progress-text");
    const btn = $("#archive-btn");

    const statusMessages = {
        starting: "Starting...",
        resolving_channel: "Resolving channel...",
        fetching_videos: "Fetching video list...",
        fetching_details: "Fetching video details...",
        downloading_transcripts: `Downloading transcripts: ${data.completed} / ${data.total_videos} (${data.saved || 0} saved, ${data.failed || 0} unavailable)`,
        done: `Done! ${data.saved || 0} transcripts saved, ${data.failed || 0} unavailable, ${data.skipped || 0} already existed (${data.total_videos} videos total).`,
        error: "Error: " + (data.error || "Unknown error"),
    };

    txt.textContent = data.channel_name
        ? data.channel_name + " \u2014 " + (statusMessages[data.status] || data.status)
        : statusMessages[data.status] || data.status;

    if (data.total_videos > 0) {
        bar.style.width = Math.round((data.completed / data.total_videos) * 100) + "%";
    }

    if (data.status === "done" || data.status === "error") {
        clearInterval(pollTimer);
        pollTimer = null;
        btn.disabled = false;
        btn.textContent = "Archive";
        if (data.status === "done") {
            $("#channel-url").value = "";
            loadStats();
            loadChannels();
        }
    }
}

// ── Search ───────────────────────────────────────────────────────────────

async function doSearch() {
    const q = $("#search-query").value.trim();
    if (!q) return;

    const container = $("#search-results");
    container.innerHTML = '<p class="muted"><span class="spinner"></span> Searching...</p>';

    const data = await api("/api/search?q=" + encodeURIComponent(q));

    if (data.error) {
        container.innerHTML = `<p class="muted">${data.error}</p>`;
        return;
    }

    if (data.count === 0) {
        container.innerHTML = '<p class="muted">No results found.</p>';
        return;
    }

    let html = `<p class="result-count">Found <strong>${data.count}</strong> video(s) matching "<em>${escHtml(data.query)}</em>"</p>`;

    for (const r of data.results) {
        const sentClass = "sentiment-" + r.sentiment.overall;
        const sentLabel = r.sentiment.overall.charAt(0).toUpperCase() + r.sentiment.overall.slice(1);
        const sentIcon = r.sentiment.overall === "positive" ? "\u25B2"
                       : r.sentiment.overall === "negative" ? "\u25BC" : "\u25CF";

        let detailsHtml = "";
        if (r.sentiment.details && r.sentiment.details.length > 0) {
            detailsHtml = '<div class="sentiment-details">';
            for (const s of r.sentiment.details) {
                const cls = s.label === "positive" ? "pos" : s.label === "negative" ? "neg" : "neu";
                detailsHtml += `<div class="sentiment-sentence ${cls}">${escHtml(s.sentence)}</div>`;
            }
            detailsHtml += "</div>";
        }

        html += `
        <div class="result-item">
            <div class="result-thumb">
                <a href="https://www.youtube.com/watch?v=${r.video_id}" target="_blank" rel="noopener">
                    <img src="${r.thumbnail_url}" alt="" loading="lazy">
                </a>
            </div>
            <div class="result-info">
                <div class="result-title">
                    <a href="https://www.youtube.com/watch?v=${r.video_id}" target="_blank" rel="noopener">
                        ${escHtml(r.title)}
                    </a>
                </div>
                <div class="result-meta">
                    ${escHtml(r.channel_name)} &middot; ${formatDate(r.published_at)}
                </div>
                <div class="result-snippet">${r.snippet}</div>
                <span class="sentiment-badge ${sentClass}">
                    ${sentIcon} ${sentLabel} (${r.sentiment.polarity.toFixed(2)})
                </span>
                ${detailsHtml}
            </div>
        </div>`;
    }

    container.innerHTML = html;
}

// ── Channels list ────────────────────────────────────────────────────────

async function loadChannels() {
    const data = await api("/api/channels");
    const el = $("#channels-list");
    if (!data.length) {
        el.innerHTML = '<p class="muted">No channels archived yet.</p>';
        return;
    }
    let html = "";
    for (const ch of data) {
        html += `
        <div class="channel-item">
            <span class="channel-name">${escHtml(ch.channel_name)}</span>
            <span class="channel-stats">
                ${ch.video_count} videos &middot; ${ch.transcript_count} transcripts &middot;
                archived ${formatDate(ch.archived_at)}
            </span>
        </div>`;
    }
    el.innerHTML = html;
}

// ── Utilities ────────────────────────────────────────────────────────────

function escHtml(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

// ── Init ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    loadStats();
    loadKeyStatus();
    loadChannels();
});
