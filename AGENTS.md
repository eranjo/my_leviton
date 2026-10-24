# Agent instructions — my_leviton

## HACS releases (mandatory)

Whenever `custom_components/my_leviton/manifest.json` version is bumped for something the user should install:

1. Land commits, then **merge into `main`** (do not leave `main` stale).
2. On `main`, tag `v` + version (e.g. `1.1.0b10` → `v1.1.0b10`), push `main` + tag.
3. Publish a GitHub **pre-release** with `--target main` in the **same turn**.
4. Only then tell the user it is available in HACS (Show beta versions / reload if needed).

HACS does not see open PR branches. Never leave a version bump without a release tag on `main`.
