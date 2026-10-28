# Claude MCP

MCP servers that connect Claude to the tools you already use.

| Server | What it does | Download |
|---|---|---|
| [Moodle](moodle-mcp/) | Read your Moodle courses: what's new, deadlines, grades, feedback, forums and messages. Download a whole week's files in one go. | [ZIP](https://github.com/EmreAI/claude-mcp/raw/main/dist/moodle-mcp.zip) |

## Install

Each server folder has its own README with the setup steps. In short:

1. Download the server's ZIP from the table above and unzip it somewhere it can stay.
2. Run its setup (for Moodle: `sh moodle-mcp/setup.sh https://your-moodle`). It prints the config for Claude.
3. Add it to the **Claude desktop app** (*Settings → Developer → Edit Config*) or to **Claude Code** (`claude mcp add …`).
4. Start a new chat.

MCP servers run on your computer, so you need the Claude desktop app or Claude Code. They don't run in Claude on the web.

## Privacy

The servers keep no data of their own. Logins and tokens stay in your local Claude config and are never part of this repository.
