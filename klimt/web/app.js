"use strict";

const transcript = document.getElementById("transcript");
const input = document.getElementById("input");
const modelLabel = document.getElementById("model");
const statusLabel = document.getElementById("status");
const contextLabel = document.getElementById("context");

marked.setOptions({ gfm: true, breaks: false });

const SANITIZE_OPTS = {
  ADD_TAGS: ["foreignObject"],
  ADD_ATTR: ["target"],
};

const klimt = { pending: null, current: null, suppressUntilDone: false };
window.klimt = klimt;

let inputHistory = [];
let historyPos = null;
let historyDraft = "";

// ---- rendering -----------------------------------------------------------


function formatTokens(n) {
  if (n === null || n === undefined) return "?";
  if (n < 1000) return String(n);
  if (n < 10000) return (n / 1000).toFixed(1) + "k";
  if (n < 1000000) return Math.round(n / 1000) + "k";
  if (n < 10000000) return (n / 1000000).toFixed(1) + "M";
  return Math.round(n / 1000000) + "M";
}

function setContextUsage(ctx) {
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

function renderMarkdown(src) {
  return DOMPurify.sanitize(marked.parse(src ?? ""), SANITIZE_OPTS);
}

// Close any unterminated ``` block so partial markdown renders cleanly.
function autoCloseFences(s) {
  const fences = (s.match(/^```/gm) || []).length;
  return fences % 2 ? s + "\n```" : s;
}

function hasOpenFence(s) {
  return ((s.match(/^```/gm) || []).length % 2) !== 0;
}

function countClosedMermaidBlocks(s) {
  return (s.match(/(^|\n)```mermaid[\s\S]*?\n```/gi) || []).length;
}

function countUnescaped(s, token) {
  let count = 0;
  let pos = 0;
  while (true) {
    const i = s.indexOf(token, pos);
    if (i < 0) return count;
    let slashes = 0;
    for (let j = i - 1; j >= 0 && s[j] === "\\"; j--) slashes += 1;
    if (slashes % 2 === 0) count += 1;
    pos = i + token.length;
  }
}

function mathBlockCount(s) {
  const displayDollars = Math.floor(countUnescaped(s, "$$") / 2);
  const bracketBlocks = (s.match(/\\\[[\s\S]*?\\\]/g) || []).length;
  const parenBlocks = (s.match(/\\\([\s\S]*?\\\)/g) || []).length;
  return displayDollars + bracketBlocks + parenBlocks;
}

function streamEnhanceKey(raw) {
  const math = mathBlockCount(raw);
  const mermaid = hasOpenFence(raw) ? 0 : countClosedMermaidBlocks(raw);
  const parts = [];
  if (math) parts.push(`math:${math}`);
  if (mermaid) parts.push(`mermaid:${mermaid}`);
  return parts.join("+");
}

let mermaidCounter = 0;


function waitForGlobal(name, timeoutMs = 5000) {
  if (window[name]) return Promise.resolve(window[name]);

  return new Promise((resolve) => {
    const start = Date.now();
    const timer = setInterval(() => {
      if (window[name]) {
        clearInterval(timer);
        resolve(window[name]);
      } else if (Date.now() - start >= timeoutMs) {
        clearInterval(timer);
        resolve(null);
      }
    }, 50);
  });
}

function renderMath(root, renderMathInElement) {
  renderMathInElement(root, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$",  right: "$",  display: false },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
    processEscapes: true,
  });
}

async function enhance(root) {
  // Make broken/blocked images visible instead of failing as empty space.
  // Set this before the node is appended where possible, so WebKit sends less
  // identifying referrer data to hosts that dislike hotlinking.
  root.querySelectorAll("img").forEach((img) => {
    img.referrerPolicy = "no-referrer";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => {
      const url = img.getAttribute("src") || "";
      const a = document.createElement("a");
      a.href = url;
      a.textContent = `image failed to load: ${url}`;
      a.target = "_blank";
      a.rel = "noreferrer";
      img.replaceWith(a);
    }, { once: true });
  });

  // 0. Render TeX before other async work. If the CDN is slow, wait briefly;
  // otherwise plain TeX remains visible instead of blocking the message.
  const renderMathInElement = await waitForGlobal("renderMathInElement");
  if (renderMathInElement) {
    try { renderMath(root, renderMathInElement); }
    catch (e) { console.warn("KaTeX render failed", e); }
  } else if ((root.textContent || "").includes("$")) {
    console.warn("KaTeX render skipped: window.renderMathInElement was not loaded");
  }

  // 1. Syntax-highlight normal fenced code blocks. Mermaid is handled below.
  if (window.hljs) {
    root.querySelectorAll("pre > code:not(.language-mermaid)").forEach((code) => {
      try { window.hljs.highlightElement(code); }
      catch (e) { console.warn("highlight.js failed", e); }
    });
  } else {
    console.warn("highlight.js unavailable; syntax highlighting skipped");
  }

  // 2. Convert ```mermaid blocks into <div class="mermaid">.
  root.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.id = `mmd-${++mermaidCounter}`;
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });

  const mmd = root.querySelectorAll(".mermaid");
  if (mmd.length) {
    const mermaid = await waitForGlobal("mermaid");
    if (mermaid) {
      try { await mermaid.run({ nodes: mmd }); }
      catch (e) { console.warn("mermaid render failed", e); }
    } else {
      console.warn("mermaid render skipped: window.mermaid was not loaded");
    }
  }

}

function reloadCss() {
  const href = new URL("style.css", window.location.href).href;
  const bust = `v=${Date.now()}`;
  document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
    if (new URL(link.href, window.location.href).pathname.endsWith("/style.css")) {
      link.href = `${href}?${bust}`;
    }
  });
}

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
}


function escapeMd(text) {
  return String(text ?? "").replace(/[\\`*_{}\[\]()#+.!|>]/g, "\\$&");
}

function addBannerLogo() {
  const div = document.createElement("div");
  div.className = "startup-mark";
  div.setAttribute("aria-label", "klimt");
  div.textContent = "[|<] klimt";
  transcript.appendChild(div);
  scrollToBottom();
}

function addStartup(info) {
  addBannerLogo();

  const lines = [
    `version ${escapeMd(info.version || "unknown")}`,
    "",
    "## Available skills",
  ];

  const skills = Array.isArray(info.skills) ? info.skills : [];
  if (skills.length) {
    lines.push("", "| skill | description |", "|---|---|");
    for (const s of skills) {
      const name = escapeMd(s.name || "unnamed");
      const desc = escapeMd(s.description || "(no description)");
      lines.push("| `/" + name + "` | " + desc + " |");
    }
  } else {
    lines.push("", "_none_");
  }

  lines.push("", "## Available tools");
  const tools = Array.isArray(info.available_tools) ? info.available_tools : (Array.isArray(info.tools) ? info.tools : []);
  if (tools.length) {
    lines.push("", "| tool | description |", "|---|---|");
    for (const t of tools) {
      const name = escapeMd(t.name || "unnamed");
      const desc = escapeMd(t.description || "(no description)");
      lines.push("| `" + name + "` | " + desc + " |");
    }
  } else {
    lines.push("", "_none_");
  }

  lines.push("", "type `/help` for more information");
  addMessage("system", lines.join("\n"));
}


// ---- message constructors ------------------------------------------------

function addMessage(role, text, { markdown = true } = {}) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = role;

  const body = document.createElement("div");
  body.className = "body";
  if (markdown) {
    body.innerHTML = renderMarkdown(text);
    enhance(body);
  } else {
    body.classList.add("plain");
    body.textContent = text;
  }

  div.appendChild(r);
  div.appendChild(body);
  transcript.appendChild(div);
  scrollToBottom();
  return div;
}

function addPending() {
  const div = addMessage("assistant", "", { markdown: false });
  div.classList.add("pending");
  const body = div.querySelector(".body");
  body.innerHTML = '<span class="thinking">thinking</span>';
  scrollToBottom();
  requestAnimationFrame(scrollToBottom);
  return div;
}

function summarizeArgs(name, args) {
  if (name === "bash")  return "$ " + (args.command ?? "");
  if (name === "read")  return "read " + (args.path ?? "");
  if (name === "write") return "write " + (args.path ?? "") +
                              " (" + (args.content?.length ?? 0) + " bytes)";
  try { return JSON.stringify(args); } catch (_) { return String(args); }
}

function addTool(name, args, result) {
  const div = document.createElement("div");
  div.className = "msg tool";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "tool \u00b7 " + name;

  const body = document.createElement("div");
  body.className = "body";

  const call = document.createElement("pre");
  call.className = "tool-call";
  if (name === "bash") {
    const code = document.createElement("code");
    code.className = "language-bash";
    code.textContent = args.command ?? "";
    call.appendChild(code);
    if (window.hljs) {
      try { window.hljs.highlightElement(code); }
      catch (e) { console.warn("highlight.js failed", e); }
    }
  } else {
    call.textContent = summarizeArgs(name, args);
  }

  const out = document.createElement("pre");
  out.className = "tool-out";
  out.textContent = result;

  body.appendChild(call);
  body.appendChild(out);
  div.appendChild(r);
  div.appendChild(body);
  transcript.appendChild(div);
  scrollToBottom();
  return div;
}

// ---- streaming -----------------------------------------------------------

function startStreaming() {
  const div = document.createElement("div");
  div.className = "msg assistant streaming";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "assistant";

  const body = document.createElement("div");
  body.className = "body";

  div.appendChild(r);
  div.appendChild(body);
  transcript.appendChild(div);
  scrollToBottom();
  return {
    div,
    body,
    raw: "",
    pending: false,
    raf: null,
    done: false,
    enhanceTimer: null,
    enhancedKey: "",
    renderSerial: 0,
    enhancedSerial: 0,
  };
}

function scheduleStreamEnhance(h) {
  const key = streamEnhanceKey(h.raw);
  if (!key) return;
  if (key === h.enhancedKey && h.enhancedSerial === h.renderSerial) return;

  if (h.enhanceTimer !== null) clearTimeout(h.enhanceTimer);
  h.enhanceTimer = setTimeout(() => {
    h.enhanceTimer = null;
    if (h.done) return;

    // The next token re-renders from raw markdown, so enhancement is intentionally
    // opportunistic and repeatable. This keeps completed math/mermaid visible
    // during streaming without trying to mutate partial syntax in place.
    enhance(h.body);
    h.enhancedKey = key;
    h.enhancedSerial = h.renderSerial;
  }, 120);
}

function appendDelta(h, txt) {
  if (h.done) return;
  h.raw += txt;
  if (h.pending) return;
  h.pending = true;
  h.raf = requestAnimationFrame(() => {
    h.raf = null;
    h.pending = false;
    if (h.done) return;
    h.body.innerHTML = DOMPurify.sanitize(
      marked.parse(autoCloseFences(h.raw)),
      SANITIZE_OPTS,
    );
    h.renderSerial += 1;
    scheduleStreamEnhance(h);
    scrollToBottom();
  });
}

function finalizeStreaming(h) {
  h.done = true;
  h.pending = false;
  if (h.raf !== null) {
    cancelAnimationFrame(h.raf);
    h.raf = null;
  }
  if (h.enhanceTimer !== null) {
    clearTimeout(h.enhanceTimer);
    h.enhanceTimer = null;
  }
  h.body.innerHTML = renderMarkdown(h.raw);
  enhance(h.body);
  h.div.classList.remove("streaming");
  scrollToBottom();
}

// ---- bridge event handler (called from Python via evaluate_js) ----------

klimt.handleEvent = function(ev) {
  if (klimt.suppressUntilDone && ev.type !== "done") return;
  if (klimt.pending) { klimt.pending.remove(); klimt.pending = null; }

  switch (ev.type) {
    case "text_start":
      klimt.current = startStreaming();
      break;
    case "text_delta":
      if (!klimt.current) klimt.current = startStreaming();
      appendDelta(klimt.current, ev.content);
      break;
    case "text_end":
      if (klimt.current) {
        finalizeStreaming(klimt.current);
        klimt.current = null;
      }
      break;
    case "text":
      addMessage("assistant", ev.content || "");
      break;
    case "message": {
      const role = ev.role || "assistant";
      addMessage(role, ev.content || "", { markdown: role !== "user" });
      break;
    }
    case "clear":
      transcript.innerHTML = "";
      klimt.current = null;
      klimt.pending = null;
      break;
    case "input_history":
      setInputHistory(ev.items);
      break;
    case "session":
      if (ev.model !== undefined) modelLabel.dataset.model = ev.model || "";
      modelLabel.textContent = [modelLabel.dataset.model, ev.name].filter(Boolean).join(" · ");
      break;
    case "context":
      setContextUsage(ev);
      break;
    case "tool":
      // Close any open streaming bubble before showing a tool box.
      if (klimt.current) {
        finalizeStreaming(klimt.current);
        klimt.current = null;
      }
      addTool(ev.name, ev.args, ev.result);
      break;
    case "error":
      addMessage("error", "**Error:** " + ev.message);
      break;
    case "reload_css":
      reloadCss();
      break;
    case "done":
      klimt.suppressUntilDone = false;
      finishWork();
      break;
  }
};

// ---- input wiring --------------------------------------------------------

function setWorking(on) {
  working = Boolean(on);
  document.body.classList.toggle("working", working);
  document.body.setAttribute("aria-busy", working ? "true" : "false");
  statusLabel.textContent = working ? "working..." : "";
}

function finishWork() {
  if (klimt.pending) { klimt.pending.remove(); klimt.pending = null; }
  if (klimt.current) {
    finalizeStreaming(klimt.current);
    klimt.current = null;
  }
  setWorking(false);
  input.focus();
}

function resizeInput() {
  input.style.height = "auto";
  const max = parseInt(getComputedStyle(input).maxHeight, 10) || Infinity;
  const h = Math.min(input.scrollHeight, max);
  input.style.height = h + "px";
  input.style.overflowY = input.scrollHeight > max ? "auto" : "hidden";
}

function setInputValue(text) {
  input.value = text;
  resizeInput();
  input.selectionStart = input.selectionEnd = input.value.length;
}

function rememberInput(text) {
  if (!text) return;
  if (inputHistory[inputHistory.length - 1] !== text) inputHistory.push(text);
  historyPos = null;
  historyDraft = "";
}

function setInputHistory(items) {
  inputHistory = Array.isArray(items) ? items.slice() : [];
  historyPos = null;
  historyDraft = "";
}

function navigateHistory(delta) {
  if (!inputHistory.length) return false;

  if (historyPos === null) {
    historyPos = inputHistory.length;
    historyDraft = input.value;
  }

  const next = Math.max(0, Math.min(inputHistory.length, historyPos + delta));
  if (next === historyPos) return true;

  historyPos = next;
  setInputValue(historyPos === inputHistory.length ? historyDraft : inputHistory[historyPos]);
  return true;
}


let working = false;

function interruptWork() {
  if (!working) return;

  // Do not await the bridge. On some pywebview backends API calls are serialized
  // behind the active send call; waiting here is exactly what makes Esc feel dead.
  klimt.suppressUntilDone = true;
  finishWork();
  addMessage("system", "_interrupted_");

  window.pywebview.api.interrupt().catch((e) => {
    addMessage("error", "**Bridge error:** " + (e?.message || e));
  });
}

async function send() {
  const text = input.value.trim();
  if (!text || working) return;

  rememberInput(text);
  setInputValue("");
  setWorking(true);
  addMessage("user", text, { markdown: false });
  klimt.pending = addPending();

  try {
    const res = await window.pywebview.api.send(text);
    if (!res.ok) {
      addMessage("error", "**Error:** " + res.error);
      finishWork();
    }
  } catch (e) {
    addMessage("error", "**Bridge error:** " + (e?.message || e));
    finishWork();
  }
}


input.addEventListener("input", () => {
  historyPos = null;
  historyDraft = "";
  resizeInput();
});

input.addEventListener("keydown", (e) => {
  if (!e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (e.key === "ArrowUp") {
      e.preventDefault();
      navigateHistory(-1);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      navigateHistory(1);
      return;
    }
  }

  if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    send();
  }
});

// Global: Esc interrupts current work; Ctrl+J / Ctrl+K scroll the transcript, even while typing.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
    if (working) {
      e.preventDefault();
      e.stopPropagation();
      interruptWork();
    }
    return;
  }

  if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  if (e.key === "j" || e.key === "k") {
    e.preventDefault();
    const step = 60;
    transcript.scrollTop += e.key === "j" ? step : -step;
  }
}, true);

window.addEventListener("pywebviewready", async () => {
  try {
    const info = await window.pywebview.api.info();
    modelLabel.dataset.model = info.model || "";
    modelLabel.textContent = [info.model, info.session].filter(Boolean).join(" · ");
    setInputHistory(info.input_history);
    setContextUsage(info.context);
    addStartup(info);
  } catch (_) {}
  resizeInput();
  input.focus();
});
