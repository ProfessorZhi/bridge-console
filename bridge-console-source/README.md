# Bridge Console

A small local control console for:

- Claude-to-IM bridge status
- Feishu / QQ shared default workdir
- Happy Codex workdir presets and launch entry
- Audit log and bridge log viewing

## Desktop Client

The desktop client now has two variants:

- `bridge_console_qt.py`: recommended modern Qt client
- `bridge_console_app.py`: older Tk client kept as fallback

Double click:

```text
start-bridge-console-client.bat
```

or run:

```powershell
cd <repo-root>
python bridge_console_qt.py
```

## Web Version

```powershell
cd <repo-root>
npm start
```

Then open:

```text
http://127.0.0.1:3210
```

## Notes

- Feishu and QQ share the same `CTI_DEFAULT_WORKDIR`.
- Happy Codex works by launch/session directory, so this console manages Happy through saved presets and one-click launch.
- The Qt desktop client is the recommended entry now if you do not want a browser UI.
- By default, the app looks for Claude-to-IM at `../Claude-to-IM-skill/Claude-to-IM-skill-source` relative to the workspace layout.
- You can also create `bridge-console.local.json` in the repo root to override local paths without editing code or setting environment variables.

## Local Config

Copy `bridge-console.config.example.json` to `bridge-console.local.json` and adjust it for your machine.

## Build

```powershell
cd <repo-root>
pyinstaller --clean --noconfirm ".\Bridge Console.spec"
```
