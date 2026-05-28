import { reloadCss, setContextUsage, setCwd, setSessionLabel } from "./status.js";
import { getTab, updateTab } from "./tabs.js";
import {
  addMessage,
  addTool,
  addReasoning,
  appendDelta,
  appendReasoningDelta,
  clearTranscript,
  finalizeReasoning,
  finalizeStreaming,
  startReasoning,
  startStreaming,
  useTranscript,
} from "./transcript.js";

export function installEventHandler(klimt, finishWork, setInputHistory, submitCommand) {
  klimt.handleEvent = function(ev) {
    const tab = getTab(ev.tabId);
    useTranscript(tab.id);
    if (tab.suppressUntilDone && ev.type !== "done") return;
    if (tab.pending) { tab.pending.remove(); tab.pending = null; }

    switch (ev.type) {
      case "reasoning_start":
        tab.reasoning = startReasoning();
        break;
      case "reasoning_delta":
        if (!tab.reasoning) tab.reasoning = startReasoning();
        appendReasoningDelta(tab.reasoning, ev.content);
        break;
      case "reasoning_end":
        if (tab.reasoning) {
          finalizeReasoning(tab.reasoning);
          tab.reasoning = null;
        }
        break;
      case "reasoning":
        addReasoning(ev.content || "");
        break;
      case "text_start":
        if (tab.reasoning) {
          finalizeReasoning(tab.reasoning);
          tab.reasoning = null;
        }
        tab.current = startStreaming();
        break;
      case "text_delta":
        if (!tab.current) tab.current = startStreaming();
        appendDelta(tab.current, ev.content);
        break;
      case "text_end":
        if (tab.current) {
          finalizeStreaming(tab.current);
          tab.current = null;
        }
        break;
      case "text":
        addMessage("assistant", ev.content || "");
        break;
      case "message": {
        const role = ev.role || "assistant";
        addMessage(role, ev.content || "", { markdown: role !== "user" });
        break;
      }
      case "clear":
        clearTranscript();
        tab.current = null;
        tab.reasoning = null;
        tab.pending = null;
        break;
      case "input_history":
        setInputHistory(tab, ev.items);
        break;
      case "session":
        tab.model = ev.model || tab.model;
        tab.session = ev.name || tab.session;
        updateTab(tab.id, { model: tab.model, session: tab.session });
        if (document.querySelector(`.transcript.active`)?.dataset.tabId === tab.id) {
          setSessionLabel(ev.model, ev.name);
        }
        break;
      case "context":
        tab.context = ev;
        updateTab(tab.id, { context: ev });
        if (document.querySelector(`.transcript.active`)?.dataset.tabId === tab.id) {
          setContextUsage(ev);
        }
        break;
      case "cwd":
        tab.cwd = ev.path || "";
        updateTab(tab.id, { cwd: tab.cwd });
        if (document.querySelector(`.transcript.active`)?.dataset.tabId === tab.id) {
          setCwd(tab.cwd);
        }
        break;
      case "tool":
        if (tab.reasoning) {
          finalizeReasoning(tab.reasoning);
          tab.reasoning = null;
        }
        if (tab.current) {
          finalizeStreaming(tab.current);
          tab.current = null;
        }
        addTool(ev.name, ev.args, ev.result);
        break;
      case "error":
        addMessage("error", "**Error:** " + ev.message);
        break;
      case "reload_css":
        reloadCss();
        break;
      case "done":
        tab.suppressUntilDone = false;
        finishWork(tab);
        break;
    }
  };
}
