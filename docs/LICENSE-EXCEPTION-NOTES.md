# Notes on LICENSE-EXCEPTION (DRAFT; explanatory only, not part of the grant)

This file holds the precedents, drafting reasoning, evidence and mapping
that support the operative text in `LICENSE-EXCEPTION`. Nothing here is
itself a licence grant; if this file and `LICENSE-EXCEPTION` ever
disagree, `LICENSE-EXCEPTION` controls. Written 2026-09-30, revised the
same day after an independent review
(`docs/licensing-mkl-exception-review-2026-09-30-codex.md`) and again
after the owner's decision to drop Intel MKL/PARDISO from the default
image (Section 8).

## 1. Why a named-library form, not an open-ended one

The grant in `LICENSE-EXCEPTION` Section B names two specific Intel
Simplified-Software-License components (`mkl`, `tbb`) rather than "any
non-free numerical library." An earlier draft of this file also had a
second, functionally-bounded fallback clause ("a non-free numerical
library that implements the BLAS/LAPACK/PARDISO interfaces, used as a
drop-in replacement for MKL"); the independent review
(2026-09-30-codex, item 4) found it insufficiently bounded -- "the role
it fills today" has no fixed release or interface definition, and BLAS/
LAPACK API compatibility does not establish that a hypothetical
replacement is a drop-in replacement for pypardiso's specific MKL
Pardiso entry points. That clause has been removed. If a different
accelerator is adopted later, it should get its own, separately
reviewed, separately contributor-cleared, named addition -- the same way
each precedent below names its own library rather than writing a general
rule.

## 2. Precedents (verified 2026-09-30; not invented)

- **FSF's own GPLv3 section 7 template**, reproduced by SPDX as
  "GPL-3.0 Linking Exception (with Corresponding Source)":
  https://spdx.org/licenses/GPL-3.0-linking-source-exception.html
  (short form without the Corresponding Source sentence:
  https://spdx.org/licenses/GPL-3.0-linking-exception.html). Origin: the
  FSF's GPL FAQ, https://www.gnu.org/licenses/gpl-faq.html -- gnu.org
  pages timed out for this session's fetch tool both in the original
  drafting pass and in the independent review's pass (its "Sources
  checked" list notes the same); the wording is cross-verified via SPDX
  and via its real-world use in Leela Chess Zero below, not
  independently re-read from gnu.org. Counsel should pull the FAQ page
  directly.
  - **Material difference from our text, flagged by the independent
    review (item 5):** the SPDX template's sentence "Corresponding
    Source for a non-source form of such a combination shall include the
    source code for the parts of [name of library] used as well as that
    of the covered work" REQUIRES the named library's own source as part
    of Corresponding Source. `LICENSE-EXCEPTION` Section B does the
    opposite: it explicitly says this permission "does not require
    Corresponding Source for the Licensed Libraries themselves." That is
    deliberate here -- Intel MKL and Intel TBB are binary-only
    proprietary distributions with no source to give -- but it is a
    **substantive departure from the standard template, not a
    mechanical fill-in of its blanks**, and counsel should review it as
    such rather than assume it inherits the template's legal effect.
- **Leela Chess Zero -- NVIDIA CUDA/cuDNN exception** (closest verified
  real-world precedent: a GPLv3 compute-heavy project linking a
  proprietary vendor's numerical/GPU library). Live in
  https://github.com/LeelaChessZero/lc0/blob/master/src/main.cc :
  ```
  Additional permission under GNU GPL version 3 section 7

  If you modify this Program, or any covered work, by linking or
  combining it with NVIDIA Corporation's libraries from the NVIDIA CUDA
  Toolkit and the NVIDIA CUDA Deep Neural Network library (or a
  modified version of those libraries), containing parts covered by the
  terms of the respective license agreement, the licensors of this
  Program grant you additional permission to convey the resulting work.
  ```
  Before adding it, lc0's maintainers collected an explicit "I agree"
  from every contributor of copyrightable code, tracked in a public
  checklist: https://github.com/LeelaChessZero/lc0/issues/184 . The
  independent review (item 9) notes this precedent is about NVIDIA
  granting an exception to *their own* libraries' terms combined with
  lc0's GPL code -- structurally the same shape as MOTRES naming Intel's
  libraries, not a case of "granting an exception to their own
  library" in the GCC/Classpath sense (see below); it remains the
  closest match.
- **The "OpenSSL exception"** (older, GPLv2-era pattern; the most
  widely reused precedent for "a free program plus one named non-free
  library"):
  ```
  In addition, as a special exception, the copyright holders give
  permission to link the code of portions of this program with the
  OpenSSL library under certain conditions as described in each
  individual source file, and distribute linked combinations including
  the two. You must obey the GNU General Public License in all respects
  for all of the code used other than OpenSSL. If you modify file(s)
  with this exception, you may extend this exception to your version of
  the file(s), but you are not obligated to do so. If you do not wish to
  do so, delete this exception statement from your version. If you
  delete this exception statement from all source files in the program,
  then also delete it here.
  ```
  Source: https://scancode-licensedb.aboutcode.org/openssl-exception-gpl-2.0.html .
  Cited for the general pattern only. The independent review (item 9)
  points out OpenSSL was, at the time, a *free* (BSD-style) licence the
  FSF considered GPL-*incompatible*, not a non-free/proprietary library
  like Intel MKL -- a materially different situation from ours, and
  this predates GPLv3 section 7's cleaner mechanism.
- **Cited only for the general section-7 mechanism, not as a source of
  wording** (the independent review, item 9, notes both of these are the
  FSF/project *granting an exception to their own library's* copyleft
  terms so it can be linked into other code -- the reverse direction
  from MOTRES naming a *third party's* proprietary library):
  - GCC Runtime Library Exception -- https://www.gnu.org/licenses/gcc-exception-3.1.en.html
  - GNU Classpath linking exception -- https://www.gnu.org/software/classpath/license.html
  Neither page was re-fetched live this session (same gnu.org timeout
  noted above).
- **Checked and not cited, because the wording could not be verified**:
  GNU Octave (no documented BLAS/LAPACK linking exception found by
  search); FlexiBLAS (its actual `COPYING` file, read directly from the
  GitHub mirror, is plain unmodified LGPL-3.0, not a custom BLAS/LAPACK
  exception, despite some secondary sources describing it that way).
  OpenFOAM was not checked in detail; no search result surfaced an
  MKL/PARDISO exception for it and it is not relied on either way.

## 3. Gmsh has its own, separate, non-transferable exception

Gmsh (GPL-2.0-or-later) ships its own additional permission, verified
against https://gmsh.info/LICENSE.txt :

> "The copyright holders of Gmsh give you permission to combine Gmsh
> with code included in the standard release of Netgen (from Joachim
> Schöberl), METIS (from George Karypis at the University of
> Minnesota), OpenCASCADE (from Open CASCADE S.A.S) and ParaView (from
> Kitware, Inc.) under their respective licenses."

This names Netgen, METIS, OpenCASCADE and ParaView -- **not Intel MKL,
and not any numerical accelerator**. The independent review (item 1) is
correct that GPL-3.0/AGPL-3.0 compatibility (THIRD_PARTY_NOTICES.md note
1, "or later" + GPL-3.0 section 13) only settles that *gmsh's own
licence terms* combine cleanly with the AGPL Program; it says nothing
about MKL, and MOTRES holds no rights in gmsh to extend its exception to
a library gmsh's own copyright holders did not name. gmsh and MKL each
need their own, separate authorisation to be combined with the AGPL
Program; `LICENSE-EXCEPTION` only ever addressed the MKL side. See
`docs/MESHER_TRANSITION.md`, "Gmsh and MKL in the same process" for the
combination-specific analysis (now scoped to the optional PARDISO
backend only, since the default image no longer contains MKL -- Section
8 below).

## 4. Conveying vs. network use -- what changed in the redraft

The independent review (item 3) flagged that the original draft did not
clearly separate "conveying" (AGPL sections 3-6, e.g. shipping a Docker
image or installer) from "network use without conveying" (AGPL section
13, MOTRES's own hosted service), and that installing something on
MOTRES's own server is not itself evidence of distribution to a
customer, while a copyright owner licensing its own AGPL work is not in
the same posture as a recipient relying on this exception for someone
else's AGPL work. `LICENSE-EXCEPTION` Section C is the response: the
permission only ever changes what may be *conveyed together* (the
Licensed Libraries need not be included in Corresponding Source); it
does not touch section 13's requirement that a network user of a
modified version can still obtain the Corresponding Source of the
Program itself. Counsel should confirm this framing correctly threads
the distinction the review raised and does not, even inadvertently,
read as narrowing what MOTRES's own hosted-service users are owed under
section 13 for the Program's own code.

## 5. Contributor consent and the git-history audit -- what it does and doesn't show

The independent review (item 2) objected to "no external rights holders
exist" as an overreach: a commit-author identity shows who wrote
something, not who owns the copyright in it (borrowed/copied code,
employees or contractors of another company, and AI-assisted authorship
all need their own check), and the DCO is a certification of the right
to submit, not a copyright assignment. `LICENSE-EXCEPTION` Section G is
worded accordingly: "no other commit-author identities found," not "no
other rights holders."

Audit performed 2026-09-30, across every branch (`git log --all
--format='%ae'`):

| Author e-mail | Name | Commits | Relationship to MOTRES |
|---|---|---|---|
| vadim@motresres.com | Vadim Shcherbakov | 1,667 | Owner, MOTRES d.o.o. |
| noreply@anthropic.com | "Claude" | 17 | Claude Code / Claude Agent SDK sessions run by and under the direction of Vadim Shcherbakov acting for MOTRES d.o.o. |

This is evidence of authorship identities in the examined refs, not a
determination of copyright ownership or of code provenance (e.g.
whether any borrowed snippets, employer-owned prior work, or
contractor-authored code entered the tree by another route). Counsel
should confirm what further check, if any, is needed before relying on
"MOTRES holds the relevant copyright" as a premise for this permission,
and CONTRIBUTING.md is updated (per the review's item 2) so any future
contributor accepts the AGPL and this exception explicitly at
submission time, rather than deferring to a later re-clearance exercise
each time a new contributor's identity needs checking.

## 6. Inspecting exact installed licence files, not assumed metadata

The independent review (item 6) warned against treating the whole "MKL
chain" as one uniformly proprietary library, citing that oneTBB's
*upstream GitHub project* is available under Apache-2.0. Per-artifact
licence files were read read-only from the production container as it
stood on 2026-09-30 (image digest and build details in Section 8 below,
before the architecture decision to drop this chain from the default
image), via `dist-info/METADATA` and `dist-info/LICENSE.txt` inside
`deploy-api-1`:

| Package (PyPI) | Version | Offered licence (from the artifact itself) | Note |
|---|---|---|---|
| `mkl` | 2026.1.0 | Intel Simplified Software License (Oct 2022) | Binary redistribution permitted under stated conditions (no reverse engineering, reproduce notice); no clause found barring combination with copyleft or SaaS use. |
| `tbb` | 2023.1.0 | Intel Simplified Software License (Oct 2022) | **Not** the Apache-2.0 that covers the separate github.com/uxlfoundation/oneTBB source project (https://github.com/uxlfoundation/oneTBB/blob/master/LICENSE.txt) -- this is a different offered licence for a different (Intel-built binary) artifact. Same terms as `mkl` above. |
| `intel-openmp` | 2026.1.2 | Intel End User License Agreement for Developer Tools (Aug 2024) | Materially different and more restrictive than the Simplified licence: section 3.1(x) prohibits linking/distributing so that any part "becomes Reciprocal Open Source Software"; 3.1(xi) prohibits SaaS/service-bureau use. See `LICENSE-EXCEPTION` Section D. |
| `intel-cmplr-lib-ur` | 2026.1.2 | Intel End User License Agreement for Developer Tools (Aug 2024) | Same agreement and same 3.1(x)/(xi) clauses as `intel-openmp`, confirmed by reading its own `LICENSE.txt`, not assumed. |
| `umf` (oneAPI Unified Memory Framework) | 1.1.0 | **Apache-2.0 with LLVM exceptions** | Not proprietary at all -- this one was previously (incorrectly) lumped in with "the MKL chain" as proprietary in an earlier pass of THIRD_PARTY_NOTICES.md. It is AGPL-compatible on its own and needs no permission. |
| `tcmlib` | 1.5.0 | Intel Simplified Software License (Oct 2022) | Same terms as `mkl`/`tbb`. |
| `onemkl-license` | 2026.1.0 | Intel Simplified Software License (Oct 2022) | A licence-metadata package (records acceptance/entitlement), not itself a numerical runtime; still under the Simplified licence per its own `LICENSE.txt`. |
| `triangle` | 20250106 (wraps Triangle 1.6) | Wrapper: LGPL-3.0 (per its own `dist-info/METADATA`); bundled C code: see Section 7. | |

Only `intel-openmp` and `intel-cmplr-lib-ur` carry the Reciprocal-Open-
Source/SaaS restriction; `mkl`, `tbb`, `tcmlib` and `onemkl-license` do
not appear to, on the text read. `umf` needs no permission at all. This
supersedes any earlier wording in THIRD_PARTY_NOTICES.md that treated
"Intel MKL (via pypardiso)" as one uniform proprietary block.

## 7. Where this text lives

| Location | Change | Rationale |
|---|---|---|
| `LICENSE` | Left unmodified. | Matches the existing decision (PR #40) to keep the official AGPL-3.0 text unedited; a section 7 additional permission is a separate grant and does not require editing the licence text it supplements -- neither Leela Chess Zero nor the FSF's own GCC/Classpath exceptions edit `COPYING`. |
| `LICENSE-EXCEPTION` (repo root) | Full operative grant, kept short; marked DRAFT. | Canonical, single source of the actual permission. |
| `docs/LICENSE-EXCEPTION-NOTES.md` (this file) | Precedents, evidence, drafting reasoning. | Keeps the operative text short and readable, per the independent review's item 9 ("keep this historical analysis outside the operative licence file"). |
| `README.md` "License" section | One sentence + link. | First place a reader looks. |
| `CONTRIBUTING.md` "Dependencies" section | Updated wording; points here; a future contributor accepts AGPL + this exception at submission. | Addresses the review's item 2. |
| `THIRD_PARTY_NOTICES.md` | Corrected per-artifact table (Section 6); default-image dependency table rewritten for the new default solvers (Section 8). | Closest thing this repository has to a NOTICE file. |
| `src/motor_ai_sim/simulation/pardiso_runtime.py`, `pardiso_lifetime.py` | Two-line pointer comment. | These are the modules that actually touch `pypardiso`/MKL; mirrors Leela Chess Zero's practice of annotating the files that touch the named library rather than mass-editing the tree. |

## 8. Owner architecture decision, 2026-09-30: MKL/PARDISO and Triangle leave the default image

Same day as the independent review above, the owner decided: the
default server image and default distribution drop Intel MKL/`pypardiso`
entirely (CHOLMOD via `scikit-sparse` and MUMPS become the default
direct-solver backends, with OpenBLAS/reference BLAS-LAPACK under them);
Gmsh becomes the default mesher once per-configuration verification
passes; Triangle is removed outright (not staged to a later release).
Consequences for this pack:

- **The urgent production compliance question this pack originally
  opened with is resolved by the architecture change, not by the
  exception.** The evidence in Section 6 above, and the production
  snapshot below, describe the image as it stood on 2026-09-30 *before*
  this decision -- kept here as the record of what was actually running
  and why this pack exists, not as a description of the new default.
- **`LICENSE-EXCEPTION` is downgraded from "urgently needed for our own
  server" to "optional, for operators who self-install the optional
  PARDISO backend."** See `LICENSE-EXCEPTION` Section A for the
  recommendation to keep it rather than delete it: MOTRES's grant
  removes an ambiguity for any downstream recipient of the AGPL source
  who chooses to combine it with MKL themselves, even though MOTRES's
  own distribution no longer does so by default.
- **The "Gmsh and MKL in the same process" analysis in
  `docs/MESHER_TRANSITION.md`** no longer describes the default build
  (gmsh will run with no MKL/pypardiso present at all by default); it is
  kept, relabelled, as a note for the optional PARDISO backend path
  only, where a user who enables both gmsh and PARDISO in the same
  process still faces it.
- **Triangle** is removed, not deferred to a future stage; see
  `docs/MESHER_TRANSITION.md` for the updated plan and the exact
  Triangle licence text (independent review item 7).

### Production snapshot as evidence (2026-09-30, before this decision)

- Image: `deploy-api@sha256:f868b5ac06093dc3ed3638314cc0b229281c627e5d0b77fa10f25a65b4db5947`,
  created 2026-09-30T16:32:59+02:00 (`docker inspect`, read-only).
- Source commit: not embedded in the image or the deploy checkout (no
  `.git` on the host at `/opt/motres/app`, no baked-in git SHA label or
  file found); the closest available marker is the application version
  string `0.1.0`. **Gap, flagged for the compliance plan**: future
  builds should bake a git commit SHA into the image (an OCI
  `org.opencontainers.image.revision` label or a `GIT_SHA` file) so a
  pip/package snapshot can be tied to an exact source commit without
  relying on host state.
- Build arguments: `deploy/docker-compose.yml` on the host sets
  `WITH_TRIANGLE: ${WITH_TRIANGLE:-1}` and `WITH_PARDISO: ${WITH_PARDISO:-1}`
  -- i.e. both default to **on** at the compose level, not an
  accidental override; the compose file's own comment records this as
  "numbers stay identical to the pre-AGPL image."
- Base OS and native (apt) dependencies (`deploy/Dockerfile.api`, base
  `python:3.11-slim`): `libglu1-mesa`, `libgl1`, `libxrender1`,
  `libxcursor1`, `libxft2`, `libxinerama1`, `libgomp1`,
  `fonts-dejavu-core`, `curl`. All mainstream Debian-packaged libraries
  under long-established DFSG-free licences (the X11 libraries are
  MIT/X11-style; `fonts-dejavu-core` is the Bitstream Vera/DejaVu
  licence, permissive; `curl` is MIT-style; `libgomp1` is itself GCC's
  runtime library under **GPL-3.0 with the GCC Runtime Library
  Exception** -- a real, already-present example of exactly this pack's
  section-7 mechanism, in the same image). This was a proportionate
  check, not a line-by-line audit of every transitive apt dependency;
  recommend an automated tool (`dpkg-query` plus the packages'
  `/usr/share/doc/*/copyright` files, or an SBOM scanner) for a final
  pass, same as recommended for the pip closure.
- Frontend (`web/`, npm) dependencies are unaffected by any of the
  server-image findings above (they ship as a static build, not inside
  `deploy-api-1`) and were already fully audited in
  `THIRD_PARTY_NOTICES.md` on 2026-09-29 (275 production packages: MIT
  175, Apache-2.0 57, ISC 22, BSD-3-Clause 17, and a handful of
  single-package MIT/ISC/Zlib/0BSD licences; no copyleft-incompatible or
  non-commercial licence found there).
