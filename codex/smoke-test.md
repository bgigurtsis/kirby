# Live native-agent smoke test

This uses account allowance. Install Kirby, start a new Codex task in a scratch
workspace, and create `reference.py`:

```python
def double(value: int) -> int:
    """Return twice the input value."""
    return value * 2
```

Ask Codex:

> Use $kirby-luna's native Luna reader to describe reference.py and cite its
> lines. While it reads, inspect the scratch workspace's top-level file names.
> Then use the native Luna writer to create arithmetic.py containing typed triple
> and quadruple copies of reference.py. The writer must replace double/twice/2 with triple/three times/3 and quadruple/four times/4 respectively.
> While it writes, identify
> useful input cases for validating those functions. Review and check the result.

Review the tool calls. Workers should use `gpt-5.6-luna` with medium effort,
with useful main-model work alongside each bounded task. The reader should identify
the function at line 1 and multiplication at line 3. The writer should create
`arithmetic.py`. The main model should inspect the output and run:

```sh
python3 -c "from arithmetic import triple, quadruple; assert all(triple(v)==3*v and quadruple(v)==4*v for v in (-100,-2,-1,0,1,4,100,10**30)); print('16 checks passed')"
```

If native delegation or Luna is unavailable or blocked, expect a clear limitation
and targeted direct work. No automatic CLI retry, model substitution, or API-key
fallback should occur. Do not change approval controls to force a smoke test.

## Hook check

Create a file over the threshold and ask for it whole:

```sh
python3 -c "open('big.py','w').write('value = 1\n' * 250)"
```

> Print big.py with cat and tell me how many lines it has.

Expect the command to be denied with a reason that names `kirby_luna_bulk_reader`,
and expect Codex to spawn the reader instead of trying `head`, `sed`, or the Read
tool. If the read goes through, the hook is not trusted yet: open `/hooks`, trust
the Kirby entry, and start a new task. `head -n 20 big.py` should still be allowed.

## Optional CLI check

Run this separately only when you explicitly want to test the CLI and authorize
Luna to process the scratch source through your signed-in Codex CLI. Verify
ChatGPT sign-in with `codex login status`, then run from the checkout:

```sh
python3 codex/cli.py --workspace /path/to/scratch read --question "What does this function do? Cite its lines." --paths reference.py
python3 codex/cli.py --workspace /path/to/scratch write --spec "Copy reference twice: replace double/twice/2 with triple/three times/3 and quadruple/four times/4 respectively" --reference reference.py --target arithmetic_cli.py
```

Use a new output path, review the code, and apply the same input checks to the
generated module. A CLI result does not verify native routing.
