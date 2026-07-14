# DataGovernor raw-write allowlist policy

**Rule: shrink only.** Adding new governed-table raw writes is a regression.

## Current freeze (2026-07-14)

| Section | Count | Meaning |
|---|---|---|
| `apps/modal/workers` + `app.py` | **0** | Production path migrated to DataGovernor — any new hit fails CI |
| `apps/modal/scripts/*` | **21** | Ops/smoke tooling only; listed so `--strict` catches NEW script hits |

## CI command

```bash
cd ~/hiob-data
python3 -m hiob_data.audit_writes --strict --allowlist allowlist_raw_writes.txt \
  --root ~/hiob ~/hiob/apps/modal
# exit 0 required
```

## Honest limit

Static regex scan (including multi-line `.table/.from` + op chains and `.from()` API style).
Not a runtime proxy. Do not empty this file while scripts still contain raw writes.
