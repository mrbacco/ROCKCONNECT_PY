<!--
  File: MODERATION.md
  Author: mrbacco04@gmail.com
  Date: 2026-10-05
-->
# Running the community: moderation and privacy requests

## Two levels of staff
* **Admin**: everything below. Register normally, then on the server: `flask --app wsgi make-admin <username>` (`remove-admin` to undo).
  An **Admin** link appears in the menu bar.
* **Moderator**: the **Review** queue and the **Reports** only; can approve, hide or remove content but **cannot suspend or erase
  members, edit the blocked-words list or see the member list**. For volunteers you trust with the queue, not with the keys:
  `flask --app wsgi make-moderator <username>` (`remove-moderator` to undo). A **Moderate** link appears in their menu.

Everyone else gets a plain 404 at `/admin`. The menu shows how many items wait (open reports plus review items).

## Before anyone sees it: the review queue (Admin -> Review)
Posts, comments and gig comments can be **held** or **hidden** until staff have looked. Only the author still sees their own
item, with a notice ("waiting for a quick check"); everyone else, the gig board and the "gigs near me" search act as if it did not
exist. In the queue you can **Approve** (it goes up for everyone and any reports about it are closed as dismissed), **Remove**
(deleted with its photo), or, as an admin, **Remove and suspend author**. Everything is written to the moderation log.

Three things put an item in the queue:

1. **Blocked words** (Admin -> Blocked words). A list of words and phrases (up to 500) you edit yourself. It works in **any
   language**: it only compares text, so write the word or phrase the way people write it (for example the Italian blasphemy
   "porca troia", or Russian or Greek words). Capitals and accents are ignored and only whole words match, so "ass" does not catch
   "class". It also sees through the usual tricks: repeated letters ("porcaaa troia"), digits or symbols for letters
   ("p0rca tr0ia", "a$$"), and dashes, dots or underscores between the words of a phrase ("porca-troia"). It does **not** catch
   letters spread out ("p o r c a") or deliberate misspellings you have not listed.
   **Starter lists (many languages):** on that page one click adds a list; on the first start of a site the curated ones are added
   for you (`DEFAULT_WORD_LISTS`, default English, Italian, Spanish, French, German, Portuguese and Russian).
   * *Curated* (written for this project, low noise): English, Italian, Spanish, French, German, Portuguese, Russian. Profanity,
     insults and blasphemy ("porca troia", "me cago en dios", "puta que pariu"...).
   * *Broad* (opt-in, noisier): Arabic, Chinese, Czech, Danish, Dutch, Filipino, Finnish, Hindi, Hungarian, Japanese, Korean,
     Norwegian, Persian, Polish, Swedish, Thai, Turkish, from the open LDNOOBW project (CC BY 4.0, `rockconnect/wordlists_data/`).
     They lean towards sexual vocabulary and are volunteer-made: expect false matches and gaps, and read them before adding.
     Very short entries and entries that are everyday words are dropped on loading.
   * Chinese, Japanese, Korean and Thai have no spaces between words, so entries are found anywhere in the text. Languages that add
     endings to words (Turkish, Finnish, Hungarian, Polish...) match the forms that are listed, not every ending.
   * **Any other language:** add its words yourself on the same page; the filter compares text in any script.
   Nothing here is a complete dictionary: a list catches the common words, not every creative spelling, and what is offensive differs by
   community and country. A match never deletes anything, it only holds the post for a moderator, so an occasional false match costs one
   click. The author is told their post is waiting for a check, not which word; moderators see the word in the queue. Admins and
   moderators are never held. For real coverage of every language and of context (hate, threats, harassment) use a classifier in
   `review.screen()`; the word lists are the cheap, private first layer.
2. **New-member rules.** A member's first posts wait until `NEW_MEMBER_HOLD_POSTS` (default 3) of them were approved, and in the
   first `NEW_MEMBER_HOLD_HOURS` (default 24) of an account anything with a link or a photo waits (the usual spam pattern).
   Set either to `0` to switch it off. Nobody can send a photo or link to the whole site in their first minutes.
3. **Reports.** When `REPORTS_AUTOHIDE` (default 3) *different* members report the same post, comment or gig comment it is hidden at
   once, until staff decide. Reports from accounts younger than 24 hours do not count (one person with many accounts cannot hide
   things). Dismissing the reports, or approving the item, brings it back. Set `0` to switch off.

Private chat messages are **not** screened: they are private (see the privacy policy), and the people in them can block and report.
Profile texts and names are not screened either: use the report queue for those.

What this is not: there is no automatic image recognition. A photo from a trusted member is not checked unless someone reports it.
If you need that (for example because you let members upload many photos), add a screening service in `review.screen()`; it is
the one place every new post, comment and gig comment goes through.

## The report queue (Admin -> Reports)
Members report a post, a comment, a gig comment or a profile from the `...` menu / "Report" links. Each report keeps a copy of
the text, so you can still judge it after the author edits or deletes it. For every report you can:

* **Dismiss** - nothing wrong, content stays.
* **Remove content** - deletes the post (with its photo) or comment.
* **Remove and suspend author** (admins only) - same, and the author is signed out everywhere and cannot sign in again.

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
