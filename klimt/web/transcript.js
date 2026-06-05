/* Transcript DOM ownership and message/streaming rendering.
 *
 * Tool boxes live in tool_view.js. Startup banner/table rendering lives in
 * startup.js. Both import appendToTranscript/scrollToBottom from here so all
 * DOM appends go through the active tab's transcript container.
 */
import { enhance, renderMarkdown } from "./render.js";
import { transcriptFor } from "./tabs.js";

let currentTabId = null;

export function useTranscript(tabId) {
  currentTabId = tabId;
}

function transcript() {
  return transcriptFor(currentTabId);
}

export function appendToTranscript(node) {
  transcript().appendChild(node);
}

export function scrollToBottom() {
  const el = transcript();
  el.scrollTop = el.scrollHeight;
}

export function clearTranscript() {
  transcript().innerHTML = "";
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
  appendToTranscript(div);
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

export function addReasoning(text, { done = true } = {}) {
  const h = startReasoning();
  appendReasoningDelta(h, text);
  if (done) finalizeReasoning(h);
  return h.div;
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
  appendToTranscript(div);
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
  appendToTranscript(div);
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
  h.body.innerHTML = renderMarkdown(h.raw);
  enhance(h.body);
  h.div.classList.remove("streaming");
  scrollToBottom();
}

