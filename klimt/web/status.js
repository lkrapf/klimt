const modelLabel = document.getElementById("model");
const statusLabel = document.getElementById("status");
const contextLabel = document.getElementById("context");

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
    contextLabel.textContent = `ctx ${tokens}`;
    return;
  }

  const pct = ctx.percent === null || ctx.percent === undefined ? "?" : ctx.percent.toFixed(1) + "%";
  contextLabel.textContent = `ctx ${tokens}/${formatTokens(ctx.contextWindow)} (${pct})`;
  const value = Number(ctx.percent || 0);
  if (value > 90) contextLabel.classList.add("danger");
  else if (value > 70) contextLabel.classList.add("warning");
}

export function setSessionLabel(model, name) {
  if (model !== undefined) modelLabel.dataset.model = model || "";
  modelLabel.textContent = [modelLabel.dataset.model, name].filter(Boolean).join(" · ");
}

export function setWorking(on) {
  const working = Boolean(on);
  document.body.classList.toggle("working", working);
  document.body.setAttribute("aria-busy", working ? "true" : "false");
  statusLabel.textContent = working ? "working..." : "";
  return working;
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
