"""Conformance: SHACL profile packs and the validation of harvested records (Phase 11).

See ``docs/design/conformance.md``. A *pack* is a folder of SHACL shapes with
a manifest (:mod:`.packs`); the *vocabulary sets* give the shapes the EU
concepts' schemes and types (:mod:`.vocabularies`); :mod:`.validator` runs a
record against a pack.
"""
