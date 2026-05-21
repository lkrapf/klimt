"use strict";

const transcript = document.getElementById("transcript");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");
const modelLabel = document.getElementById("model");

marked.setOptions({ gfm: true, breaks: false });

const SANITIZE_OPTS = { ADD_TAGS: ["foreignObject"], ADD_ATTR: ["target"] };

const klimt = { pending: null, current: null };
window.klimt = klimt;

// ---- rendering -----------------------------------------------------------

function renderMarkdown(src) {
  return DOMPurify.sanitize(marked.parse(src ?? ""), SANITIZE_OPTS);
}

// Close any unterminated ``` block so partial markdown renders cleanly.
function autoCloseFences(s) {
  const fences = (s.match(/^```/gm) || []).length;
  return fences % 2 ? s + "\n```" : s;
}

let mermaidCounter = 0;

async function enhance(root) {
  // 1. Convert ```mermaid blocks into <div class="mermaid">.
  root.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.id = `mmd-${++mermaidCounter}`;
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });

  const mmd = root.querySelectorAll(".mermaid");
  if (mmd.length && window.mermaid) {
    try { await window.mermaid.run({ nodes: mmd }); }
    catch (e) { console.warn("mermaid render failed", e); }
  }

  if (window.renderMathInElement) {
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "$",  right: "$",  display: false },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
  }
}

function scrollToBottom() {
  transcript.scrollTop = transcript.scrollHeight;
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
  call.textContent = summarizeArgs(name, args);

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
  return { div, body, raw: "", pending: false };
}

function appendDelta(h, txt) {
  h.raw += txt;
  if (h.pending) return;
  h.pending = true;
  requestAnimationFrame(() => {
    h.pending = false;
    // Lightweight render: markdown only, no mermaid/KaTeX (run at finalize).
    h.body.innerHTML = DOMPurify.sanitize(
      marked.parse(autoCloseFences(h.raw)),
      SANITIZE_OPTS,
    );
    scrollToBottom();
  });
}

function finalizeStreaming(h) {
  h.body.innerHTML = renderMarkdown(h.raw);
  enhance(h.body);
  h.div.classList.remove("streaming");
  scrollToBottom();
}

// ---- bridge event handler (called from Python via evaluate_js) ----------

klimt.handleEvent = function(ev) {
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
  }
};

// ---- input wiring --------------------------------------------------------

async function send() {
  const text = input.value.trim();
  if (!text) return;

  input.value = "";
  sendBtn.disabled = true;
  addMessage("user", text);
  klimt.pending = addPending();

  try {
    const res = await window.pywebview.api.send(text);
    if (!res.ok) addMessage("error", "**Error:** " + res.error);
  } catch (e) {
    addMessage("error", "**Bridge error:** " + (e?.message || e));
  } finally {
    if (klimt.pending) { klimt.pending.remove(); klimt.pending = null; }
    if (klimt.current) {
      finalizeStreaming(klimt.current);
      klimt.current = null;
    }
    sendBtn.disabled = false;
    input.focus();
  }
}

async function reset() {
  await window.pywebview.api.reset();
  transcript.innerHTML = "";
}

sendBtn.addEventListener("click", send);
resetBtn.addEventListener("click", reset);

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    send();
  }
});

// Global: Ctrl+J / Ctrl+K scroll the transcript, even while typing.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  if (e.key === "j" || e.key === "k") {
    e.preventDefault();
    const step = 60;
    transcript.scrollTop += e.key === "j" ? step : -step;
  }
});

window.addEventListener("pywebviewready", async () => {
  try {
    const info = await window.pywebview.api.info();
    modelLabel.textContent = info.model || "";
  } catch (_) {}
  input.focus();
});
