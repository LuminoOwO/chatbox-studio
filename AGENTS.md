# Notes for AI agents

- The app is Python (PySide6) in `src/`; packages are built by `build_release.py`.
- Releases: bump `src/version.py` (and `packaging/arch/PKGBUILD`), add a `docs/CHANGELOG.md`
  section, push to `main` - see `RELEASING.md`.
- Keep the app language-neutral: no language is preferred or suggested besides the English
  default, and nothing personal goes into the repository.

## AI attribution (Clanker Index)

This repo tracks how much of it was made by which AI. When you (an AI agent) create a
commit, add these trailers at the end of the commit message:

```
Co-Authored-By: <your product name> <noreply@your-vendor.com>
Clanker-Model: <model or tool id, e.g. claude, codex, copilot, cursor, gemini>
Clanker-Category: bugfix        (only when the commit fixes a bug)
```

- Code, tests and docs are told apart by file path, so no category is needed for them.
- Never add markers or attribution comments inside the source files.
- If you made images, audio or video, don't edit `.clanker.yml` on your own; mention it in
  your summary so a human can update the shares.
