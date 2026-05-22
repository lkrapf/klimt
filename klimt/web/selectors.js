import { scrollToBottom } from "./transcript.js";
import { transcriptFor } from "./tabs.js";

export function addSelect(ev, submitCommand) {
  const div = document.createElement("div");
  div.className = "msg selector";

  const role = document.createElement("div");
  role.className = "role";
  role.textContent = ev.title || "select";

  const body = document.createElement("div");
  body.className = "body";

  const select = document.createElement("select");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = ev.placeholder || "choose...";
  placeholder.disabled = true;
  placeholder.selected = true;
  select.appendChild(placeholder);

  for (const item of ev.options || []) {
    const opt = document.createElement("option");
    opt.value = item.value || "";
    opt.textContent = item.current ? `* ${item.label || item.value || ""}` : (item.label || item.value || "");
    select.appendChild(opt);
  }

  select.addEventListener("change", () => {
    const command = select.value;
    if (!command) return;
    select.disabled = true;
    submitCommand(command, { echo: false });
  });

  body.appendChild(select);
  div.appendChild(role);
  div.appendChild(body);
  transcriptFor(ev.tabId).appendChild(div);
  scrollToBottom();
  requestAnimationFrame(() => select.focus());
  setTimeout(() => {
    if (!select.disabled) select.focus();
  }, 0);
  return div;
}
