/* Image attachment handling: paste, drag-and-drop, thumbnail strip.
 *
 * Attachments are stored as envelope objects matching the _klimt_image shape
 * that visual.py produces. When the user sends, the pending list is flushed to
 * the backend via the attachments argument of api.send(), then cleared.
 *
 * Public API:
 *   installAttachmentHandlers(input)  — wire paste/drop to the given textarea
 *   getPendingAttachments()           — returns a copy of the pending list
 *   clearAttachments()                — clear pending list and strip UI
 *   renderAttachmentThumbnail(env)    — returns an <img> element for transcript replay
 */

const MAX_BYTES = 3_750_000;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

// Pending list: each entry is { _klimt_image: true, media_type, data, bytes, name? }
let _pending = [];

let _strip = null;

function strip() {
  if (!_strip) {
    _strip = document.getElementById("attachment-strip");
  }
  return _strip;
}

export function getPendingAttachments() {
  return _pending.slice();
}

export function clearAttachments() {
  _pending = [];
  const s = strip();
  if (s) s.innerHTML = "";
}

function addAttachment(envelope) {
  _pending.push(envelope);
  _renderThumb(envelope, _pending.length - 1);
}

function _renderThumb(envelope, idx) {
  const s = strip();
  if (!s) return;

  const wrap = document.createElement("div");
  wrap.className = "attachment-thumb";
  wrap.dataset.idx = idx;

  const img = document.createElement("img");
  img.src = `data:${envelope.media_type};base64,${envelope.data}`;
  img.alt = envelope.name || "attachment";
  img.title = `${envelope.name || "image"} (${_humanBytes(envelope.bytes)})`;

  const del = document.createElement("button");
  del.type = "button";
  del.className = "attachment-remove";
  del.textContent = "×";
  del.title = "remove";
  del.addEventListener("click", () => {
    const i = parseInt(wrap.dataset.idx, 10);
    _pending.splice(i, 1);
    wrap.remove();
    // Re-index remaining thumbs
    for (const t of s.querySelectorAll(".attachment-thumb")) {
      const j = parseInt(t.dataset.idx, 10);
      if (j > i) t.dataset.idx = j - 1;
    }
  });

  wrap.appendChild(img);
  wrap.appendChild(del);
  s.appendChild(wrap);
}

function _humanBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

async function _processFile(file) {
  if (!ALLOWED_TYPES.has(file.type)) return;

  const buf = await file.arrayBuffer();
  if (buf.byteLength === 0 || buf.byteLength > MAX_BYTES) return;

  // Base64 encode
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  const data = btoa(binary);

  addAttachment({
    _klimt_image: true,
    media_type: file.type,
    data,
    bytes: buf.byteLength,
    name: file.name || undefined,
  });
}

export function installAttachmentHandlers(input) {
  // Paste
  input.addEventListener("paste", (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    let hadImage = false;
    for (const item of items) {
      if (item.kind === "file" && ALLOWED_TYPES.has(item.type)) {
        hadImage = true;
        const file = item.getAsFile();
        if (file) _processFile(file);
      }
    }
    if (hadImage) e.preventDefault();
  });

  // Drag-and-drop
  input.addEventListener("dragover", (e) => {
    if ([...e.dataTransfer.items].some(i => i.kind === "file" && ALLOWED_TYPES.has(i.type))) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  });

  input.addEventListener("drop", (e) => {
    const files = [...e.dataTransfer.files].filter(f => ALLOWED_TYPES.has(f.type));
    if (!files.length) return;
    e.preventDefault();
    for (const f of files) _processFile(f);
  });
}

/** Return an <img> element suitable for embedding in a transcript message bubble. */
export function renderAttachmentThumbnail(envelope) {
  const img = document.createElement("img");
  img.className = "attachment-inline";
  img.src = `data:${envelope.media_type};base64,${envelope.data}`;
  img.alt = envelope.name || "image";
  img.title = _humanBytes(envelope.bytes || 0);
  return img;
}
