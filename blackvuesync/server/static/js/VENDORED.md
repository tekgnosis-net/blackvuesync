# Vendored JavaScript libraries

| File | Library | Version | Source URL |
| --- | --- | --- | --- |
| `htmx.min.js` | HTMX | 2.0.6 | <https://unpkg.com/htmx.org@2.0.6/dist/htmx.min.js> |
| `alpine.min.js` | Alpine.js | 3.14.9 | <https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js> |

These files are checked in to avoid a CDN dependency at runtime.
To update, download the new version and replace the file; update the
version number in this table and in `pyproject.toml`'s comment (if any).
