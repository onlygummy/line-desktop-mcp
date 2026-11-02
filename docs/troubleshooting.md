# Troubleshooting

Fixes for the errors the server and its dependencies report.

## Common problems

- **`Node.js not found`.** Install Node.js 18 or newer, or set `LINE_NODE` to
  the executable path.
- **Login times out.** Call `login` again and scan promptly, or raise
  `timeout_sec` (up to 900).
- **LINE rejects the login (code `10201` and similar).** Delete the session file
  and log in from scratch with `login`.
- **No window appears.** The Python build lacks Tk. The QR is still written to
  `qr.txt` beside the session file, and the result reports `ui: "text"`.
- **`REQUEST_INVALID_HMAC` (code `10005`).** The bundled WASM or bridge could
  not sign. Make sure Node runs the package's own `okline/ltsm` files, and that
  the installed `okline` was not hand-modified.
- **Images missing from a result.** Check `images_failed` for the message ids
  that could not be fetched or were not images. The message itself is still in
  the archive.
- **The data directory keeps growing.** Expected: every chat listing and message
  read is stored. Delete the directory to reset it, or point
  `LINE_DESKTOP_MCP_HOME` elsewhere.
- **A result looks out of date.** `stale: true` means LINE was unreachable and
  the data came from the archive. Retry when the network is back.
- **A chat name in a group shows a mid prefix.** LINE does not put sender names
  in group messages. The server resolves the name when it can, and shows the mid
  when it cannot.

## Next steps

If none of these entries fits, read the [tool reference](tools.md) for the flags
that report the failure, or the [security policy](../SECURITY.md) for problems
with the session file and the QR window.
