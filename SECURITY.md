# Security and publication

## Credentials and private data

Camera credentials and Telegram bot tokens belong in macOS Keychain or the process environment. `config.local.json` can contain private network addresses, bot usernames and chat IDs even when it contains no password. Camera photos, pairing records, installation notes and reference files are local data and must not be committed.

The owner-approved screenshot at `docs/images/telegram-check.png` is a deliberate public documentation illustration. This exception does not allow other camera images or diagnostic bundles.

The application uses generic camera/network error messages to avoid exposing credentials in failed request URLs. Detection is local; Telegram notifications send the actual photo to Telegram when enabled.

## Before publishing

Run `python3 scripts/check_public_files.py` on the staged snapshot. After committing, run `python3 scripts/check_public_files.py --revision HEAD --history` on the branch you intend to publish. Review the diff as well: the checker recognises common secret formats and local file patterns, not every possible private value.

Adding a path to `.gitignore` does not remove it from earlier commits. If sensitive data has already been committed, clean the affected history before publication. Revoke any exposed credential rather than treating a later deletion as revocation.

Report a security issue through a private channel. Include the affected code and reproduction steps with credentials and private camera images removed.
