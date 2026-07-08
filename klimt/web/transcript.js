/* Transcript DOM ownership and message/streaming rendering.
 *
 * Tool boxes live in tool_view.js. Startup banner/table rendering lives in
 * startup.js. Both import appendToTranscript/scrollToBottom from here so all
 * DOM appends go through the active tab's transcript container.
 */
import { enhance, renderMarkdown } from "./render.js";
import { transcriptFor } from "./tabs.js";
import { renderAttachmentThumbnail } from "./attachments.js";

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

// Per-transcript autoscroll stickiness. If the user scrolls away from the
// bottom, streaming updates stop yanking the viewport back. Once they scroll
// (or we force-scroll) back within the threshold, stickiness re-engages.
const NEAR_BOTTOM_PX = 40;
const stickyState = new WeakMap();

function stateFor(el) {
  let s = stickyState.get(el);
  if (s) return s;
  s = { sticky: true };
  stickyState.set(el, s);
  el.addEventListener(
    "scroll",
    () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      s.sticky = dist <= NEAR_BOTTOM_PX;
    },
    { passive: true },
  );
  return s;
}

export function scrollToBottom({ force = false } = {}) {
  const el = transcript();
  const s = stateFor(el);
  if (!force && !s.sticky) return;
  el.scrollTop = el.scrollHeight;
  s.sticky = true;
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

  // Detect a _klimt_image envelope (pasted image replayed from history).
  const envelope = _parseImageEnvelope(text);
  if (envelope) {
    body.classList.add("plain");
    body.appendChild(renderAttachmentThumbnail(envelope));
  } else if (markdown) {
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

function _parseImageEnvelope(text) {
  if (typeof text !== "string" || !text.trimStart().startsWith("{")) return null;
  try {
    const obj = JSON.parse(text);
    if (obj && obj._klimt_image === true && obj.data && obj.media_type) return obj;
  } catch (_) {}
  return null;
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

