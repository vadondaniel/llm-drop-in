# LLM Drop-In

A small PySide6 Windows utility for dropping LLM-generated ZIPs onto a selected project and safely applying the files they contain.

## Features

- Select and remember a project folder.
- Drag/drop a ZIP or choose one from a file dialog.
- Detect common extra wrapper directories automatically.
- Match ZIP files to the selected project without flattening paths.
- Preview modified/new/unchanged files before applying.
- Select individual files to import.
- Detect duplicate/case-colliding archive paths and unsafe Windows paths.
- Detect suspiciously large/compressed archives before extraction.
- Stage and verify every file before committing an import.
- Roll back already-committed files if a commit fails.
- Skip unchanged files rather than rewriting them.
- Never delete project files merely because they are absent from the ZIP.
- Keep a local import history.
- Run archive analysis in a worker thread so the UI stays responsive.
- Exclude files matched by Git ignore rules by default, with an option to include them.
- Create a project ZIP directly from the selected folder and drag the result into a browser chat.
- Package the current working tree, the staged Git index, or the last committed HEAD snapshot.
- Keep one generated ZIP per package scope, using stable names such as `project.zip`, `project-staged.zip`, and `project-head.zip`; replacement happens through a temporary archive so the previous package survives until the new one is complete.
- Optionally monitor a chosen downloads folder for newly downloaded ZIPs, wait for them to finish downloading and validate them before analysis, then open the normal import review for the current project.

## Requirements

- Windows 10/11
- Python 3.10+
- PySide6
- Git (used when available for Git ignore-rule matching)

Install:

```powershell
py -m pip install PySide6
```

Run:

```powershell
py main.py
```

Run tests:

```powershell
py -m unittest discover -s core -p "test_*.py"
```

## Optional build

With PyInstaller:

```powershell
py -m pip install -r requirements-build.txt
pyinstaller --noconfirm --clean LLM-Drop-In.spec
```

`LLM-Drop-In.spec` is the **single authoritative build configuration**. The spec uses PyInstaller's `SPECPATH` value to locate project files, so it works when the spec is executed normally by PyInstaller. There is deliberately no second build script to keep in sync with it. The resulting application is `dist\LLM-Drop-In\LLM-Drop-In.exe`.

The distribution uses **onedir** rather than onefile: startup is faster, the packaged files are inspectable when diagnosing a user report, and the application does not need to unpack itself into a temporary directory on every launch. UPX compression is intentionally disabled for a more predictable Qt/Windows build.

The spec also embeds `app.ico` into the executable and bundles a runtime copy beside it. The application sets a stable Windows AppUserModelID before creating `QApplication`, so Windows can associate the process with the LLM Drop-In icon on the taskbar.
