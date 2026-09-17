# WP-05 paper receipt approval binding — unpublished candidate

Repository: wmqfl861/polymarket-alpha-lab
Required upstream base: a71ae4d716e8a73bd3614f080f42e5a6968d76f8
Required base tree: 4d15e1d555f108e19edf19e160593eea2841b28c
Expected candidate tree: ec17d9401fabdb6a3b7507b5324a4811070c7327

This is not merged or published to GitHub, not an installation, and not Windows
acceptance. The coordinator's connector had no write operations; no remote device
was available. The supplied local-agent task must FIRST publish the complete
verified packet to a retained GitHub handoff commit and read every file back.
Only then may the exact patch be fetched from that fixed GitHub commit into a
fresh SOURCE checkout. Do not apply directly from this archive to an existing
installation, .local, or business database. Stop if main has moved, a candidate
already exists, or any hash/tree check fails. No automatic rebase or force apply.

Intended handoff branch: handoff/wp05-paper-receipt-binding-20260917
Directory: handoffs/wp05-paper-receipt-binding-20260917/
Intended feature branch: feature/wp05-paper-receipt-binding-20260917
These are intended names, NOT claims that those refs already exist.

implementation.patch changes exactly six paths listed in source-manifest.json.
sources/ holds exact resulting bytes for review, not an alternative installation
method. candidate-identity.json distinguishes upstream identity from local Git
bookkeeping. REVIEW.md describes first failures, the fix, separate self-review,
actual local verification and limitations. The14new case IDs are recorded in
new-case-ids.json. evidence/ preserves raw logs and bounded metadata.

The code binds a capture receipt to the caller's original reviewed SHA256 rather
than an argument object a faulty adapter can alter. It neither repairs writes
nor changes capture/storage/simulation algorithms. Normal output is unchanged.
Native acceptance must use only GitHub CI-owned PostgreSQL: the enhanced original
paper-operator testcase verifies BTC/ETH same-input replay after adapter argument
mutation while original receipt/history remain. Native execution is not yet done.

Required next gates: all four existing final-head workflows, including every
native partition and its aggregate, paper and actual kit. Source-cleanliness,
raw JUnit/logs,14new Windows cases, and the entire enhanced native testcase must
pass. A printed PASS marker alone is not acceptance. Preserve failures; no rerun
to green, prewarm, larger deadlines, weaker tests or old-head substitution.

Other open work, including PR49 and observed parallel PR54/55, is not certified,
modified or merged here. This candidate is based only on the fixed current main;
if another PR changes main first, stop for an explicit integration review.
No credentials, user data, real model/market/order operations are authorized.
WP05/WP06 remain PARTIAL; G5/G6/V1 and D1-D3 remain open.

Before extraction verify the outer hash supplied in the forwardable prompt.
Then verify PAYLOAD-MANIFEST.json's independently supplied size/hash and exact
member set. Its files mapping covers every member except the manifest itself.
All paths must be relative POSIX paths, no duplicates, links or path traversal.
