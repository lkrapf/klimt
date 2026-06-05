/* Tool box rendering: pending → result pre, plus argument summaries.
 *
 * Kept separate from transcript.js because tool presentation changes
 * independently of the message/streaming pipeline.
 */
import { appendToTranscript, scrollToBottom } from "./transcript.js";

function summarizeArgs(name, args) {
  if (name === "bash")  return "$ " + (args.command ?? "");
  if (name === "read")  return "read " + (args.path ?? "");
  if (name === "write") return "write " + (args.path ?? "") +
                              " (" + (args.content?.length ?? 0) + " bytes)";
  if (name === "glob")  return "glob " + (args.pattern ?? "") +
                              (args.path ? " in " + args.path : "");
  if (name === "grep") {
    const parts = ["grep " + JSON.stringify(args.pattern ?? "")];
    if (args.path) parts.push("in " + args.path);
    if (args.glob) parts.push("glob=" + args.glob);
    if (args.case_insensitive) parts.push("-i");
    return parts.join(" ");
  }
  if (name === "agent") {
    const head = "agent " + (args.name ?? "");
    const prompt = (args.prompt ?? "").replace(/\s+/g, " ").trim();
    return prompt ? head + ": " + (prompt.length > 80 ? prompt.slice(0, 80) + "\u2026" : prompt) : head;
  }
  try { return JSON.stringify(args); } catch (_) { return String(args); }
}

export function startTool(name, args) {
  const div = document.createElement("div");
  div.className = "msg tool pending";

  const r = document.createElement("div");
  r.className = "role";
  r.textContent = "tool · " + name;

  const body = document.createElement("div");
  body.className = "body";

  const call = document.createElement("pre");
  call.className = "tool-call";
  if (name === "bash") {
    const code = document.createElement("code");
    code.className = "language-bash";
    code.textContent = args.command ?? "";
    call.appendChild(code);
    if (window.hljs) {
      try { window.hljs.highlightElement(code); }
      catch (e) { console.warn("highlight.js failed", e); }
    }
  } else {
    call.textContent = summarizeArgs(name, args);
  }

  const out = document.createElement("pre");
  out.className = "tool-out";
  const waiting = document.createElement("span");
  waiting.className = "thinking";
  waiting.textContent = "running";
  out.appendChild(waiting);

  body.appendChild(call);
  body.appendChild(out);
  div.appendChild(r);
  div.appendChild(body);
  appendToTranscript(div);
  scrollToBottom();
  return { div, out };
}

export function finalizeTool(handle, result) {
  if (!handle) return null;
  handle.div.classList.remove("pending");
  handle.out.textContent = result;
  scrollToBottom();
  return handle.div;
}

export function addTool(name, args, result) {
  const handle = startTool(name, args);
  return finalizeTool(handle, result);
}
