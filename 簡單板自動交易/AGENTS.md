# Project Rules

These rules apply to this project:

- The project root is `C:\Users\User\Documents\New project 5`.
- Only create, edit, move, or delete files inside this project root.
- Do not modify, move, rename, or delete files outside this project root.
- Treat any copied source project, Obsidian vault, credentials folder, or other external folder as read-only.
- If outside files are needed as references, only read them or copy their contents into this project root.
- Before any recursive delete or move, verify the resolved absolute target path is inside this project root.
- Keep generated runtime files inside this project root, preferably under `data/`, `reports/`, or `tests/_tmp/`.
- The simple order mode is SinoPac / Shioaji only for now. Do not add multi-broker abstractions until explicitly requested.
- The simple `buy`, `sell`, and JSON `order` commands must default to simulation-only behavior unless the user explicitly asks to design live-submit support.
- Other trading models should integrate through order-intent files handled by `python run.py model-orders --file ...`.
