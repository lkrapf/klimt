/* Startup banner: version mark, available skills/commands/tools tables.
 *
 * Renders once per tab at boot. Kept separate from transcript.js so the
 * initial summary content can evolve without touching message-streaming code.
 */
import { addMessage, appendToTranscript, scrollToBottom } from "./transcript.js";

function tableCell(text) {
  return String(text ?? "").replace(/\|/g, "\\|");
}

function codeSpan(text) {
  return "`" + String(text ?? "").replace(/\|/g, "\\|") + "`";
}

export function addBannerLogo() {
  const div = document.createElement("div");
  div.className = "startup-mark";
  div.setAttribute("aria-label", "klimt");
  div.innerHTML =
    '<span class="logo-bracket">[</span>' +
    '<span class="logo-core">|&lt;</span>' +
    '<span class="logo-bracket">]</span>' +
    ' <span class="logo-word">klimt</span>';
  appendToTranscript(div);
  scrollToBottom();
}

export function addStartup(info) {
  addBannerLogo();

  const lines = [
    `version ${info.version || "unknown"}`,
    "",
    "## Available skills",
  ];

  const skills = Array.isArray(info.skills) ? info.skills : [];
  if (skills.length) {
    lines.push("", "| skill | description |", "|---|---|");
    for (const s of skills) {
      const name = tableCell(s.name || "unnamed");
      const desc = tableCell(s.description || "(no description)");
      lines.push("| `/" + name + "` | " + desc + " |");
    }
  } else {
    lines.push("", "_none_");
  }

  lines.push("", "## Commands");
  const commands = Array.isArray(info.commands) ? info.commands : [];
  if (commands.length) {
    lines.push("", "| command | description |", "|---|---|");
    for (const c of commands) {
      const usage = codeSpan(c.usage || "");
      const desc = tableCell(c.description || "");
      lines.push("| " + usage + " | " + desc + " |");
    }
  } else {
    lines.push("", "type `/help` for commands");
  }

  lines.push("", "## Available tools");
  const tools = Array.isArray(info.available_tools)
    ? info.available_tools
    : (Array.isArray(info.tools) ? info.tools : []);
  if (tools.length) {
    lines.push("", "| tool | description |", "|---|---|");
    for (const t of tools) {
      const name = tableCell(t.name || "unnamed");
      const desc = tableCell(t.description || "(no description)");
      lines.push("| `" + name + "` | " + desc + " |");
    }
  } else {
    lines.push("", "_none_");
  }

  addMessage("system", lines.join("\n"));
}
