# Installing the Moodle MCP server (instructions for Claude)

The user asked you to install this server for them. Do every step yourself in the shell and keep the user's part to logging in. Talk to the user in their language, briefly. Ask for their Moodle address if they didn't give it (Nova SBE: `https://moodle.novasbe.pt`).

Never ask the user to paste a token, password or `moodlemobile://` link into the chat, and never print the token. Step 4 reads the link from the clipboard so it stays out of the conversation.

Below, `$DIR` is the install folder: `~/moodle-mcp` on macOS and Linux, `%USERPROFILE%\moodle-mcp` on Windows. `$PY` is the folder's Python: `$DIR/.venv/bin/python` on macOS and Linux, `$DIR\.venv\Scripts\python.exe` on Windows.

## 1. Check Python

Python 3.10 or newer is needed. Try `python3 --version` (Windows: `py --version`). If it's missing or older, tell the user and offer to install it (macOS with Homebrew: `brew install python`; otherwise https://www.python.org/downloads/). Don't install anything without their OK.

## 2. Download

If `$DIR` already exists, it's an earlier install: replace `server.py`, `get_token.py`, `requirements.txt` and the docs with the new download and keep its `.venv`.

macOS and Linux:

```bash
curl -fsSL -o /tmp/moodle-mcp.zip https://github.com/EmreAI/claude-mcp/raw/main/dist/moodle-mcp.zip
unzip -oq /tmp/moodle-mcp.zip -d ~ && rm /tmp/moodle-mcp.zip
```

Windows (PowerShell):

```powershell
Invoke-WebRequest https://github.com/EmreAI/claude-mcp/raw/main/dist/moodle-mcp.zip -OutFile $env:TEMP\moodle-mcp.zip
Expand-Archive -Force $env:TEMP\moodle-mcp.zip $env:USERPROFILE; Remove-Item $env:TEMP\moodle-mcp.zip
```

## 3. Install

```bash
cd ~/moodle-mcp && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
```

Windows: `cd $env:USERPROFILE\moodle-mcp; py -m venv .venv; .venv\Scripts\pip install -q -r requirements.txt`

## 4. Log in

First tell the user, briefly, what is about to happen, because the next command waits for them:

1. Moodle opens in their browser. They log in as usual.
2. A page with a **Continue** button appears. They must **not** click it (it is meant for the Moodle phone app). Instead: right-click **Continue** → copy the link (**Copy Link Address** / **Copy link**).
3. That's all. The setup notices the copied link by itself, they don't need to reply.

Then run `$PY $DIR/get_token.py <MOODLE_URL> --login` with a timeout of about 10 minutes (it waits up to 8). It opens the login, waits until the link appears in the clipboard, checks the token with Moodle, clears the clipboard and adds the server to every Claude app it finds (the Claude desktop app and Claude Code). It prints who is logged in and where the server was added, never the token.

If it fails:

- **No link copied in time:** ask whether they clicked **Continue** instead of copying it, or ended up on the Moodle dashboard. Run `--login` again; if they are already logged in, the Continue page appears straight away.
- **The token doesn't work, or there's no Continue page at all:** the university may have turned off the Moodle app. Tell the user to check *Profile → Preferences → Security keys* in Moodle or ask Moodle support. Don't try other ways around it.
- **The Claude desktop app or Claude Code was not found:** tell the user what the script printed. They can run `$PY $DIR/get_token.py <MOODLE_URL>` in a terminal themselves, which prints the config to paste.
- **Your shell can't wait that long:** run `--start` instead, let the user copy the link and tell you, then run `--finish`, which reads the link from the clipboard.

## 5. Finish

Tell the user what the script printed and how to load the server:

- **Claude desktop app:** quit it completely and reopen it.
- **Claude Code:** start a new session, or run `/mcp` and reconnect `moodle`.

Then suggest a first question, for example *"What's due in Moodle this week?"* Files they download go to `~/Downloads/Moodle`.
