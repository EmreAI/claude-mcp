"""Get a Moodle web service token with your own login.

Run it yourself in a terminal (setup.sh does this for you):

    .venv/bin/python get_token.py https://moodle.example.edu

It offers two ways to log in:

1. Microsoft / university login (SSO) in your browser. This uses the same
   flow as the official Moodle app: you log in in the browser, and at the end
   the browser tries to open a "moodlemobile://token=..." link. You copy that
   link into this script, which reads the token from it.
2. Moodle username and password, typed here and sent only to your Moodle site.

Both need the Moodle mobile app service, which most universities enable
(if the Moodle app works with your university login, this works too).

At the end it prints the commands to add the server to Claude.
The token is only printed in your terminal, never saved or sent anywhere else.
"""

import base64
import hashlib
import json
import secrets
import shlex
import sys
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlencode

import httpx


def browser_login(url: str) -> str:
    passport = secrets.token_hex(8)
    # confirmed=1 makes Moodle show a page with a "Continue" link to
    # moodlemobile://token=… instead of redirecting there, so the link can be copied.
    launch = f"{url}/admin/tool/mobile/launch.php?" + urlencode(
        {"service": "moodle_mobile_app", "passport": passport, "urlscheme": "moodlemobile", "confirmed": 1}
    )
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
        f"\nIf the browser doesn't open, visit this address yourself:\n  {launch}\n"
    )
    webbrowser.open(launch)
    pasted = input("\nPaste the moodlemobile:// link here: ").strip().strip("'\"")
    return decode_launch_url(pasted, url, passport)


def decode_launch_url(pasted: str, url: str, passport: str) -> str:
    """Read the token from moodlemobile://token=<base64 of signature:::token[:::privatetoken]>."""
    if "token=" not in pasted:
        sys.exit("That is not a moodlemobile://token=… link.")
    encoded = unquote(pasted.split("token=", 1)[1]).split("'")[0].split("&")[0].split()[0]
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded).decode()
        signature, token = decoded.split(":::")[:2]
    except ValueError:
        sys.exit("Could not read the link. Was it copied completely?")
    # Moodle signs the token with md5(wwwroot + passport); this checks the link
    # really came from this login and this site.
    expected = {hashlib.md5((u + passport).encode()).hexdigest() for u in (url, url + "/")}
    if signature not in expected:
        print(
            "\nNote: the signature doesn't match this login. This happens when the Moodle\n"
            "address is slightly different (e.g. with or without www) or the link comes\n"
            "from an earlier attempt. Check the result below before using the token."
        )
    return token


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


def check_token(url: str, token: str) -> bool:
    try:
        resp = httpx.post(
            f"{url}/webservice/rest/server.php",
            data={"wstoken": token, "wsfunction": "core_webservice_get_site_info", "moodlewsrestformat": "json"},
            timeout=30,
            follow_redirects=True,
        )
        info = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"\nCould not check the token: {exc}")
        return False
    if "exception" in info:
        print(f"\nMoodle rejected the token: {info.get('message')}")
        return False
    print(f"\nToken works: logged in as {info.get('fullname')} at {info.get('sitename')}.")
    return True


def print_setup(url: str, token: str) -> None:
    """Print ready-to-paste commands with this folder's absolute paths."""
    python = sys.executable
    server = str(Path(__file__).resolve().with_name("server.py"))
    cli = " ".join(
        shlex.quote(p)
        for p in ["claude", "mcp", "add", "moodle", "--scope", "user",
                  "-e", f"MOODLE_URL={url}", "-e", f"MOODLE_TOKEN={token}", "--", python, server]
    )
    desktop = json.dumps(
        {"moodle": {"command": python, "args": [server], "env": {"MOODLE_URL": url, "MOODLE_TOKEN": token}}},
        indent=2,
    )
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


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: get_token.py <moodle-url>   e.g. get_token.py https://moodle.my-university.edu")
    url = sys.argv[1].strip().rstrip("/")
    if not url.startswith("https://"):
        url = "https://" + url.removeprefix("http://")
    choice = input(
        "How do you log in to Moodle?\n"
        "  1 = Microsoft / university login in the browser\n"
        "  2 = Moodle username and password\n"
        "Choice [1]: "
    ).strip() or "1"
    token = password_login(url) if choice == "2" else browser_login(url)
    if check_token(url, token):
        print_setup(url, token)
    else:
        print(f"\nMOODLE_TOKEN={token}\n\nTreat the token like a password.")


if __name__ == "__main__":
    main()
