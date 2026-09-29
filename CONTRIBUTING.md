# Contributing

Thank you for helping improve this project. It is developed by
**MOTRES d.o.o.** and published under the **GNU Affero General Public License
v3.0 or later** (see [LICENSE](LICENSE)). MOTRES also offers the same code
under commercial licences; contact MOTRES d.o.o. for terms.

## Contributor License Agreement (required)

Every contributor must sign the [CLA](CLA.md) (CLA v1.0) before a pull request
can be merged. The CLA lets you keep your copyright while granting MOTRES the
rights to distribute your contribution under the AGPL and under commercial
licences.

- **Individuals:** open your pull request; the CLA Assistant bot will comment.
  Reply with `I have read the CLA Document and I hereby sign the CLA`.
- **Companies:** if your employer owns what you write, your employer signs the
  Entity CLA (Part B of [CLA.md](CLA.md)) and lists you in Schedule A; then
  sign as an individual through the bot as well.

Signatures are stored in `signatures/cla.json` on the `cla-signatures` branch
of this repository. This project uses the CLA; a Developer Certificate of
Origin (`Signed-off-by`) is not required.

## How to contribute

1. Open an issue first for anything larger than a small fix, so the approach
   can be agreed before you write code.
2. Fork, create a topic branch from the default branch, keep the change
   focused (one concern per pull request).
3. Add or update tests for what you change and run **only the tests that cover
   your change**, e.g. `python -m pytest -q tests/test_<area>.py -m "not slow"`.
   Tests marked `slow` run the real FEM solver and take minutes.
4. For the web client: `npm ci` in `web/`, then
   `npx tsc -p tsconfig.app.json --noEmit` and `npm run lint`.
5. Open the pull request with a short description of *what* and *why*.

## Code style

- Python >= 3.10, formatted with `black`, linted with `ruff`; type hints on new
  public functions.
- Validate user input loudly: an impossible machine must be rejected with a
  clear message, never solved silently.
- No hard-coded run settings: operating point, materials and mesh settings come
  from the caller / configuration.
- TypeScript: follow the existing ESLint configuration; keep UI text short.

## Licence headers

New source files start with an SPDX identifier (do not mass-edit existing
files):

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
```

```ts
// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) MOTRES d.o.o. and contributors
```

## Dependencies

New dependencies must have a licence compatible with AGPL-3.0-or-later **and**
with MOTRES's commercial licensing (permissive licences such as MIT, BSD,
Apache-2.0, ISC, Zlib are preferred). Record every new runtime dependency in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Packages with
non-commercial or field-of-use restrictions (for example `triangle`) may only
be optional extras with a fallback when absent.
