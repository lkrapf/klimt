import { acceptVisibleCompletion, cancelCompletion, completeAtCursor, cycleCompletionBackward } from "./completion.js";
import { setQueueCount, setWorking as setWorkingStatus } from "./status.js";
import { activeTab, activeId, activateTab, addTab, allTabs, closeTab as closeLocalTab, updateTab } from "./tabs.js";
import { addMessage, addPending, finalizeStreaming, useTranscript } from "./transcript.js";
import { clearAttachments, getPendingAttachments, installAttachmentHandlers } from "./attachments.js";

const input = document.getElementById("input");
let historyPos = null;
let historyDraft = "";

export function setInputHistory(tab, items) {
  tab.inputHistory = Array.isArray(items) ? items.slice() : [];
  historyPos = null;
  historyDraft = "";
}

export function finishWork(klimt, tab = activeTab()) {
  useTranscript(tab.id);
  if (tab.pending) { tab.pending.remove(); tab.pending = null; }
  if (tab.reasoning) {
    tab.reasoning.div?.classList.remove("streaming");
    tab.reasoning = null;
  }
  if (tab.current) {
    finalizeStreaming(tab.current);
    tab.current = null;
  }
  if (tab.tools && tab.tools.size) {
    for (const handle of tab.tools.values()) {
      handle.div.classList.remove("pending");
      handle.out.textContent = "[interrupted]";
    }
    tab.tools.clear();
  }
  tab.working = false;
  updateActiveStatus(tab);
  input.focus();
  runNextQueued(klimt, tab);
}

