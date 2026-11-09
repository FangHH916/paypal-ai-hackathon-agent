# Third-party code in this folder

`star-dashboard.js` (and its gzip copy) is built from `dashboard/` and bundles:

- AG Studio 3.0.0 (`ag-studio`, `ag-studio-react`), with AG Grid Enterprise 36.2 and AG Charts Enterprise 14.2:
  commercial software by AG Grid Ltd. Use needs an AG Studio licence; Star reads one from `AG_STUDIO_LICENSE` in the
  host's environment and never commits it. Without one, AG Studio shows a watermark.
- React 19.2.8 and React DOM 19.2.8 (MIT).

See each package's own licence in `dashboard/node_modules/<package>/` after `npm ci`.
