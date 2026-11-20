"""D1 offline shared-capture preparation (2026-11-20 lane).

This subpackage is deliberately an EMPTY, lazy surface: it imports
nothing at package-import time and re-exports no names, so the FX-20
startup-module budget of ``polymarket_alpha_lab.papersim.cli``
(``tests/test_init_lazy_imports.py``) is untouched.  The operator tool
``tools/d1_compare.py`` imports the submodules explicitly.

Members (import explicitly, never from here):

- :mod:`.journal` -- capture-journal record types and the pure
  ``verify_journal`` / ``journal_digest`` functions.
- :mod:`.packbuild` -- pure journal-to-pack builder over the existing
  ``EntryRequest`` / replay-bundle codec and the B1 pack contract.
- :mod:`.frozenrun` -- staging, pinned-library materialization,
  sandbox-layout presentation, per-script override allow-lists and the
  recording urllib layer for the designated frozen chains.
- :mod:`.diff` -- candidate-by-candidate Path L vs Path N comparison
  with the three fixed difference classes.

Everything here is preparation code only: no network call, no database
connection, no timer, no managed write and no frozen-script execution is
performed by this node.  Run-time execution paths exist only behind the
plan section 5 run gates and are never exercised by this pass.
"""

__all__: list[str] = []
