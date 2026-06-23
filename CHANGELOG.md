# Changelog

All notable changes to **Motor AI Simulator** are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com); versioning follows [SemVer](https://semver.org).
The single source of truth for the current version is the `VERSION` file at the repo root;
cut a release with `scripts/release.ps1` (see `docs/RELEASES.md`).

## [Unreleased]

### Changed
- **Access control:** the Motors catalog stays open to everyone, but **Configure +
  the engineering tabs now require sign-in**. Anonymous visitors browse motors only;
  "Load" prompts Google sign-in (previously anon could open Configure and work).
- Catalog: first diameter bucket **5 mm → 12 mm**.

### Removed
- Redundant "Sign in to save your motor designs" prompt in the catalog — the header
  already has a Sign-in button.

## [0.1.1] — 2026-06-23

### Changed
- **Rebrand → AeroStator Core** — a motor technology portal: header title, browser
  title/meta, motor-catalog copy (positioned for **aerospace, robotics, EV, marine**;
  select → configure → price → request manufacturing), version-badge tooltip, and the
  support assistant's identity. Internal package name (`motor_ai_sim`) unchanged.

## [0.1.0] — 2026-06-23
First tracked release. Establishes app versioning + a coordinated release process
(frontend + backend deployed together, version stamped into both, skew detected at runtime).

### Added
- **Multi-user isolation (P1–P4):** per-request `?geo=` so each signed-in user computes their
  OWN design instead of a shared global config; per-user active workspace persisted to Firestore
  (`users/{uid}/workspace/active`, restored on sign-in); `AUTH_ENFORCE` + tiers live
  (anonymous = configurator-only, heavy FEM gated, owner = admin).
- **Slot-derived winding connections** (2S/2P … 8S/4S-2P/8P) — single source `/api/winding/config`.
- **Versioning:** `VERSION` file, `GET /api/version`, in-app version badge with
  frontend↔backend **skew detection**, this changelog, and a coordinated release script.

### Fixed
- **3D viewer:** stuck "Building…" indicator (the updating flag now clears on every fetch
  settle); FEM sliding-band air domains no longer obscure the motor (default off + migration).
- **40 mm geometry:** `tooth_width` schema minimum lowered (4 → 1) so small motors are
  editable in the UI and build without a degenerate slot fillet.
