const tabsBar = document.getElementById("tabs");
const newTabButton = document.getElementById("new-tab");
const transcriptHost = document.getElementById("transcripts");

let tabs = [];
let activeTabId = null;
let onActivate = () => {};
let onClose = () => {};
let onNew = () => {};

function tabState(tab) {
  return {
    id: tab.id,
    model: tab.model || "",
    session: tab.session || "",
    inputHistory: Array.isArray(tab.input_history) ? tab.input_history.slice() : [],
    context: tab.context || null,
    working: Boolean(tab.busy),
    queue: [],
    current: null,
    reasoning: null,
    pending: null,
    suppressUntilDone: false,
  };
}

export function installTabs({ activate, close, create }) {
  onActivate = activate;
  onClose = close;
  onNew = create;
  newTabButton.addEventListener("click", () => onNew());
}

export function initializeTabs(initialTabs, activeId) {
  tabs = (Array.isArray(initialTabs) && initialTabs.length ? initialTabs : [{ id: "tab-1" }]).map(tabState);
  activeTabId = activeId || tabs[0].id;
  for (const tab of tabs) ensureTranscript(tab.id);
  renderTabs();
}

export function allTabs() {
  return tabs.slice();
}

export function getTab(tabId = activeTabId) {
  let tab = tabs.find((t) => t.id === tabId);
  if (!tab) {
    tab = tabState({ id: tabId || `tab-${Date.now()}` });
    tabs.push(tab);
    ensureTranscript(tab.id);
    renderTabs();
  }
  return tab;
}

export function activeTab() {
  return getTab(activeTabId);
}

export function activeId() {
  return activeTabId;
}

export function addTab(tabInfo) {
  const tab = tabState(tabInfo);
  tabs.push(tab);
  ensureTranscript(tab.id);
  activateTab(tab.id);
  renderTabs();
  return tab;
}

export function activateTab(tabId) {
  if (!tabs.some((t) => t.id === tabId)) return;
  activeTabId = tabId;
  document.querySelectorAll(".transcript").forEach((el) => {
    el.classList.toggle("active", el.dataset.tabId === tabId);
  });
  renderTabs();
  onActivate(getTab(tabId));
}

export function closeTab(tabId) {
  if (tabs.length <= 1) return;
  const idx = tabs.findIndex((t) => t.id === tabId);
  if (idx < 0) return;
  const closingActive = tabId === activeTabId;
  if (tabs[idx].working) return;
  onClose(tabId);
  tabs.splice(idx, 1);
  document.querySelector(`.transcript[data-tab-id="${CSS.escape(tabId)}"]`)?.remove();
  if (closingActive) activeTabId = tabs[Math.max(0, idx - 1)].id;
  activateTab(activeTabId);
  renderTabs();
}

export function updateTab(tabId, fields) {
  const tab = getTab(tabId);
  Object.assign(tab, fields);
  renderTabs();
  return tab;
}

export function ensureTranscript(tabId) {
  let el = document.querySelector(`.transcript[data-tab-id="${CSS.escape(tabId)}"]`);
  if (el) return el;
  el = document.createElement("main");
  el.className = "transcript";
  el.dataset.tabId = tabId;
  el.setAttribute("aria-live", "polite");
  transcriptHost.appendChild(el);
  el.classList.toggle("active", tabId === activeTabId);
  return el;
}

export function transcriptFor(tabId = activeTabId) {
  return ensureTranscript(tabId);
}

function renderTabs() {
  tabsBar.innerHTML = "";
  tabs.forEach((tab, index) => {
    const button = document.createElement("button");
    button.className = "tab";
    button.classList.toggle("active", tab.id === activeTabId);
    button.classList.toggle("working", Boolean(tab.working));
    button.title = [tab.model, tab.session].filter(Boolean).join(" · ");
    button.addEventListener("click", () => activateTab(tab.id));

    const label = document.createElement("span");
    label.className = "tab-label";
    label.textContent = tab.session || `tab ${index + 1}`;
    button.appendChild(label);

    const close = document.createElement("span");
    close.className = "tab-close";
    close.textContent = "×";
    close.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeTab(tab.id);
    });
    button.appendChild(close);
    tabsBar.appendChild(button);
  });
}
