"""Get a Moodle web service token with your own login.

Two ways to use it:

A) Yourself, in a terminal (setup.sh does this for you):

       .venv/bin/python get_token.py https://moodle.example.edu

   It asks how you log in (university/Microsoft login in the browser, or
   username and password) and prints the config for Claude.

B) Through Claude Code, which installs everything for you (see
   INSTALL_WITH_CLAUDE.md). Claude runs:

       get_token.py https://moodle.example.edu --login

   It opens the Moodle login in your browser. You log in, then right-click
   "Continue" and copy the link. The script notices the link in the
   clipboard, checks the token, clears the clipboard and adds the server to
   Claude. The token is never printed, so it doesn't end up in the chat.
   (--start and --finish run the same thing in two separate steps.)

The browser login uses the same flow as the official Moodle app, so it works
wherever the Moodle app works with your university login.
"""

import argparse
import base64
import hashlib
import json
import os
import platform
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlencode

import httpx

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server.py"
PENDING = HERE / ".login-pending.json"  # url + passport between --start and --finish, no secrets


def launch_url(url: str, passport: str) -> str:
    # confirmed=1 makes Moodle show a page with a "Continue" link to
    # moodlemobile://token=… instead of redirecting there, so the link can be copied.
    return f"{url}/admin/tool/mobile/launch.php?" + urlencode(
        {"service": "moodle_mobile_app", "passport": passport, "urlscheme": "moodlemobile", "confirmed": 1}
    )


def decode_launch_url(pasted: str, url: str, passport: str | None) -> tuple[str, bool]:
    """Read the token from moodlemobile://token=<base64 of signature:::token[:::privatetoken]>.

    Returns the token and whether the signature matches this login.
    """
    pasted = pasted.strip().strip("'\"")
    if "token=" not in pasted:
        raise ValueError("That is not a moodlemobile://token=… link.")
    encoded = unquote(pasted.split("token=", 1)[1]).split("'")[0].split("&")[0].split()[0]
    encoded += "=" * (-len(encoded) % 4)
    try:
        signature, token = base64.b64decode(encoded).decode().split(":::")[:2]
    except ValueError:
        raise ValueError("Could not read the link. Was it copied completely?") from None
    # Moodle signs the token with md5(wwwroot + passport); this checks the link
    # really came from this login and this site.
    expected = {hashlib.md5((u + (passport or "")).encode()).hexdigest() for u in (url, url + "/")}
    return token, signature in expected


def password_login(url: str) -> str:
    import getpass

    username = input("Moodle username: ")
    password = getpass.getpass("Moodle password: ")
    resp = httpx.post(
        f"{url}/login/token.php",
        data={"username": username, "password": password, "service": "moodle_mobile_app"},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        sys.exit(f"Error: {data.get('error', data)}")
    return data["token"]


def check_token(url: str, token: str) -> dict | None:
    """Return the site info if Moodle accepts the token, else None (and print why)."""
    try:
        resp = httpx.post(
            f"{url}/webservice/rest/server.php",
            data={"wstoken": token, "wsfunction": "core_webservice_get_site_info", "moodlewsrestformat": "json"},
            timeout=30,
            follow_redirects=True,
        )
        info = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Could not check the token: {exc}")
        return None
    if "exception" in info:
        print(f"Moodle rejected the token: {info.get('message')}")
        return None
    return info


# ---------- Clipboard ----------

def _run(cmd: list[str], data: str | None = None) -> str | None:
    try:
        out = subprocess.run(cmd, input=data, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def read_clipboard() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return _run(["pbpaste"])
    if system == "Windows":
        return _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])
    for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b", "-o"]):
        if shutil.which(cmd[0]):
            text = _run(cmd)
            if text is not None:
                return text
    return None


def clear_clipboard() -> None:
    system = platform.system()
    if system == "Darwin":
        _run(["pbcopy"], "")
    elif system == "Windows":
        _run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"])
    elif shutil.which("wl-copy"):
        _run(["wl-copy", "--clear"])
    elif shutil.which("xclip"):
        _run(["xclip", "-selection", "clipboard"], "")
    elif shutil.which("xsel"):
        _run(["xsel", "-b", "-c"])


