const THEME_LINK_ID = "theme-css";
const DEFAULT_THEME = "amber";
const THEME_NAME_RE = /^[A-Za-z0-9_-]+$/;

function safeThemeName(name) {
  const value = String(name || DEFAULT_THEME).trim();
  return THEME_NAME_RE.test(value) ? value : DEFAULT_THEME;
}

function themeHref(name) {
  return new URL(`themes/${safeThemeName(name)}.css`, window.location.href).href;
}

export function currentTheme() {
  const link = document.getElementById(THEME_LINK_ID);
  return link?.dataset.theme || DEFAULT_THEME;
}

export function setTheme(name, { bust = false } = {}) {
  const theme = safeThemeName(name);
  let link = document.getElementById(THEME_LINK_ID);
  if (!link) {
    link = document.createElement("link");
    link.id = THEME_LINK_ID;
    link.rel = "stylesheet";
    document.head.appendChild(link);
  }
  link.dataset.theme = theme;
  const href = themeHref(theme);
  link.href = bust ? `${href}?v=${Date.now()}` : href;
  return theme;
}

export function reloadCss() {
  const bust = `v=${Date.now()}`;
  document.querySelectorAll('link[rel="stylesheet"]').forEach((link) => {
    const path = new URL(link.href, window.location.href).pathname;
    if (path.endsWith("/app.css")) {
      link.href = `${new URL("app.css", window.location.href).href}?${bust}`;
    }
  });
  setTheme(currentTheme(), { bust: true });
}
