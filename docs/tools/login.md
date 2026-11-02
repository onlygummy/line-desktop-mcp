# login

```text
login(timeout_sec: float = 300, force: bool = False)
```

Call this tool first.

- When the saved session works, the call returns at once with
  `already_logged_in: true` and `ui: "none"`, and no window opens.
- Otherwise it opens a Tk window titled `Line Desktop MCP` in a child process,
  shows the QR code and any PIN LINE asks for, and waits.
- On success it saves the session file and returns `already_logged_in: false`
  with `ui: "window"`. A finished window closes itself after about a second.
- Closing the window stops the wait and saves nothing.
- `timeout_sec` is 10 to 900. `force=true` replaces a working session and always
  shows a fresh QR.
- One login runs at a time. A second concurrent call is refused.
- If the window cannot open (no local display, or a Python build without Tk),
  the QR is written to `qr.txt` beside the session file and the result reports
  `ui: "text"`. That file is deleted when the call ends, because the code is
  single-use.
- The window appears on the machine that runs the server, so a login cannot be
  completed from a different host over a non-local transport.
- Failures arrive as MCP tool errors (`ToolError`) instead of result data.

The result is `{ok, already_logged_in, profile{mid, display_name}, mid,
e2ee_ready, session_path, ui}`.
