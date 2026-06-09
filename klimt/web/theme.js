const THEME_LINK_ID = "theme-css";
const THEME_STYLE_ID = "theme-css-user";
const DEFAULT_THEME = "editorial";
const THEME_NAME_RE = /^[A-Za-z0-9_-]+$/;

// Names of themes that live in ~/.klimt/themes/ and must be served via bridge.
// Populated by app.js from info().user_themes on startup.
const _userThemes = new Set();
export function registerUserThemes(names) {
  names.forEach((n) => _userThemes.add(n));
}

function safeThemeName(name) {
  const value = String(name || DEFAULT_THEME).trim();
  return THEME_NAME_RE.test(value) ? value : DEFAULT_THEME;
}

function themeHref(name) {
  return new URL(`themes/${safeThemeName(name)}.css`, window.location.href).href;
}

export function currentTheme() {
  const link = document.getElementById(THEME_LINK_ID);
  const style = document.getElementById(THEME_STYLE_ID);
  return (link?.dataset.theme || style?.dataset.theme) ?? DEFAULT_THEME;
}

function _applyBundledTheme(theme, bust) {
  // Remove any injected user-theme style.
  document.getElementById(THEME_STYLE_ID)?.remove();

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
}

async function _applyUserTheme(theme) {
  const result = await window.pywebview.api.get_theme_css(theme);
  if (!result?.ok) {
    console.warn("[theme] failed to load user theme:", theme, result?.error);
    return;
  }
  // Remove bundled <link> so the two don't fight.
  document.getElementById(THEME_LINK_ID)?.remove();

  let style = document.getElementById(THEME_STYLE_ID);
  if (!style) {
    style = document.createElement("style");
    style.id = THEME_STYLE_ID;
    document.head.appendChild(style);
  }
  style.dataset.theme = theme;
  style.textContent = result.css;
}

export function setTheme(name, { bust = false } = {}) {
  const theme = safeThemeName(name);
  if (_userThemes.has(theme)) {
    _applyUserTheme(theme);
  } else {
    _applyBundledTheme(theme, bust);
  }
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
