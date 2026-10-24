# Triage Labels

The skills speak in terms of five canonical triage roles. Local implementation issues record the corresponding string in their `Status:` line.

| Canonical role | Local status | Meaning |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate this issue |
| `needs-info` | `needs-info` | Waiting on reporter for more information |
| `ready-for-agent` | `ready-for-agent` | Fully specified, ready for an agent |
| `ready-for-human` | `ready-for-human` | Requires human implementation |
| `wontfix` | `wontfix` | Will not be actioned |

When a skill mentions a triage role, use the corresponding local status from this table. New implementation issues awaiting evaluation start with `Status: needs-triage`.

Edit the Local status column to change the vocabulary. Wayfinding ticket states are defined separately in [issue-tracker.md](issue-tracker.md).
