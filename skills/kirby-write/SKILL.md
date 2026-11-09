---
name: kirby-write
description: Create a new file that copies an existing reference file and write it straight to disk. Literal renames are applied in code with no model call. Other mechanical copies go to a cheap worker model (Claude Haiku by default). The result is a diff against the reference. Use for mirrored tests, fixtures, config entries, and stubs. Not for anything that needs a design decision.
---

# kirby-write

`kirby_write` makes a new file from a reference file and writes it to disk. It has two modes. Both return a diff against the reference, so the file itself stays out of your context.

## Literal copy with `substitutions`

Use this when the new file differs from the reference only by renames you can list.

```json
{"reference": ["tests/test_orders.py"], "target": "tests/test_users.py",
 "substitutions": [{"from": "Order", "to": "User"}, {"from": "orders", "to": "users"}]}
```

kirby applies the list in code, in one pass over the reference, with no model call. The copy is exact and takes milliseconds.

- Matching is case-sensitive. List each casing you need, such as `Order`, `orders`, and `ORDERS`.
- A string also matches inside longer words. `order` turns `border` into `buser`. The diff shows every changed line, so check it for matches you did not intend.
- Where two entries match at the same place, the longer one wins. No entry rewrites another entry's output.
- An entry that matches nothing stops the copy and writes no file. The error lists the count for every entry.
- Pass exactly one reference file, and do not pass `spec` as well.

From the shell:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/kirby.py" write --reference <file> --target <path> --sub Order User --sub orders users
```

## Worker copy with `spec`

Use this only when the change is mechanical but cannot be written as a substitution list, for example the same fixture with three date fields moved one day later. Pass `spec`, `reference`, `target`, and optionally `context`. The worker retypes the whole file, which takes seconds to minutes and can alter lines you did not mention.

- Spell out every change in the spec. "Make it work for users" is not a spec.
- The worker returns the task without writing when it would have to choose an approach. Do the work yourself when that happens.
- Write new logic, refactors, debugging, integrations, and security-sensitive code yourself.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/kirby.py" write --spec "<spec>" --reference <file> [--context <file> ...] --target <path> [--force]
```

## Review

Read the diff in the result. It lists changed lines only and stops at the read threshold. A diff longer than the changes you asked for means the worker did more than copy. Then run the relevant checks.

The target must not exist unless you pass `force`.
