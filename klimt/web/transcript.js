import { enhance, renderMarkdown, streamEnhanceKey } from "./render.js";
import { transcriptFor } from "./tabs.js";

let currentTabId = null;

export function useTranscript(tabId) {
  currentTabId = tabId;
}

function transcript() {
  return transcriptFor(currentTabId);
}

export function scrollToBottom() {
  const el = transcript();
  el.scrollTop = el.scrollHeight;
}

export function clearTranscript() {
  transcript().innerHTML = "";
}

export function escapeMd(text) {
  return String(text ?? "").replace(/[\\`*_{}\[\]()#+.!|>]/g, "\\$&");
}

function codeSpan(text) {
  return "`" + String(text ?? "").replace(/\|/g, "\\|") + "`";
}

export function addBannerLogo() {
  const div = document.createElement("div");
  div.className = "startup-mark";
  div.setAttribute("aria-label", "klimt");
  div.textContent = "[|<] klimt";
  transcript().appendChild(div);
  scrollToBottom();
}

export function addStartup(info) {
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

  lines.push("", "## Commands");
  const commands = Array.isArray(info.commands) ? info.commands : [];
  if (commands.length) {
    lines.push("", "| command | description |", "|---|---|");
    for (const c of commands) {
      const usage = codeSpan(c.usage || "");
      const desc = escapeMd(c.description || "");
      lines.push("| " + usage + " | " + desc + " |");
    }
  } else {
    lines.push("", "type `/help` for commands");
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

  addMessage("system", lines.join("\n"));
}

export function addMessage(role, text, { markdown = true } = {}) {
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
  transcript().appendChild(div);
  scrollToBottom();
  return div;
}

export function addPending(label = "thinking") {
  const div = addMessage("assistant", "", { markdown: false });
  div.classList.add("pending");
  const body = div.querySelector(".body");
  const span = document.createElement("span");
  span.className = "thinking";
  span.textContent = label;
  body.replaceChildren(span);
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

export function addReasoning(text, { done = true } = {}) {
  const h = startReasoning();
  appendReasoningDelta(h, text);
  if (done) finalizeReasoning(h);
  return h.div;
}

export function addTool(name, args, result) {
  const div = document.createElement("div");
  div.className = "msg tool";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "tool · " + name;

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
  transcript().appendChild(div);
  scrollToBottom();
  return div;
}

export function startReasoning() {
  const div = document.createElement("div");
  div.className = "msg reasoning streaming";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "reasoning";

  const body = document.createElement("pre");
  body.className = "body reasoning-body";

  div.appendChild(r);
  div.appendChild(body);
  transcript().appendChild(div);
  scrollToBottom();
  return {
    div,
    body,
    raw: "",
    pending: false,
    raf: null,
    done: false,
  };
}

export function appendReasoningDelta(h, txt) {
  if (h.done) return;
  h.raw += txt;
  if (h.pending) return;
  h.pending = true;
  h.raf = requestAnimationFrame(() => {
    h.raf = null;
    h.pending = false;
    if (h.done) return;
    h.body.textContent = h.raw;
    scrollToBottom();
  });
}

export function finalizeReasoning(h) {
  h.done = true;
  h.pending = false;
  if (h.raf !== null) {
    cancelAnimationFrame(h.raf);
    h.raf = null;
  }
  h.body.textContent = h.raw;
  h.div.classList.remove("streaming");
  scrollToBottom();
}

export function startStreaming() {
  const div = document.createElement("div");
  div.className = "msg assistant streaming";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "assistant";

  const body = document.createElement("div");
  body.className = "body";

  div.appendChild(r);
  div.appendChild(body);
  transcript().appendChild(div);
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
    enhance(h.body);
    h.enhancedKey = key;
    h.enhancedSerial = h.renderSerial;
  }, 120);
}

export function appendDelta(h, txt) {
  if (h.done) return;
  h.raw += txt;
  if (h.pending) return;
  h.pending = true;
  h.raf = requestAnimationFrame(() => {
    h.raf = null;
    h.pending = false;
    if (h.done) return;
    h.body.innerHTML = renderMarkdown(h.raw);
    h.renderSerial += 1;
    scheduleStreamEnhance(h);
    scrollToBottom();
  });
}

export function finalizeStreaming(h) {
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
