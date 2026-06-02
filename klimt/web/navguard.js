"use strict";

// Keep the Klimt window from ever navigating away from index.html.
//
// Defends against:
// - clicking <a href> in rendered Markdown (left, middle, modifier, auxclick)
// - drag-and-drop of a URL onto the window
// - same-document form submits or stray window.location writes from inside
//   rendered content (logs them rather than silently navigating)
//
// Anchor clicks are routed to the Python bridge, which validates the scheme
// and opens the user's default browser. javascript:, file:, custom schemes,
// and empty hrefs are refused.

const ALLOWED_SCHEMES = new Set(["http:", "https:", "mailto:"]);

function findAnchor(target) {
  let el = target;
  while (el && el !== document) {
    if (el.tagName === "A") return el;
    el = el.parentNode;
  }
  return null;
}

function isExternallyOpenable(href) {
  if (!href) return false;
  let url;
  try {
    url = new URL(href, document.baseURI);
  } catch (_) {
    return false;
  }
  return ALLOWED_SCHEMES.has(url.protocol);
}

async function openExternal(href) {
  const api = window.pywebview?.api;
  if (!api?.open_url) {
    console.warn("navguard: pywebview bridge unavailable, dropping click");
    return;
  }
  try {
    const result = await api.open_url(href);
    if (result && result.ok === false) {
      console.warn("navguard: open_url refused", href, result.error);
    }
  } catch (e) {
    console.warn("navguard: open_url threw", e);
  }
}

function handleAnchorClick(event) {
  const a = findAnchor(event.target);
  if (!a) return;

  // In-page fragment links stay in-page; let the browser handle them.
  const href = a.getAttribute("href") || "";
  if (href.startsWith("#")) return;

  // Always cancel the default. The window must never navigate.
  event.preventDefault();
  event.stopPropagation();

  if (isExternallyOpenable(a.href)) {
    openExternal(a.href);
  } else {
    console.warn("navguard: refused link", a.href || href);
  }
}

function blockDrop(event) {
  // Files or URLs dropped onto the window would otherwise navigate.
  event.preventDefault();
}

function blockBeforeUnload(event) {
  // Tripwire: if something tries to unload the document we want to know.
  // Don't actually prompt the user; just log. Returning undefined is a no-op.
  console.warn("navguard: beforeunload fired", event);
}

export function installNavGuard() {
  // Use capture so we beat any bubbling handlers in rendered content.
  document.addEventListener("click", handleAnchorClick, true);
  document.addEventListener("auxclick", handleAnchorClick, true);
  document.addEventListener("dragover", blockDrop, false);
  document.addEventListener("drop", blockDrop, false);
  window.addEventListener("beforeunload", blockBeforeUnload);
}
