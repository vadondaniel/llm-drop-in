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
- Keep one generated ZIP per package scope, with a clean versioned filename such as `project-head.zip`; older generated copies are removed after a successful replacement.

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
py -m pip install pyinstaller
pyinstaller --noconsole --onedir --name LLM-Drop-In main.py
```
