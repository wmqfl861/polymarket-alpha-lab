# Redactions (FX-31 address hygiene, 2026-11-20)

FX-31 remediation of finding N-6 (2026-11-19 audit fix list, item FX-31;
signed 2026-11-16 decisions C2/5.3): tracked files that re-quoted the server
public address were redacted in place — one byte-precise occurrence each,
replaced with the `SERVER_HOST_REDACTED` placeholder per the FX-24 mirror
convention (`docs/paper-sim/server-mirror/REDACTIONS.md`). Original bytes
remain fixed by git history; pre-redaction sha256 below. Hashes and line
numbers only: this table never restates any address value. The companion
table covering the rerun log and the remediation MANIFEST itself lives in the
FX-31 section appended to `docs/remediation-evidence/2026-11-14/MANIFEST.md`.
The guard test `tests/test_tracked_public_address_hygiene.py` freezes the
post-redaction tracked stock (any new occurrence anywhere fails).

| file | original sha256 | redacted sha256 | line |
|---|---|---|---|
| docs/handoffs/m3-cleanup/codex-b0-pause-decisions-20261116.log | `0ba9d7820239070e05b553910d3bcdc9341cd5bd69e616716fa0b4f04bb17dcb` | `c7234a950139601f5f03a2d4bf544629e6c88c0b858855b46bbc7e06d6efe2e4` | 1266 |
| docs/remediation-evidence/2026-11-14/decision/m1-final-review-rerun-20261116.log | `e4d7a35de67833563d0fa17036291e0141a7a3d673138373b775b477930e1aa7` | `2c4e75d7317b90f526e269b47de7b24cee932b7e82aff532f6c22fe8a0ebf83f` | 329 |

Correction note (2026-11-20, FX-31 acceptance): the original-sha256 cell of the
rerun-log row above first reused the 62-character historical text
`e4d7a35de67833563d0fa17036291e0141a3d673138373b775b477930e1aa7`.
That text is a historical erroneous transcription, not a SHA-256: the real
digest now recorded above contains the two characters ("7a", 1-based
positions 36-37) that the transcription dropped. The misprint originates in
the frozen 2026-11-14 receipt pins (remediation-evidence MANIFEST.md row 60
and the DELIVERY_PLAN.md receipt row) and is retained unchanged there and in
the FX-31 section of the remediation MANIFEST (its line 152); no historical
file is edited for this correction. Recomputed authoritative values for the
rerun log: original git blob at HEAD
3e52516f71fdadeb86ef0d521521e6384ad9bb68 = 866313 bytes, sha256 as recorded
above (the historical 866313-byte pin is confirmed correct); redacted
working-tree bytes = 866321 bytes, sha256 as recorded above (unchanged by
this correction). First failure preserved verbatim:
fx31-second-source-digest-finding.json (independent verifier, 2026-11-20;
six focused guard tests passed, return code 0, before this correction; no
pinned handoff blob is affected). MANIFEST pair-3 chain unchanged:
pre-redaction 14005 bytes (sha256 8718a20b...), line-60-redacted pre-append
14013 bytes (sha256 7392598d..., self-reference convention per the MANIFEST
note), final retained state 15832 bytes (sha256 0f8a3284..., fixed by a
future git object, not embedded). No demonstration is rebuilt; no denied
operation is retried.
