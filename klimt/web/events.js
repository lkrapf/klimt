import { addSelect } from "./selectors.js";
import { reloadCss, setContextUsage, setSessionLabel } from "./status.js";
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
} from "./transcript.js";

export function installEventHandler(klimt, finishWork, setInputHistory, submitCommand) {
  klimt.handleEvent = function(ev) {
    if (klimt.suppressUntilDone && ev.type !== "done") return;
    if (klimt.pending) { klimt.pending.remove(); klimt.pending = null; }

    switch (ev.type) {
      case "reasoning_start":
        klimt.reasoning = startReasoning();
        break;
      case "reasoning_delta":
        if (!klimt.reasoning) klimt.reasoning = startReasoning();
        appendReasoningDelta(klimt.reasoning, ev.content);
        break;
      case "reasoning_end":
        if (klimt.reasoning) {
          finalizeReasoning(klimt.reasoning);
          klimt.reasoning = null;
        }
        break;
      case "reasoning":
        addReasoning(ev.content || "");
        break;
      case "text_start":
        if (klimt.reasoning) {
          finalizeReasoning(klimt.reasoning);
          klimt.reasoning = null;
        }
        klimt.current = startStreaming();
        break;
      case "text_delta":
        if (!klimt.current) klimt.current = startStreaming();
        appendDelta(klimt.current, ev.content);
        break;
      case "text_end":
        if (klimt.current) {
          finalizeStreaming(klimt.current);
          klimt.current = null;
        }
        break;
      case "text":
        addMessage("assistant", ev.content || "");
        break;
      case "select":
        addSelect(ev, (command, opts) => submitCommand(command, opts));
        break;
      case "message": {
        const role = ev.role || "assistant";
        addMessage(role, ev.content || "", { markdown: role !== "user" });
        break;
      }
      case "clear":
        clearTranscript();
        klimt.current = null;
        klimt.reasoning = null;
        klimt.pending = null;
        break;
      case "input_history":
        setInputHistory(ev.items);
        break;
      case "session":
        setSessionLabel(ev.model, ev.name);
        break;
      case "context":
        setContextUsage(ev);
        break;
      case "tool":
        if (klimt.reasoning) {
          finalizeReasoning(klimt.reasoning);
          klimt.reasoning = null;
        }
        if (klimt.current) {
          finalizeStreaming(klimt.current);
          klimt.current = null;
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
        klimt.suppressUntilDone = false;
        finishWork();
        break;
    }
  };
}
