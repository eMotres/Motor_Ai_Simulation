# Module template

Copy this folder as `modules/<capability_kind>_<id>/` in the public
**`aerostator-modules`** repository (see [../MODULE_CONTRIBUTION_RULES.md](../MODULE_CONTRIBUTION_RULES.md)
section "Where modules live") and fill in every `TODO`. One module per pull request.

```text
aerostator-modules/
  modules/
    transmission_my_belt_drive/        <- one folder per module (this template)
      module.py                        the class: CAPABILITY, manifest(), run() -> IR
      module.json                      metadata read by the review tooling (below)
      test_module.py                   conformance gate + one check against your data
      PROVENANCE.md                    where every number comes from, in your words
      validation/
        SOURCE.md                      dataset origin, licence, how it was measured / quoted
        *.csv                          the data itself (text only, no binaries)
      requirements.lock                pinned runtime dependencies, free licences only
  .github/workflows/conformance.yml    copy of ci/conformance.yml
  README.md  LICENSE (Apache-2.0)  NOTICE  CODE_OF_CONDUCT.md
```

Try it before you open a PR (needs the core checkout on `PYTHONPATH`):

```text
set PYTHONPATH=<core repo>\src;.
python -m pytest -q test_module.py
```

`module.json` keys: `id`, `capability`, `version`, `author`, `licence` (must be
`Apache-2.0`), `agent` (`null` or `{"name": ..., "model": ...}` when an AI agent wrote
it), `requested_status` (`community`), `core_contracts_version`. The maintainers
set the real `status`; a pull request cannot grant itself `verified` or `integrated`.
