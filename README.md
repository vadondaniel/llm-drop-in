# LLM Drop-In

LLM Drop-In makes it easy to move project files between your computer and an LLM chat.

The usual workflow is:

**Project → ZIP → LLM → ZIP → Review → Project**

## Getting started

1. Open LLM Drop-In and choose the project folder you want to work on.
2. Click **Create ZIP** and choose what to package.
3. Drag the ZIP into your LLM chat and ask for the changes you want.
4. Download the ZIP the LLM gives you.
5. Drag that ZIP into LLM Drop-In, or click **Select LLM ZIP**.
6. Review the files and select the changes you want to bring into the project.
7. Click **Apply selected**.

You can use the ZIP exactly as it was downloaded. There is no need to unpack it first.

## Reviewing an LLM ZIP

Before anything is changed, LLM Drop-In shows you what the ZIP contains for the selected project.

You can see which files are new, changed, or unchanged, inspect individual changes, and choose which files to apply.

The app checks the ZIP before import and can warn you when it appears to belong to a different project. Invalid or incomplete ZIPs are not imported.

After an import, the operation appears under **Recent imports**. Imports that still have undo information can be reversed with **Undo selected import**.

## Watching downloads automatically

You can enable **Watch downloads** to have LLM Drop-In keep an eye on your Downloads folder, or another folder you choose, for new ZIPs.

When a new ZIP is downloaded, the app waits for the download to finish and can bring it into the same review flow as a ZIP you selected manually. It can warn you when it is not clear that the ZIP belongs to the current project.

Downloads that clearly belong to another project are ignored.

The download folder and monitoring setting are remembered between launches.

## Creating a ZIP for an LLM

Click **Create ZIP** for the usual project export. Use the arrow next to it to choose a different export.

You can choose between:

- **Working tree** — the files currently in the project, excluding files ignored by Git.
- **Working tree (include ignored files)** — the same export, but also includes files that Git normally ignores.
- **Staged** — the files you currently have staged in Git.
- **HEAD** — the latest committed version in Git.

The **include ignored files** option is useful when the LLM needs something that normally is not included in a project export, such as local configuration or generated files. Depending on the project and technology stack, it can also make the ZIP significantly larger. Git-ignored files can contain machine-local or sensitive data, so check what is included before sending it to an LLM.

Once the ZIP is created, it is available in the app as a draggable file, so you can drop it directly into a browser chat.

## Requirements

For everyday use, use the packaged Windows application so Python does not need to be installed separately.

To run the project from source, you need:

- Windows 10 or 11
- Python 3.10 or newer
- PySide6
- Git for the Git-related packaging options

Install the Python dependency with:

```powershell
py -m pip install PySide6
```

Then run:

```powershell
py main.py
```

## Developer guide

### Tests

Run the behavioral test suite with:

```powershell
py -m unittest discover -s core -p "test_*.py"
```

### Building the Windows application

Install the build dependencies:

```powershell
py -m pip install -r requirements-build.txt
```

Build with:

```powershell
pyinstaller --noconfirm --clean LLM-Drop-In.spec
```

The executable is created at:

```text
dist\LLM-Drop-In\LLM-Drop-In.exe
```
