# Chief of Staff

"Atlas, what am I forgetting this week?" — a deadline-aware rundown of the
commitments A.T.L.A.S. already holds.

## Works now (local, zero-token)

Aggregates and sorts by deadline:
- **Reminders** you've scheduled that fall within the next 7 days.
- **Notes** that read like commitments (contain words like *pay, bill,
  due, call, email, appointment, deadline*…).
- **A local calendar file** at `data/calendar.ics` if present (standard
  iCalendar; drop or sync one there — many calendar apps can export/publish
  an .ics you can copy to the Pi on a timer).

Nothing is ever sent, scheduled, submitted, or purchased. It reads and
reports only.

## Extensions (need approval + add on-demand model cost)

`aggregate_sources()` is source-agnostic — new sources append items in the
same shape. Two are planned but NOT wired, because each needs credentials
and would call the model when summarizing:

- **Live cloud calendar** (Google/Microsoft via CalDAV or an API token).
  Reading events is cheap; summarizing/prioritizing them uses the model
  on demand.
- **Email triage** (IMAP, read-only) to surface bills/deadlines. This is
  the meaningful token cost — summarizing an inbox per request. It would
  run only when you ask, never continuously.

Before either is enabled I'll estimate the per-request token cost and get
your explicit approval, per the operating rules. Draft preparation (reply
drafts, form fills) is also model-backed and stays approval-gated —
nothing leaves the Pi without your yes.
