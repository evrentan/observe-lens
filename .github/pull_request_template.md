## What and why

<!-- What does this change, and why is it needed? Link related issues. -->

## How I tested it

<!-- Commands you ran, platforms you tried, sample output (redacted). -->

## Checklist

- [ ] Scripts stay read-only and use only the Python standard library
- [ ] No real hostnames, IPs, tokens, company names or customer data
- [ ] Edited `common/_ol_common.py` (not the copies) and ran `python tools/sync_common.py`
- [ ] `sync_common.py --check`, `validate_skills.py`, `pytest` and `complexipy` pass locally
- [ ] Guard changes have allowed and refused test cases
- [ ] Docs (`SKILL.md`, `references/`, README) and `CHANGELOG.md` updated
