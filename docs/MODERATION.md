<!--
  File: MODERATION.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# Running the community: moderation and privacy requests

## Becoming an admin
Register normally, then on the server: `flask --app wsgi make-admin <username>` (`remove-admin` to undo).
An **admin** link appears in the menu bar. Everyone else gets a plain 404 at `/admin`.

## The report queue (Admin -> Reports)
Members report a post, a comment or a profile from the `...` menu / "Report" links. Each report keeps a copy of
the text, so you can still judge it after the author edits or deletes it. For every report you can:

* **Dismiss** - nothing wrong, content stays.
* **Remove content** - deletes the post (with its photo) or comment.
* **Remove and suspend author** - same, and the author is signed out everywhere and cannot sign in again.

All other open reports about the same item are closed together. Every action is written to the **moderation
log** on the overview page (who, what, when).

## Members (Admin -> Members)
Search by username, name or e-mail. **Suspend** (with a reason shown to the member when they try to sign in) hides
the member from the directory, the feed and the gig board; **Unsuspend** brings everything back. Admins can also
delete any post or comment straight from the feed (the x button). Admins themselves cannot be suspended or
erased here: run `remove-admin` first.

## Imported events with wrong data
Open an imported event (from "Gigs near you") and press **Hide this event** when the provider has it in the wrong place. It is
deleted and remembered, so it does not return at the next refresh. *Admin -> Hidden events* shows what was hidden and why, and
brings one back. A band's own Bandsintown dates cannot be hidden this way: the band manages them. Hiding is in the moderation log.

## Members protect themselves
* **Block** (profile or post menu): neither side sees the other's posts or comments, and they cannot message each
  other. Blocked people are listed on the Account page, where they can be unblocked.
* **Report** as above.

## Abuse protection that runs by itself
Sign-in failures (per address and per username), sign-ups, password-reset mails, posts, comments, messages, reports
and data exports are rate limited. Defaults are in `rockconnect/settings.py`; override with `RATE_LIMIT_<NAME>=count/seconds`.
A refused action shows a "Slow down" page (or a JSON error for the chat), with a `Retry-After` header.

## Privacy requests (GDPR)
Members can do both themselves on the **Account** page:

* **Download my data** - a zip with `data.json` (profile, posts, comments, likes, messages they sent, blocks, reports)
  and their photos. Messages written by other people are not included.
* **Delete my account** - erases the profile, posts, photos, comments, likes and their private conversations (for both
  sides). Reports about their content are closed and their copy of the text removed.

If someone e-mails you instead, find them under Admin -> Members and use **Erase** (it does the same thing). The
privacy policy tells members all of this, so keep the two in step if you change either.
