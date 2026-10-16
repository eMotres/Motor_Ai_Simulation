# D85 identity bootstrap: concrete operational proposal

Model: GPT-6; no new escalation. State: independently reviewed local package, not transferred or executed. The ordinary-account 85 mm Configure card is already installed and available for human review. This next step checks authenticated saved-build/process integration; it does not finish or qualify propeller S1 coupling.

## Exact payload and destinations

Host: `176.9.84.229` (aerostator.com). Create-only staging destination: `/tmp/motres-bootstrap-install-37320eae246b0579/proposal`, with both new directories root-owned mode 0700 and no symlink traversal. Refuse existing paths, including dangling links. No other destination is authorized by this proposal.

Local source: `scratch_s1_20261006/d85_finish_20261007/canonical-bootstrap-runtime-source-package-20261008/`. Transfer exactly the following ten files, preserving their bytes. Internal Git checkout, extracted overlay, audits and dataset records are not transfer payload.

| File | SHA-256 |
|---|---|
| README.md | fec46ceaedd5d5e090f6923baab22b64f1893001ca74e9c29da29513cc09d697 |
| actual-api-source-preflight.json | 40d411cf3c7937544d608c392dba5c086dfe160f715f7300b1b517e01b86bb30 |
| actual-inherited-api-source-preflight.json | 3455248bd61120c6dc79cc308638ab6901496e55345f1b547ad3a035d4e1ba99 |
| bootstrap-source-overlay.tar.gz | e944d45667b02f713eead32d5a3a4aff4103b4d98b6029907b6ca0b58cd5aaf4 |
| bootstrap_identity_once.py | e76b75cd329663025af1bf1d61520b64b17d436c4323c3308a568f92e1ecc148 |
| candidate-descendant.bundle | f30dd5b820c3f0857b027b47c4cd42cbdaba0d9eb00756a356baa80d1ffaf3ee |
| inherited-source-archive-comparison.json | 6b5966425ad72ff9352d1b2e3caa586653311a3843044775e8eb0788481b87fd |
| source-inventory.json | 450fa7a671de91a073b09546beb30e64905fb4d1a14eaac421809bbc183c44c8 |
| package-checksums.json | 89dea5bcb9056181be2889458f76dffa75423bac2b5df729b7f4459b6ccb3394 |
| install_bootstrap_closed.py | 152bbd324a36120f3461a7cd5377a135dc0dc9ee52eb966b706df48ed3c2fe91 |

The installer is pinned externally to avoid a self-referential manifest. Its source overlay contains exactly 24 paths (18 additions, six modifications) under `/opt/motres/app`, enumerated in `source-inventory.json`. Candidate `c2c13c392bcc31e03d285186737c9201bbdd1692` is the sole-parent descendant of production `847b254c53f863d6d7c4bfbd44286f0a4269d57d`; its tree is `3b5edfa2c89edc4d2146ac85ef210a4383eb6355`.

The actual read-only metadata preflight found `/var/lib/motres` absent and API UID/GID 10001. Proposed apply creates only `/var/lib/motres`, root-owned mode 0755, after checking safe real ancestors; it refuses unexpected links, ownership, modes or creation races. It then creates a new private runtime directory `/var/lib/motres/propeller-s1-bootstrap-665bfbda76b0621d`, UID/GID 10001, mode 0700. The separate nonce is derived from the stage nonce with the installer's domain-separated hash. Creation is explicitly part of the requested approval, not a completed server operation.

## Proposed action after explicit owner approval

The operator first verifies the committed payload pins and create-only staging safeguards. Only then may the staged installer be run with `--verify-bundle` followed by `--apply`. It requires the pinned production marker, source preimages and absent-file set, original API image `sha256:56dcf627d46a8988e5fdd8224e7004841d092f56504a933327e84294760f78b3`, and start witness `2026-10-07T16:17:31.422176668Z`. It refuses detected jobs, resumable sweeps, scheduled/sending newsletter campaigns, malformed state or unknown liveness. It takes the deployment lock, builds an API image from that immutable base, and verifies all 52 source hashes before source replacement or stopping the API.

Apply performs two API-only recreations: briefly enable the default-closed bootstrap route, issue one authenticated loopback identity request, and recreate the same candidate API with the gate closed. It checks web container identity/image/start unchanged. It installs exactly the 24 source paths and updates the deployed marker only after success. Failure permits the recorded CAS-guarded source restoration and API-only fallback to the original image only while exact service/job/child liveness is clear. Busy or unknown liveness refuses a forced restart/rollback and requires operator recovery; no job is silently killed.

Authentication requires a genuine existing, live, revocable SID for fixed owner workspace `c309c100cd421858`. The helper uses the existing signing material and a token lasting at most 360 seconds only in memory; no credential argument, output, copy, persisted bearer, session creation, renewal or revocation is permitted. Normal HTTP session `last_seen` bookkeeping and the owner scheduler record/private snapshot/result writes are expected effects. Live session availability remains untested; absence causes refusal before service changes.

The exact saved body is L13/rated/G32 at 1000 RPM, body hash `851f96bd55e3f10c5f5e4500a46e7f1ff6d6f3d0324050fd0a94d3d4e370e7b0`. This administrator identity scenario differs from ordinary workspace `93e912e4003e328e`/peak and unsaved Configure edits. It performs no motor solve, search or FEM and proves no valid G32 load point at 1000 RPM. All six qualification/publication flags must remain false.

## Limits and approval boundary

The authenticated route is restricted to the fixed owner but has no global single-use guard during the gate-on interval; another valid same-owner caller could submit a request. The installer sends one request. The 300-second scheduler deadline is cooperative around synchronous snapshot I/O, not a hard end-to-end wall-clock cap. HTTP/helper timeouts are finite; a client timeout does not prove child cancellation or permit a forced restart. Builds/recreations have separate finite budgets.

Source and installer independent static review passed; parent matched the final payload checksums. Worker tests covered nine fake-filesystem parent-creation/refusal cases plus temporary CAS/liveness failures. No application child, authenticated bootstrap, image build, transfer, API restart or FEM has been performed for this package. The earlier four-file Linux lifecycle consent was fulfilled once and does not authorize this operation.

`C:/Users/vadim/Projects/AGENT_RULES_motor_ai_sim.md` requires: “Restarts happen only when the owner says «restart» (Russian in the original).” Prior automatic review also required concrete destination/payload consent for source transfer. Approval is therefore requested only after this exact package and operational scope are reviewable. It does not extend to public S1 enablement, ordinary-account source/context edits, large FEM runs or physical qualification.