# ---------- Adding the server to Claude ----------

def server_config(url: str, token: str) -> dict:
    return {"command": sys.executable, "args": [str(SERVER)], "env": {"MOODLE_URL": url, "MOODLE_TOKEN": token}}


def desktop_config_path() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Claude"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Claude"
    else:
        base = Path.home() / ".config" / "Claude"
    return base / "claude_desktop_config.json" if base.is_dir() else None


def add_to_desktop(url: str, token: str) -> str:
    path = desktop_config_path()
    if path is None:
        return "Claude desktop app: not found, skipped."
    config: dict = {}
    if path.exists() and path.read_text(encoding="utf-8").strip():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return f"Claude desktop app: {path} is not valid JSON, so it was left alone. Fix it and run --finish again."
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    config.setdefault("mcpServers", {})["moodle"] = server_config(url, token)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return f"Claude desktop app: added to {path}. Quit and reopen Claude to load it."


def find_claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for p in (Path.home() / ".local" / "bin" / "claude", Path.home() / ".claude" / "local" / "claude"):
        if p.exists():
            return str(p)
    return None


def add_to_claude_code(url: str, token: str) -> str:
    cli = find_claude_cli()
    if cli is None:
        return "Claude Code: the claude command was not found, skipped."
    # Replace an older moodle entry if there is one.
    subprocess.run([cli, "mcp", "remove", "moodle", "--scope", "user"], capture_output=True, text=True)
    res = subprocess.run(
        [cli, "mcp", "add-json", "moodle", json.dumps({"type": "stdio", **server_config(url, token)}), "--scope", "user"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        # Never echo the command: it contains the token.
        return "Claude Code: adding the server failed: " + (res.stderr or res.stdout).replace(token, "<token>").strip()
    return "Claude Code: added for your user. Start a new session (or /mcp → Reconnect) to load it."


# ---------- Modes ----------

def normalise(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        url = "https://" + url.removeprefix("http://")
    return url


def start(url: str, open_browser: bool) -> None:
    passport = secrets.token_hex(8)
    PENDING.write_text(json.dumps({"url": url, "passport": passport}))
    link = launch_url(url, passport)
    if open_browser:
        webbrowser.open(link)
    print(
        "Opened the Moodle login in the browser.\n"
        "  1. Log in to Moodle (university or Microsoft login).\n"
        "  2. A page with a \"Continue\" button appears. Don't click it: right-click it and copy the link.\n"
        "If the login ends on the Moodle dashboard instead, open this address again:\n"
        f"  {link}"
    )


def wait_for_link(minutes: float) -> str:
    """Wait until a new moodlemobile:// link shows up in the clipboard."""
    before = read_clipboard()
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        text = read_clipboard()
        if text and text != before and "moodlemobile://token=" in text:
            return text
        time.sleep(1)
    sys.exit(f"No Moodle link was copied within {minutes:g} minutes. Run --login again.")


def login(url: str, open_browser: bool, minutes: float, target: str) -> None:
    start(url, open_browser)
    print(f"\nWaiting up to {minutes:g} minutes for the copied link …", flush=True)
    finish(url, wait_for_link(minutes), target)


def finish(url: str, link: str | None, target: str) -> None:
    pending = json.loads(PENDING.read_text()) if PENDING.exists() else {}
    if link is None:
        link = read_clipboard()
        if not link:
            sys.exit("The clipboard is empty or can't be read. Ask the user to copy the Continue link again.")
    try:
        token, signed = decode_launch_url(link, url, pending.get("passport"))
    except ValueError as exc:
        sys.exit(f"{exc} Ask the user to right-click Continue → Copy Link Address again.")
    info = check_token(url, token)
    if info is None:
        sys.exit("The token doesn't work. Run --start again for a fresh login.")
    clear_clipboard()
    PENDING.unlink(missing_ok=True)
    print(f"Logged in as {info.get('fullname')} at {info.get('sitename')}.")
    if not signed:
        print("(The link came from a different login attempt, but Moodle accepts the token.)")
    if target in ("auto", "desktop"):
        print(add_to_desktop(url, token))
    if target in ("auto", "code"):
        print(add_to_claude_code(url, token))
    print("The token was stored only in the Claude config and not printed.")


def print_setup(url: str, token: str) -> None:
    """Print ready-to-paste commands with this folder's absolute paths."""
    cli = " ".join(
        shlex.quote(p)
        for p in ["claude", "mcp", "add", "moodle", "--scope", "user",
                  "-e", f"MOODLE_URL={url}", "-e", f"MOODLE_TOKEN={token}", "--", sys.executable, str(SERVER)]
    )
    desktop = json.dumps({"moodle": server_config(url, token)}, indent=2)
    print(
        "\n────────── Add it to Claude ──────────\n"
        "\nClaude Code: run this in your terminal\n\n"
        f"  {cli}\n"
        "\nClaude desktop app: Settings → Developer → Edit Config, then add this\n"
        "inside \"mcpServers\": { … } and restart Claude\n\n"
        f"{desktop[2:-2]}\n"
        "\nThe token gives read access to your Moodle account. Treat it like a password\n"
        "and don't share these lines.\n"
    )


def interactive(url: str) -> None:
    choice = input(
        "How do you log in to Moodle?\n"
        "  1 = Microsoft / university login in the browser\n"
        "  2 = Moodle username and password\n"
        "Choice [1]: "
    ).strip() or "1"
    if choice == "2":
        token = password_login(url)
    else:
        passport = secrets.token_hex(8)
        link = launch_url(url, passport)
        print(
            "\nHow it works:\n"
            "  1. Moodle opens in your browser. Log in with your university or\n"
            "     Microsoft account (if you aren't logged in already).\n"
        )
        input("Press Enter to open Moodle … ")
        webbrowser.open(url)
        input("\nLogged in? Press Enter … ")
        print(
            "\n  2. A page with a \"Continue\" button opens next.\n"
            "     Do NOT click it. Right-click it → \"Copy Link Address\".\n"
            "     The link starts with moodlemobile://token=\n"
            f"\nIf the browser doesn't open, visit this address yourself:\n  {link}\n"
        )
        webbrowser.open(link)
        pasted = input("\nPaste the moodlemobile:// link here: ")
        try:
            token, signed = decode_launch_url(pasted, url, passport)
        except ValueError as exc:
            sys.exit(str(exc))
        if not signed:
            print("\nNote: the link comes from a different login attempt. Checking the token anyway.")
    info = check_token(url, token)
    if info:
        print(f"\nToken works: logged in as {info.get('fullname')} at {info.get('sitename')}.")
        print_setup(url, token)
    else:
        print(f"\nMOODLE_TOKEN={token}\n\nTreat the token like a password.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Get a Moodle token and add the Moodle MCP server to Claude.")
    parser.add_argument("url", help="Your Moodle address, e.g. https://moodle.my-university.edu")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--login", action="store_true",
                      help="open the Moodle login, wait for the copied link and add the server to Claude")
    mode.add_argument("--start", action="store_true", help="step 1: open the Moodle login in the browser")
    mode.add_argument("--finish", action="store_true", help="step 2: read the copied link and add the server to Claude")
    parser.add_argument("--no-browser", action="store_true", help="with --login/--start: only print the login address")
    parser.add_argument("--minutes", type=float, default=8, help="with --login: how long to wait for the link (default 8)")
    parser.add_argument("--link", help="with --finish: the moodlemobile:// link instead of reading the clipboard")
    parser.add_argument("--target", choices=["auto", "desktop", "code", "none"], default="auto",
                        help="with --login/--finish: where to add the server (default: every Claude app found)")
    args = parser.parse_args()
    url = normalise(args.url)
    if args.login:
        login(url, not args.no_browser, args.minutes, args.target)
    elif args.start:
        start(url, not args.no_browser)
    elif args.finish:
        finish(url, args.link, args.target)
    else:
        interactive(url)


if __name__ == "__main__":
    main()
