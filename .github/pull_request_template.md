## Summary

<!-- What does this change do, and why? Reference the issue it resolves (Fixes #...). -->

## Changes

- Specification:
- Golden model:
- RTL:
- Tests:
- Documentation:

## Verification

- [ ] `make regs-check` (generated files up to date)
- [ ] `make lint`
- [ ] `make model`
- [ ] `make test` (RTL changes)
- [ ] `make regress` (larger RTL changes; seeds and result)
- [ ] New or changed behaviour is covered by a self-checking test against the golden model

## Checklist

- [ ] Every new source file has its SPDX identifier (`python3 scripts/spdx.py --check`)
- [ ] No generated file edited by hand (`make regs`)
- [ ] `CHANGELOG.md` updated under "Unreleased" for user-visible changes
- [ ] Third-party material listed in `THIRD_PARTY.md`
