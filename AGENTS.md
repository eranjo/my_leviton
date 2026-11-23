# Agent instructions — my_leviton

## HACS releases (mandatory)

Whenever `custom_components/my_leviton/manifest.json` version is bumped for something the user should install:

1. Land commits, then **merge into `main`** (do not leave `main` stale).
2. On `main`, tag `v` + version (e.g. `1.1.0b10` → `v1.1.0b10`), push `main` + tag.
3. Publish a GitHub **pre-release** with `--target main` in the **same turn**.
4. Only then tell the user it is available in HACS (Show beta versions / reload if needed).

HACS does not see open PR branches. Never leave a version bump without a release tag on `main`.

## Laws

Future edits must obey these laws. Do not put personal names, entity ids, or LAN addresses in this file or in tests. Office Ceiling / office_ceiling is the only public example name.

Before changing behavior:

- Name which laws the change touches, and whether a new law is required.
- Add or adjust a test for each touched law.
- A new or changed test must be shown to fail for a real violation before it is trusted. Run it once against code (or a temporary break you revert) that violates the law, record that it failed, then restore the code and show it passes. Do not add a tautology that cannot fail. Do not weaken a law to make a test pass.

1. The USB device is the 116A00-1 Omni-Bus Network USB Programming Interface (OB161, HAI/Leviton), VID `0x04D8` PID `0xFE93`. No other adapter.
2. One bus user at a time. Status listen and commands share one lock. A waiting local SET preempts a listen between short reads. A toggle must not wait for a full status sweep.
3. A Home Assistant tap sends SET immediately and returns. Rapid taps stay on that path. The quiet gap before readback stays about 0.45s. Do not lengthen the SET path to fix status sync.
4. For about 2s after a local SET, an older status frame must not overwrite that new state.
5. A status level is a percent 0–100. The light is on only when that percent is greater than 0, and off when it is 0. A missing frame is not off. Do not publish off at startup just because no frame has arrived. The USB command encoding is different: command byte `0x00` means full on, not off.
6. A listen ends on a quiet gap (on the order of 150–200ms with no bytes), not because some other light's level changed, and not by stretching a fixed extra second onto the toggle path.
7. The stable identity is the bus address (type, channel, device_id), format `my_leviton:{type:04x}:{channel:02x}:{device_id:04x}`. The room name is only a label. Entity-id stability is law 13. Do not treat "the entity id always stays unchanged" as the rule.
8. The device list lives in `.storage/my_leviton`. The example LIGHTS table must not replace an install that already has lights or a store. HACS replaces files inside custom_components, including constants.py, so user data must not live only there.
9. Packets use CRC-8/MAXIM.
10. A Home Assistant dimmer turned on with no brightness is full on only when the USB command argument is `0x00`. Last-level restore is a different command, `0x0A`, and is not the Home Assistant tap path. A wall paddle may still recall the last non-zero level through a later status frame. A status frame keeps the percent it carries; a reported level must not be rewritten to full.
11. A status frame belongs to one bus address. The level or wire name on a frame for one type, channel, and device_id must never be applied to a different light.
12. An update must not disable existing my_leviton entities and must not create the sample lights (Office Ceiling, Hallway Fan, and the rest) beside them. If `.storage/my_leviton` is missing, the old entity ids stay as they are. Discovery may add new lights. It must not retarget the old entity ids.
13. Once a device is stored by bus address, later updates keep that entity_id. When an update finds no stored addresses (the constants file inside the component was replaced and cannot be copied), discovery creates new entities and does not reuse the old entity ids. Automations then have to be pointed at the new ones.
14. A private home inventory never goes into the public repo. No real room names, entity ids, or bus maps from a home.
