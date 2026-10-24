# Agent instructions — my_leviton

## HACS releases (mandatory)

Whenever `custom_components/my_leviton/manifest.json` version is bumped for something the user should install:

1. Merge/land on the beta delivery branch (`cursor/fix-duplicate-entities-0bc6` unless changed).
2. Tag `v` + version (e.g. `1.1.0b10` → `v1.1.0b10`), push the tag.
3. Publish a GitHub **pre-release** on that tag in the **same turn**.
4. Only then tell the user it is available in HACS.

HACS does not see open PR branches. Never leave a version bump without a release tag.
