marked.setOptions({ gfm: true, breaks: false });

export const SANITIZE_OPTS = {
  ADD_TAGS: ["foreignObject"],
  ADD_ATTR: ["target"],
};

export function renderMarkdown(src) {
  return DOMPurify.sanitize(marked.parse(src ?? ""), SANITIZE_OPTS);
}

export function autoCloseFences(s) {
  const fences = (s.match(/^```/gm) || []).length;
  return fences % 2 ? s + "\n```" : s;
}

function hasOpenFence(s) {
  return ((s.match(/^```/gm) || []).length % 2) !== 0;
}

function countClosedMermaidBlocks(s) {
  return (s.match(/(^|\n)```mermaid[\s\S]*?\n```/gi) || []).length;
}

function countUnescaped(s, token) {
  let count = 0;
  let pos = 0;
  while (true) {
    const i = s.indexOf(token, pos);
    if (i < 0) return count;
    let slashes = 0;
    for (let j = i - 1; j >= 0 && s[j] === "\\"; j--) slashes += 1;
    if (slashes % 2 === 0) count += 1;
    pos = i + token.length;
  }
}

function mathBlockCount(s) {
  const displayDollars = Math.floor(countUnescaped(s, "$$") / 2);
  const bracketBlocks = (s.match(/\\\[[\s\S]*?\\\]/g) || []).length;
  const parenBlocks = (s.match(/\\\([\s\S]*?\\\)/g) || []).length;
  return displayDollars + bracketBlocks + parenBlocks;
}

export function streamEnhanceKey(raw) {
  const math = mathBlockCount(raw);
  const mermaid = hasOpenFence(raw) ? 0 : countClosedMermaidBlocks(raw);
  const parts = [];
  if (math) parts.push(`math:${math}`);
  if (mermaid) parts.push(`mermaid:${mermaid}`);
  return parts.join("+");
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
