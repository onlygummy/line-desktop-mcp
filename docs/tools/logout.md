# logout

```text
logout()
```

- Asks LINE to invalidate the session, then deletes `session.json` and any
  `qr.txt`. `remote` reports `revoked`, `skipped`, or `failed`; the local file
  goes either way, because the stored token is the real risk. The phone stays
  logged in; only this server signs out.
- The archive and the image backups are kept, but every tool needs a `login`
  again before it can reach LINE.
- Safe to repeat (`already_logged_out: true`); refused while a `login` runs.

The result is `{ok, already_logged_out, session_deleted, remote, remote_error,
archive, next}`.
