## Summary

- What changed:
- Why:

## Validation

- [ ] `PYTHONPATH=. pytest tests -q`
- [ ] `python scripts/release_preflight.py`
- [ ] `VERSION` updated when behavior or public release content changes
- [ ] `CHANGELOG.md` updated

## Public Data Safety

- [ ] No `.env`, API key, token, Feishu URL, private database or absolute local path
- [ ] Screenshots/GIFs use an isolated synthetic demo runtime
- [ ] No real business image or raw row-level CSV is included
- [ ] Remote model/vector calls are disabled in automated tests

## Agent Quality

- [ ] Model outputs keep their failure/skipped semantics
- [ ] External writes retain HITL confirmation and audit logging
- [ ] New metrics state their task, sample size and limitation