function updateActiveStatus(tab = activeTab()) {
  updateTab(tab.id, { working: tab.working });
  if (tab.id === activeId()) {
    setWorkingStatus(tab.working);
    setQueueCount(tab.queue.length);
  }
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

function rememberInput(tab, text) {
  if (!text) return;
  if (tab.inputHistory[tab.inputHistory.length - 1] !== text) tab.inputHistory.push(text);
  historyPos = null;
  historyDraft = "";
}

function navigateHistory(delta) {
  const tab = activeTab();
  const inputHistory = tab.inputHistory;
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

function interruptWork(klimt) {
  const tab = activeTab();
  if (!tab.working) return;

  clearQueued(tab);
  tab.suppressUntilDone = true;
  finishWork(klimt, tab);
  // Partial assistant output and any completed tool calls from this turn are
  // preserved in history by the backend (see runner._build_assistant_entry).
  addMessage("system", "_interrupted \u2014 queued messages cleared_");

  window.pywebview.api.interrupt(tab.id).catch((e) => {
    useTranscript(tab.id);
    addMessage("error", "**Bridge error:** " + (e?.message || e));
  });
}

function clearQueued(tab) {
  for (const item of tab.queue) item.pending?.remove();
  tab.queue = [];
  if (tab.id === activeId()) setQueueCount(0);
}

function queueCommand(tab, text, echo) {
  rememberInput(tab, text);
  setInputValue("");
  useTranscript(tab.id);
  if (echo) addMessage("user", text, { markdown: false });
  const pending = addPending("queued");
  tab.queue.push({ text, echo: false, pending });
  if (tab.id === activeId()) setQueueCount(tab.queue.length);
  return true;
}

function runNextQueued(klimt, tab) {
  if (tab.working || tab.suppressUntilDone || !tab.queue.length) return;
  const item = tab.queue.shift();
  item.pending?.remove();
  if (tab.id === activeId()) setQueueCount(tab.queue.length);
  submitCommand(klimt, item.text, { echo: item.echo, tab });
}

export async function submitCommand(klimt, text, { echo = true, tab = activeTab() } = {}) {
  text = String(text ?? "").trim();
  if (!text) return false;
  if (tab.working) return queueCommand(tab, text, echo);

  const attachments = getPendingAttachments();
  clearAttachments();

  rememberInput(tab, text);
  setInputValue("");
  tab.working = true;
  updateActiveStatus(tab);
  useTranscript(tab.id);
  if (echo) {
    const msgDiv = addMessage("user", text, { markdown: false });
    // Render attachment thumbnails above the text in the user bubble
    if (attachments.length) {
      const body = msgDiv.querySelector(".body");
      const thumbWrap = document.createElement("div");
      thumbWrap.className = "attachment-inline-list";
      for (const att of attachments) {
        const img = document.createElement("img");
        img.className = "attachment-inline";
        img.src = `data:${att.media_type};base64,${att.data}`;
        img.alt = att.name || "image";
        thumbWrap.appendChild(img);
      }
      body.prepend(thumbWrap);
    }
  }
  tab.pending = addPending(text.startsWith("!") ? "running" : "thinking");

  try {
    const res = await window.pywebview.api.send(text, tab.id, attachments.length ? attachments : null);
    if (!res.ok) {
      useTranscript(tab.id);
      addMessage("error", "**Error:** " + res.error);
      finishWork(klimt, tab);
    }
    return true;
  } catch (e) {
    useTranscript(tab.id);
    addMessage("error", "**Bridge error:** " + (e?.message || e));
    finishWork(klimt, tab);
    return false;
  }
}

async function send(klimt) {
  submitCommand(klimt, input.value);
}

async function createNewTab() {
  const current = activeTab();
  const res = await window.pywebview.api.new_tab(current?.model || "");
  if (res.ok) addTab(res.tab);
}

async function closeTab(tabId) {
  const res = await window.pywebview.api.close_tab(tabId);
  if (!res.ok) {
    useTranscript(tabId || activeId());
    addMessage("error", "**Error:** " + res.error);
  }
}

function switchRelative(delta) {
  const list = allTabs();
  const idx = list.findIndex((t) => t.id === activeId());
  const next = list[(idx + delta + list.length) % list.length];
  if (next) activateTab(next.id);
}

function switchNumber(n) {
  const tab = allTabs()[n - 1];
  if (tab) activateTab(tab.id);
}

export function installInputHandlers(klimt) {
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

    if (e.key === "Tab" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      completeAtCursor(activeId());
      return;
    }

    if (e.key === "Tab" && e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (cycleCompletionBackward()) {
        e.preventDefault();
        return;
      }
    }

    if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
    }

    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      if (acceptVisibleCompletion()) {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      send(klimt);
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.key === "t" || e.key === "T") && e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      if (e.target === input && input.value) return;
      e.preventDefault();
      createNewTab();
      return;
    }

    if ((e.key === "w" || e.key === "W") && e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      closeLocalTab(activeId());
      return;
    }

    if (e.key === "Tab" && e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      switchRelative(e.shiftKey ? -1 : 1);
      return;
    }

    if (e.altKey && !e.metaKey && !e.ctrlKey && !e.shiftKey && /^[1-9]$/.test(e.key)) {
      e.preventDefault();
      switchNumber(Number(e.key));
      return;
    }

    if ((e.key === "r" || e.key === "R") && e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      document.body.classList.toggle("hide-reasoning");
      return;
    }

    if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
      if (cancelCompletion()) {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      if (activeTab().working) {
        interruptWork(klimt);
      } else {
        // Tab is idle — may have a pending /back or /sessions prompt; let
        // the backend clear it and emit a cancel notice if so.
        window.pywebview.api.interrupt(activeTab().id).catch((e) => {
          useTranscript(activeTab().id);
          addMessage("error", "**Bridge error:** " + (e?.message || e));
        });
      }
      return;
    }

    if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      const transcript = document.querySelector(".transcript.active");
      const step = 60;
      if (transcript) transcript.scrollTop += e.key === "j" ? step : -step;
    }
  }, true);

  installAttachmentHandlers(input);
  window.klimtTabControls = { createNewTab, closeTab };
  resizeInput();
  input.focus();
}
