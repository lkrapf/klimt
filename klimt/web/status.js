const modelLabel = document.getElementById("model");
const cwdLabel = document.getElementById("cwd");
const statusLabel = document.getElementById("status");
const contextLabel = document.getElementById("context");

let working = false;
let queueCount = 0;

function formatTokens(n) {
  if (n === null || n === undefined) return "?";
  if (n < 1000) return String(n);
  if (n < 10000) return (n / 1000).toFixed(1) + "k";
  if (n < 1000000) return Math.round(n / 1000) + "k";
  if (n < 10000000) return (n / 1000000).toFixed(1) + "M";
  return Math.round(n / 1000000) + "M";
}

export function setContextUsage(ctx) {
  contextLabel.className = "muted context";
  if (!ctx) {
    contextLabel.textContent = "";
    return;
  }

  const tokens = formatTokens(ctx.tokens);
  if (!ctx.contextWindow) {
    contextLabel.textContent = tokens;
    return;
  }

  const pct = ctx.percent === null || ctx.percent === undefined ? "?" : ctx.percent.toFixed(1) + "%";
  contextLabel.textContent = `${tokens}/${formatTokens(ctx.contextWindow)} (${pct})`;
  const value = Number(ctx.percent || 0);
  if (value > 90) contextLabel.classList.add("danger");
  else if (value > 70) contextLabel.classList.add("warning");
}

export function setSessionLabel(model, name) {
  if (model !== undefined) modelLabel.dataset.model = model || "";
  modelLabel.textContent = [modelLabel.dataset.model, name].filter(Boolean).join(" · ");
}

function compactPath(path) {
  const value = path || "";
  const parts = value.split("/");
  if (parts.length <= 3) return value;

  const absolute = parts[0] === "";
  const names = parts.filter(Boolean);
  if (names.length <= 2) return value;

  const compact = names.map((part, i) => (i === names.length - 1 ? part : part[0] || part));
  return (absolute ? "/" : "") + compact.join("/");
}

export function setCwd(path) {
  const value = path || "";
  cwdLabel.textContent = compactPath(value);
  cwdLabel.title = value;
}

function updateStatus() {
  if (working) {
    statusLabel.textContent = queueCount ? `working... ${queueCount} queued` : "working...";
  } else {
    statusLabel.textContent = queueCount ? `${queueCount} queued` : "";
  }
}

export function setWorking(on) {
  working = Boolean(on);
  document.body.classList.toggle("working", working);
  document.body.setAttribute("aria-busy", working ? "true" : "false");
  updateStatus();
  return working;
}

export function setQueueCount(count) {
  queueCount = Math.max(0, Number(count) || 0);
  updateStatus();
}

export function showTabStatus(tab) {
  setSessionLabel(tab?.model, tab?.session);
  setCwd(tab?.cwd);
  setContextUsage(tab?.context);
  setWorking(Boolean(tab?.working));
  setQueueCount(tab?.queue?.length || 0);
}

export function reloadCss() {
  const href = new URL("style.css", window.location.href).href;
  const bust = `v=${Date.now()}`;
  document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
    if (new URL(link.href, window.location.href).pathname.endsWith("/style.css")) {
      link.href = `${href}?${bust}`;
    }
  });
}
