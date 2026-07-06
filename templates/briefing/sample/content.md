---
title: "Peer Review Pipeline — Week 3 Status"
date: "3 July 2026"
kicker: "Engineering briefing"
subtitle: "Migration off the legacy review queue continues on schedule."
stats:
  - label: "Reviews migrated"
    value: "142 of 180"
  - label: "Days elapsed"
    value: "18"
  - label: "Open blockers"
    value: "2"
sections:
  - heading: "What shipped this week"
    body: |
      - Cut the legacy queue over to read-only for the `payments` and
        `billing` repos; new reviews there now route through the pipeline.
      - Fixed the duplicate-notification bug that was paging reviewers
        twice for the same comment thread.
      - Backfilled 40 archived reviews so history stays searchable after
        the old queue is retired.
  - heading: "Risks and open questions"
    body: |
      The two remaining blockers are both in the `identity` repo, where a
      custom webhook still expects the legacy queue's payload shape. We
      need a decision from that team by **Friday** on whether to adapt the
      webhook or hold their repo back a week.
---
Migration is on track for the July 18 cutover. Of the eleven repos in
scope, nine have fully moved; the remaining two are gated on the
`identity` webhook issue described below.

This week's focus was closing out the notification bugs that came up
in week 2 and clearing the archived-review backlog so nothing gets
lost when the legacy queue goes read-only for good. Outstanding work
for next week:

- Resolve the `identity` webhook blocker (owner: platform team)
- Turn off write access to the legacy queue for the nine migrated repos
- Run the retirement checklist against the legacy queue's remaining
  read traffic

| Repo | Status | Reviews/week |
|---|---|---|
| payments | Migrated | 34 |
| billing | Migrated | 21 |
| identity | Blocked | 19 |
| checkout | Migrated | 28 |
