"use strict";

import { installEventHandler } from "./events.js";
import { finishWork, installInputHandlers, setInputHistory, submitCommand } from "./input.js";
import { setContextUsage, setSessionLabel } from "./status.js";
import { addStartup } from "./transcript.js";

const klimt = { pending: null, current: null, suppressUntilDone: false };
window.klimt = klimt;

installEventHandler(klimt, () => finishWork(klimt), setInputHistory, (command, opts) => submitCommand(klimt, command, opts));
installInputHandlers(klimt);

let initialized = false;

async function initialize() {
  if (initialized || !window.pywebview?.api) return;
  initialized = true;
  try {
    const info = await window.pywebview.api.info();
    setSessionLabel(info.model, info.session);
    setInputHistory(info.input_history);
    setContextUsage(info.context);
    addStartup(info);
  } catch (e) {
    initialized = false;
    console.error("Klimt startup failed", e);
  }
}

window.addEventListener("pywebviewready", initialize);
initialize();
