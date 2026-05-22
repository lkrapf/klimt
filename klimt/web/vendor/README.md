# Vendored frontend dependencies

These libraries are vendored to keep Klimt self-contained and avoid runtime CDN
dependencies. Update them by replacing the files in place and bumping the
version numbers in this table.

| Library    | Version | License                  | Source |
|------------|---------|--------------------------|--------|
| marked     | 15.0.12 | MIT                      | https://github.com/markedjs/marked |
| DOMPurify  | 3.2.6   | Apache-2.0 OR MPL-2.0    | https://github.com/cure53/DOMPurify |
| highlight.js | 11.10.0 | BSD-3-Clause           | https://github.com/highlightjs/highlight.js |
| KaTeX      | 0.16.11 | MIT                      | https://github.com/KaTeX/KaTeX |
| KaTeX fonts | 0.16.11 | SIL OFL 1.1             | https://github.com/KaTeX/KaTeX |
| mermaid    | 11.6.0  | MIT                      | https://github.com/mermaid-js/mermaid |

License texts are in `LICENSES/`.

## Notes

- `katex.min.css` has been trimmed to reference only `.woff2` font files. The
  upstream CSS also lists `.woff` and `.ttf` fallbacks, which WebKit does not
  need. If you re-vendor KaTeX, rerun the trim or restore the missing files.
- `mermaid.min.js` is the UMD build, not the ESM build. It self-attaches to
  `window.mermaid`, which is what `render.js` expects.
