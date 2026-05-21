"use strict";

import { installEventHandler } from "./events.js";
import { finishWork, installInputHandlers, setInputHistory } from "./input.js";
import { setContextUsage, setSessionLabel } from "./status.js";
import { addStartup } from "./transcript.js";

const klimt = { pending: null, current: null, suppressUntilDone: false };
window.klimt = klimt;

installEventHandler(klimt, () => finishWork(klimt), setInputHistory);
installInputHandlers(klimt);

window.addEventListener("pywebviewready", async () => {
  try {
    const info = await window.pywebview.api.info();
    setSessionLabel(info.model, info.session);
    setInputHistory(info.input_history);
    setContextUsage(info.context);
    addStartup(info);
  } catch (_) {}
});
