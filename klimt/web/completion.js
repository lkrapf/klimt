const input = document.getElementById("input");

let popup = null;
let state = null;
let requestId = 0;

function commonPrefix(values) {
  if (!values.length) return "";
  let prefix = values[0];
  for (const value of values.slice(1)) {
    while (prefix && !value.startsWith(prefix)) prefix = prefix.slice(0, -1);
    if (!prefix) break;
  }
  return prefix;
}

function replaceRange(range, value) {
  const text = input.value;
  input.value = text.slice(0, range.start) + value + text.slice(range.end);
  const pos = range.start + value.length;
  input.selectionStart = input.selectionEnd = pos;
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function closePopup() {
  popup?.remove();
  popup = null;
  state = null;
}

function ensurePopup() {
  if (popup) return popup;
  popup = document.createElement("div");
  popup.className = "completion-popup";
  popup.setAttribute("role", "listbox");
  document.body.appendChild(popup);
  return popup;
}

function positionPopup() {
  if (!popup) return;
  const rect = input.getBoundingClientRect();
  popup.style.left = `${rect.left}px`;
  popup.style.width = `${rect.width}px`;
  popup.style.bottom = `${Math.max(0, window.innerHeight - rect.top + 4)}px`;
}

function renderPopup() {
  if (!state?.items?.length) {
    closePopup();
    return;
  }
  const el = ensurePopup();
  el.innerHTML = "";
  for (let i = 0; i < state.items.length; i++) {
    const item = state.items[i];
    const row = document.createElement("button");
    row.type = "button";
    row.className = "completion-item";
    row.classList.toggle("active", i === state.index);
    row.textContent = item.label || item.value || "";
    row.addEventListener("mousedown", (e) => {
      e.preventDefault();
      acceptCompletion(i);
    });
    el.appendChild(row);
  }
  positionPopup();
}

function acceptCompletion(index = state?.index || 0) {
  if (!state?.items?.length) return false;
  const item = state.items[index];
  if (!item) return false;
  replaceRange(state.range, item.value || "");
  closePopup();
  return true;
}

function sameRequest(text, cursor) {
  return state && state.text === text && state.cursor === cursor;
}

function cycleCompletion(reverse = false) {
  if (!state?.items?.length) return false;
  const n = state.items.length;
  state.index = (state.index + (reverse ? n - 1 : 1)) % n;
  renderPopup();
  return true;
}

export function acceptVisibleCompletion() {
  if (!popup || !state?.items?.length) return false;
  return acceptCompletion();
}

export function cycleCompletionBackward() {
  if (!popup || !state?.items?.length) return false;
  return cycleCompletion(true);
}

export function cancelCompletion() {
  if (!state) return false;
  closePopup();
  return true;
}

export async function completeAtCursor(tabId) {
  const text = input.value;
  const cursor = input.selectionStart ?? text.length;
  if (input.selectionEnd !== cursor) return false;

  if (sameRequest(text, cursor) && cycleCompletion(false)) return true;

  const id = ++requestId;
  let result;
  try {
    result = await window.pywebview.api.complete(text, cursor, tabId);
  } catch (e) {
    closePopup();
    return false;
  }
  if (id !== requestId) return true;

  const range = result?.range || { start: cursor, end: cursor };
  const items = Array.isArray(result?.items) ? result.items.filter((x) => x?.value) : [];
  if (!items.length) {
    closePopup();
    return true;
  }

  const prefix = commonPrefix(items.map((x) => x.value));
  const current = text.slice(range.start, cursor);
  if (prefix && prefix !== current) {
    replaceRange(range, prefix);
    state = {
      text: input.value,
      cursor: input.selectionStart,
      range: { start: range.start, end: range.start + prefix.length },
      items,
      index: 0,
    };
    if (items.length > 1) renderPopup();
    else closePopup();
    return true;
  }

  state = { text, cursor, range, items, index: 0 };
  if (items.length === 1) return acceptCompletion(0);
  renderPopup();
  return true;
}

input.addEventListener("input", () => closePopup());
window.addEventListener("resize", positionPopup);
