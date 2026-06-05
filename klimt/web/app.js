"use strict";

import { installEventHandler } from "./events.js";
import { finishWork, installInputHandlers, setInputHistory, submitCommand } from "./input.js";
import { installNavGuard } from "./navguard.js";
import { showTabStatus } from "./status.js";
import { setTheme } from "./theme.js";
import { activateTab, activeTab, initializeTabs, installTabs } from "./tabs.js";
import { useTranscript } from "./transcript.js";
import { addStartup } from "./startup.js";

const klimt = {};
window.klimt = klimt;

installEventHandler(
  klimt,
  (tab) => finishWork(klimt, tab),
  setInputHistory,
  (command, opts) => submitCommand(klimt, command, opts),
);
installInputHandlers(klimt);
installNavGuard();
installTabs({
  activate: (tab) => {
    useTranscript(tab.id);
    showTabStatus(tab);
  },
  close: (tabId) => window.klimtTabControls?.closeTab(tabId),
  create: () => window.klimtTabControls?.createNewTab(),
});

let initialized = false;

async function initialize() {
  if (initialized || !window.pywebview?.api) return;
  initialized = true;
  try {
    const info = await window.pywebview.api.info();
    setTheme(info.theme);
    initializeTabs(info.tabs, info.active_tab);
    activateTab(info.active_tab);
    const tab = activeTab();
    useTranscript(tab.id);
    showTabStatus(tab);
    addStartup(info);
  } catch (e) {
    initialized = false;
    console.error("Klimt startup failed", e);
  }
}

window.addEventListener("pywebviewready", initialize);
initialize();
