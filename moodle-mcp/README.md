# Moodle MCP

An MCP server that lets Claude read your Moodle: courses, files, grades, deadlines, feedback, forums and messages. Ask *"What's due this week?"* or *"Download all slides from Week 3"* and Claude looks it up for you.

It uses Moodle's official web service API, the same one the Moodle mobile app uses. It only reads: it never submits assignments, posts in forums or sends messages. Tested with Nova SBE's Moodle, and it should work with any Moodle site that allows the Moodle app.

## What you can ask

- *"What's new in my courses since last week?"*
- *"Which assignments are due in the next 14 days, and have I submitted them?"*
- *"Show my grades in Corporate Finance."*
- *"What feedback did I get on my last assignment?"*
- *"Download all files from Week 2 of Marketing."*
- *"Summarise the latest announcements in all my courses."*

## Tools

| Tool | What it does |
|---|---|
| `site_info` | Checks the connection (site and user) |
| `list_courses` | Your enrolled courses with their ids |
| `get_course_contents` | Sections, activities and files of a course |
| `whats_new` | New or changed files, activities and forum posts in the last days (default 7), across all current courses |
| `list_assignments` | Assignments with due date and submission status |
| `assignment_feedback` | Grades, comments and feedback files for graded assignments |
| `quiz_results` | Quiz attempts and grades |
| `quiz_attempt_review` | Questions of a finished attempt, right or wrong (if the teacher allows reviews) |
| `upcoming_deadlines` | Deadlines from the Moodle timeline |
| `calendar_events` | Calendar events |
| `get_grades` | Your grades in a course |
| `course_progress` | Which activities Moodle marks as done |
| `list_forum_posts` | Latest discussions and announcements in a course |
| `read_discussion` | All posts of a discussion |
| `read_page` | Text of pages, books, labels and links |
| `notifications` | Moodle notifications (new posts, grades, reminders) |
| `list_messages` | Your Moodle conversations |
| `read_conversation` | Messages of one conversation |
| `search_courses` | Search all courses on the site |
| `download_file` | Download one course file to `~/Downloads/Moodle` |
| `download_files` | Download a whole section (e.g. "Week 2"), folder or course into `~/Downloads/Moodle/<Course>/<Section>/…`. Files you already have are skipped |

## Install

You need **Claude Code**: the **Code** tab in the Claude desktop app, or `claude` in a terminal. Regular chats can't install software on your computer.

1. Open Claude Code and paste this, with your own Moodle address if you're not at Nova SBE:

   ```
   Install the Moodle MCP server for me by following https://raw.githubusercontent.com/EmreAI/claude-mcp/main/moodle-mcp/INSTALL_WITH_CLAUDE.md. My Moodle is https://moodle.novasbe.pt
   ```

2. Claude installs everything and opens Moodle in your browser. Log in, then on the page with the **Continue** button, don't click it (it's meant for the Moodle phone app): right-click it and copy the link. That's it. Claude notices the copied link, finishes the setup and clears your clipboard, so your login never appears in the chat.
3. Quit and reopen the Claude desktop app (or start a new Claude Code session) and ask: *"What's due in Moodle this week?"*

Claude follows [INSTALL_WITH_CLAUDE.md](INSTALL_WITH_CLAUDE.md). The server needs Python 3.10 or newer; Claude checks and asks before installing it.

If there's no **Continue** page or the login fails, your university has probably turned off access for the Moodle app. Look in Moodle under *Profile → Preferences → Security keys*, or ask Moodle support.

## Your token

The token gives read access to your Moodle account. Treat it like a password.

- It's stored only in your local Claude config, never in this folder.
- The server only sends it to the Moodle address you set, and only downloads files from that site.
- Don't share your Claude config. If a token leaks, reset it in Moodle under *Profile → Preferences → Security keys*.

## Settings

| Variable | Meaning |
|---|---|
| `MOODLE_URL` | Your Moodle address, e.g. `https://moodle.your-university.edu` |
| `MOODLE_TOKEN` | Your token, set by the installer |
| `MOODLE_DOWNLOAD_DIR` | Optional download folder (default `~/Downloads/Moodle`) |

After changing `server.py`, restart the server: open a new chat, or in Claude Code run `/mcp` → moodle → Reconnect.

## How it's built

```
moodle-mcp/
├── server.py                 the MCP server and its 21 tools
├── get_token.py              logs you in and adds the server to Claude
├── INSTALL_WITH_CLAUDE.md    the steps Claude follows to install it
└── requirements.txt          mcp and httpx
```
