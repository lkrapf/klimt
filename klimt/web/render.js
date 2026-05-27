marked.setOptions({ gfm: true, breaks: false });

const SANITIZE_OPTS = {
  ADD_TAGS: ["foreignObject"],
  ADD_ATTR: ["target"],
};

function escapeHtml(s) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isEscaped(src, pos) {
  let slashes = 0;
  for (let i = pos - 1; i >= 0 && src[i] === "\\"; i--) slashes += 1;
  return slashes % 2 !== 0;
}

function findUnescaped(src, token, start) {
  let pos = start;
  while (true) {
    const i = src.indexOf(token, pos);
    if (i < 0) return -1;
    if (!isEscaped(src, i)) return i;
    pos = i + token.length;
  }
}

function backtickRunLength(src, pos) {
  let len = 0;
  while (src[pos + len] === "`") len += 1;
  return len;
}

function fenceMarker(line) {
  const m = line.match(/^ {0,3}(`{3,}|~{3,})/);
  return m ? { char: m[1][0], len: m[1].length } : null;
}

function isClosingFence(line, fence) {
  const m = fenceMarker(line);
  return Boolean(m && m.char === fence.char && m.len >= fence.len);
}

function protectMathInText(src) {
  let out = "";
  let pos = 0;

  while (pos < src.length) {
    if (src[pos] === "`") {
      const ticks = backtickRunLength(src, pos);
      const marker = "`".repeat(ticks);
      const end = src.indexOf(marker, pos + ticks);
      if (end < 0) {
        out += src.slice(pos);
        break;
      }
      out += src.slice(pos, end + ticks);
      pos = end + ticks;
      continue;
    }

    const open = src.startsWith("$$", pos) && !isEscaped(src, pos) ? "$$"
      : src.startsWith("\\[", pos) && !isEscaped(src, pos) ? "\\["
      : null;
    if (open) {
      const close = open === "$$" ? "$$" : "\\]";
      const end = findUnescaped(src, close, pos + open.length);
      if (end >= 0) {
        const math = src.slice(pos, end + close.length);
        out += `<div class="klimt-display-math">${escapeHtml(math)}</div>`;
        pos = end + close.length;
        continue;
      }
    }

    out += src[pos];
    pos += 1;
  }

  return out;
}

function protectDisplayMath(src) {
  let text = "";
  let prose = "";
  let fence = null;
  let pos = 0;

  function flushProse() {
    if (!prose) return;
    text += protectMathInText(prose);
    prose = "";
  }

  while (pos < src.length) {
    const newline = src.indexOf("\n", pos);
    const line = newline < 0 ? src.slice(pos) : src.slice(pos, newline + 1);

    if (fence) {
      text += line;
      if (isClosingFence(line, fence)) fence = null;
    } else {
      const marker = fenceMarker(line);
      if (marker) {
        flushProse();
        text += line;
        fence = marker;
      } else {
        prose += line;
      }
    }

    if (newline < 0) break;
    pos = newline + 1;
  }

  flushProse();
  return text;
}

export function renderMarkdown(src) {
  const text = protectDisplayMath(autoCloseFences(src ?? ""));
  return DOMPurify.sanitize(marked.parse(text), SANITIZE_OPTS);
}

function autoCloseFences(s) {
  const fences = (s.match(/^```/gm) || []).length;
  return fences % 2 ? s + "\n```" : s;
}

let mermaidCounter = 0;

function waitForGlobal(name, timeoutMs = 5000) {
  if (window[name]) return Promise.resolve(window[name]);

  return new Promise((resolve) => {
    const start = Date.now();
    const timer = setInterval(() => {
      if (window[name]) {
        clearInterval(timer);
        resolve(window[name]);
      } else if (Date.now() - start >= timeoutMs) {
        clearInterval(timer);
        resolve(null);
      }
    }, 50);
  });
}

function renderMath(root, renderMathInElement) {
  renderMathInElement(root, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "\\[", right: "\\]", display: true },
      { left: "$",  right: "$",  display: false },
      { left: "\\(", right: "\\)", display: false },
    ],
    throwOnError: false,
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
    processEscapes: true,
  });
}

export async function enhance(root) {
  root.querySelectorAll("img").forEach((img) => {
    img.referrerPolicy = "no-referrer";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => {
      const url = img.getAttribute("src") || "";
      const a = document.createElement("a");
      a.href = url;
      a.textContent = `image failed to load: ${url}`;
      a.target = "_blank";
      a.rel = "noreferrer";
      img.replaceWith(a);
    }, { once: true });
  });

  const renderMathInElement = await waitForGlobal("renderMathInElement");
  if (renderMathInElement) {
    try { renderMath(root, renderMathInElement); }
    catch (e) { console.warn("KaTeX render failed", e); }
  } else if ((root.textContent || "").includes("$")) {
    console.warn("KaTeX render skipped: window.renderMathInElement was not loaded");
  }

  if (window.hljs) {
    root.querySelectorAll("pre > code:not(.language-mermaid)").forEach((code) => {
      try { window.hljs.highlightElement(code); }
      catch (e) { console.warn("highlight.js failed", e); }
    });
  } else {
    console.warn("highlight.js unavailable; syntax highlighting skipped");
  }

  root.querySelectorAll("pre > code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.id = `mmd-${++mermaidCounter}`;
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });

  const mmd = root.querySelectorAll(".mermaid");
  if (mmd.length) {
    const mermaid = await waitForGlobal("mermaid");
    if (mermaid) {
      try { await mermaid.run({ nodes: mmd }); }
      catch (e) { console.warn("mermaid render failed", e); }
    } else {
      console.warn("mermaid render skipped: window.mermaid was not loaded");
    }
  }
}
