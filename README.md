# LLM Drop-In

A small PySide6 Windows utility for dropping LLM-generated ZIPs onto a selected project and safely applying the files they contain.

## Features

- Select and remember a project folder.
- Drag/drop a ZIP or choose one from a file dialog.
- Detect common extra wrapper directories automatically.
- Match ZIP files to the selected project without flattening paths.
- Preview replacements/new files before applying.
- Never delete project files merely because they are absent from the ZIP.
- Remove Windows Mark-of-the-Web (`Zone.Identifier`) from imported files.
- Keep a local import history.
- Reject ambiguous paths instead of guessing.

## Requirements

- Windows 10/11
- Python 3.10+
- PySide6

Install:

```powershell
py -m pip install PySide6
```

Run:

```powershell
py main.py
```

## Optional build

With PyInstaller:

```powershell
py -m pip install pyinstaller
pyinstaller --noconsole --onedir --name LLM-Drop-In main.py
```
