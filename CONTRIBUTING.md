# Contributing

Thank you for helping improve this project. It is developed by
**MOTRES d.o.o.** and contributors and published under the **Apache License,
Version 2.0** (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). There is no
separate licence and no contributor licence agreement: under section 5 of the
Apache License your contribution is licensed under the same Apache-2.0 terms
as the rest of the project, and you keep your copyright.

## Developer Certificate of Origin (required)

Every commit in a pull request must be signed off under the
[Developer Certificate of Origin 1.1](DCO.md). The sign-off is a line at the end
of the commit message certifying that you wrote the change or otherwise have the
right to submit it under the project's open source licence:

```text
Signed-off-by: Your Name <you@example.com>
```

- Add it automatically with `git commit -s` (or `--signoff`). The name and
  e-mail must match the commit author (`git config user.name` /
  `git config user.email`); a real name is expected, not an alias.
- Forgot it? Fix the last commit with `git commit --amend -s --no-edit`, or all
  commits of your branch with `git rebase --signoff <base>`, then push again.
- If your employer has rights in what you write, make sure you are allowed to
  contribute it under the Apache License 2.0 before signing off.

The `DCO` check on every pull request fails if any commit lacks a sign-off
matching its author.

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
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
```

```ts
// SPDX-License-Identifier: Apache-2.0
// Copyright (C) MOTRES d.o.o. and contributors
```

## Dependencies

New runtime dependencies that load in the same process as our code must have
a permissive licence (MIT, BSD, Apache-2.0, ISC, Zlib, PSF, ...) or LGPL used as
a dynamically linked, replaceable library (the way Netgen is). GPL / AGPL code
may only run as a separate program that our code talks to over a pipe or files
(the way gmsh runs in `gmsh_worker_main`); it must never be imported by the
API / compute process. Packages with non-commercial, field-of-use or similar
restrictions are not accepted at all (Triangle was removed for this reason,
see [docs/MESHER_TRANSITION.md](docs/MESHER_TRANSITION.md)). Record every new
runtime dependency in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Proprietary accelerators may only be optional, with the code working
without them (the way `pypardiso` / Intel MKL is today).
