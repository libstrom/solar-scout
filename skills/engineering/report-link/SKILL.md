# report-link

Deliver a generated file to the user and link it from **their** machine, not the
container. This session runs in a remote Linux box, so a path like
`/root/.claude/usage-data/report.html` is useless to Linus — he's on Windows and
opens files from his Downloads folder.

## When to use

- After `/insights` (or any command) prints a `file://…` path on the container.
- After exporting a CSV/XLSX, rendering a diagram, or saving a log the user wants.
- Any time you'd otherwise hand the user a container-local file path.

## Do it

1. **Send the file** so it reaches the user's machine:
   `SendUserFile` with the container path (e.g. `/root/.claude/usage-data/report-….html`).
2. **Link it from Downloads**, never the container path:
   ```
   file:///C:/Users/linus/Downloads/<filename>
   ```
   Use the file's basename, e.g.
   `file:///C:/Users/linus/Downloads/report-2026-05-25-224837.html`

## Notes

- Linus's Downloads folder is `C:/Users/linus/Downloads/`. Forward slashes work in
  a Windows `file://` URL.
- If the file doesn't auto-save there, he saves the sent file to Downloads and the
  link resolves.
