import { setWorking as setWorkingStatus } from "./status.js";
import { addMessage, addPending, finalizeStreaming } from "./transcript.js";

const input = document.getElementById("input");
let inputHistory = [];
let historyPos = null;
let historyDraft = "";
let working = false;

export function setInputHistory(items) {
  inputHistory = Array.isArray(items) ? items.slice() : [];
  historyPos = null;
  historyDraft = "";
}

export function isWorking() {
  return working;
}

export function setWorking(on) {
  working = setWorkingStatus(on);
}

export function finishWork(klimt) {
  if (klimt.pending) { klimt.pending.remove(); klimt.pending = null; }
  if (klimt.reasoning) {
    klimt.reasoning.div?.classList.remove("streaming");
    klimt.reasoning = null;
  }
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

function interruptWork(klimt) {
  if (!working) return;

  klimt.suppressUntilDone = true;
  finishWork(klimt);
  addMessage("system", "_interrupted_");

  window.pywebview.api.interrupt().catch((e) => {
    addMessage("error", "**Bridge error:** " + (e?.message || e));
  });
}

export async function submitCommand(klimt, text, { echo = true } = {}) {
  text = String(text ?? "").trim();
  if (!text || working) return false;

  rememberInput(text);
  setInputValue("");
  setWorking(true);
  if (echo) addMessage("user", text, { markdown: false });
  klimt.pending = addPending();

  try {
    const res = await window.pywebview.api.send(text);
    if (!res.ok) {
      addMessage("error", "**Error:** " + res.error);
      finishWork(klimt);
    }
    return true;
  } catch (e) {
    addMessage("error", "**Bridge error:** " + (e?.message || e));
    finishWork(klimt);
    return false;
  }
}

async function send(klimt) {
  submitCommand(klimt, input.value);
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

    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      send(klimt);
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.key === "t" || e.key === "T") && e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      document.body.classList.toggle("hide-reasoning");
      return;
    }

    if (e.key === "Escape" && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey) {
      if (working) {
        e.preventDefault();
        e.stopPropagation();
        interruptWork(klimt);
      }
      return;
    }

    if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      const transcript = document.getElementById("transcript");
      const step = 60;
      transcript.scrollTop += e.key === "j" ? step : -step;
    }
  }, true);

  resizeInput();
  input.focus();
}
