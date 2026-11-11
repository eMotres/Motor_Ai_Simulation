# Mobile audit, November 2026

Owner's request (2026-11-11): the site must be comfortable on a phone, above all when a customer fills in the
motor specification, and the way from a deep page back up to the level above must be visible and must work.
A later note from the owner: desktop bugs found during the audit are fixed too. They are listed separately below.

## How it was checked

- **Widths.** 360 x 780 and 390 x 844 (phones), 768 x 1024 (tablet), and desktop 1440 x 900.
- **Browser.** Headless Chromium with mobile emulation: `isMobile`, `hasTouch`, device pixel ratio 2 and an Android user agent.
- **Where it ran.**
  - Everything ran on a local dev server: the web app from this branch, against a local API with `AUTH_ENFORCE=1`.
  - The local API used test accounts and synthetic data: one project, one production with parts, a thread, a document and two bench tests.
  - The production site was not touched. Its `X-Frame-Options: DENY` header blocks embedding it at phone width, and the owner's own browser cannot emulate a phone. No spec, order, message or upload was sent anywhere except to the local API.
- **Accounts.** The customer view used a standard account (Motors, Configure, 3D model, Production member, Projects). The admin view was used for the catalog, Configure and the engineering tabs.
- **What was measured.** On every page, a script measured:
  - horizontal page overflow;
  - controls sticking out of their card;
  - input text under 16 px (iOS zooms the page into such inputs);
  - numeric fields without `inputmode`;
  - tap targets under 32 px.
- **Dark mode.** Checked on the specification at 360.
- **Before and after screenshots.** "Before" comes from a second dev server at the base commit. "After" comes from this branch. Both ran against the same local data.

Severity: **H** = blocks or seriously hinders the task on a phone; **M** = clearly wrong but there is a workaround; **L** = cosmetic.

## Findings and status (phone / tablet)

| # | Page | Width | Symptom | Screenshot | Sev | Status |
|---|------|-------|---------|------------|-----|--------|
| 1 | Every deep page (project, production, project tests window) | all | The browser / Android **Back button left aerostator.com** from any depth. The app keeps every page on one address with no history entries; inside a project's specification, Back went to the previous site. | n/a (tested: Back from a project went to `about:blank`) | H | Fixed: `lib/historyLevels.ts`. Opening a project, a production or the project's tests window adds one history entry. Back closes that level (project to list, production to list or back to its project, tests window closes). Closing a level with the page's own arrow removes its entry again, so Back never lands on a dead step. The address bar is not changed. |
| 2 | Project, all tabs, especially the specification | 360-768 | **No visible way up.** The "All projects" button sits above the content and scrolls away, and the wizard scrolls past it on every step change. | `01_spec_top_360_before.png` | H | Fixed: a back arrow in the fixed header row, before the project's sub-tabs (Production already had one). |
| 3 | Production opened from a project ("Open in Production") | all | Its back arrow led to the production list, not back to the project, which breaks the path test > Production > project > projects list. | n/a | M | Fixed: a production opened from a project returns to that project ("Back to the project", EN + ZH). |
| 4 | Specification, step 1 (Application, Mode and rotation) and other menus with long options | 360, 390 | **Inputs cut off at the card's right edge.** The option-based `min-width` beat `max-width: 100%`. | `01_spec_top_360_before.png`, `02_spec_load_type_360_before.png` | H | Fixed: `optionsMinWidth` returns `min(100%, ...)`. |
| 5 | Specification: Load type, Power supply switches | 360, 390 | One-line switch row: "Turbine" half visible, "Shaft" and "AC mains" off the card, with no hint that the row scrolls. | `02_spec_load_type_360_before.png`, `03_spec_supply_360_before.png` | H | Fixed: below 768 px these switches become a grid of separate 44 px buttons, so every choice is visible. |
| 6 | Specification: every text and number field | phones | Input text 11-15 px. **iOS Safari zooms into the field on focus** and does not zoom back. | metrics in the run log | H | Fixed: on touch screens (`pointer: coarse`) inputs, textareas and selects are 16 px. The large number boxes keep their 18-20 px. |
| 7 | Specification Back / Next bar | 360, 390 | "Step 1 of 7" broke into 3 lines and "Next: Operating points" into 2 lines. The help bubble, lifted above the bar, covered the input right above it. | `01_spec_top_360_before.png` | M | Fixed: on phones the counter is hidden (the step bar above already shows it), Next is one line with an ellipsis, and the help bubble docks in the bar's free right end. |
| 8 | Specification at the tablet width | 768 | The help bubble sat on the Next button (the wizard stacks below 900 px, while the bubble rule started at 768). | `05_spec_768_after.png` (after) | M | Fixed: the same docking for everything below 900 px. |
| 9 | Whole app on a phone browser | phones | Height was `100vh`, which is taller than the visible area while the address bar shows, so the bottom of each page (the spec's Back / Next bar, chat inputs) sat under the browser's toolbar. | n/a | M | Fixed: `100dvh` where supported, plus `interactive-widget=resizes-content`, so on Android the on-screen keyboard shrinks the page and the active field and the bar stay above it. |
| 10 | App header (signed in) | 360 | The header wrapped to two rows; the theme switch sat alone on the second row and cost 40 px on every page. | `01_spec_top_360_before.png` | M | Fixed: one row on phones (tighter gutters, brand without "Core" below 600 px). |
| 11 | Panel header row in a project / production | 360 | A row held only the tab name ("Projects", "Production"), already underlined in the tab bar: 40 px of a 780 px screen. | `01_spec_top_360_before.png` | L | Fixed: on phones, when a panel shows sub-tabs and no actions, the sub-tab row is the header. |
| 12 | Landing (signed out) header | 360 | "Sign in" broke into two lines ("Sign / in"). | `09_landing_signin_360_before.png` | L | Fixed. |
| 13 | Production > BOM | 360 | Inside the sideways-scrolling table the sticky header stuck 33 px into it and covered the first row, and the Title column broke one letter per line. | `06_bom_360_before.png` | M | Fixed (see also D1). |
| 14 | Production > Discussion, Proposals | 360 | The thread selector row ran off the screen ("Factory only" cut off); the part selector of Proposals stuck out of its card. | `08_discussion_360_before.png` | M | Fixed: the row wraps, and the selectors are capped at the column width. |
| 15 | Configure > die / configuration strip | all | No way up from a configuration to the catalog other than the top tab. | n/a | L | Fixed: the die name in the strip opens the Motors catalog. |
| 16 | Specification, draft | 360 | Checked: a typed value survives a tab switch and a reload ("Saved" shows). A non-number shows the red outline. A submit with open required fields jumps to the first one and shows its message above the bar. | n/a | - | OK. |
| 17 | Sign-in dialog, legal pages, help chat, projects list, production list, Tests (table scrolls inside its own box), Documents, Timeline | 360-768 | No page overflowed horizontally; the dialogs close and scroll. | n/a | - | OK. |
| 18 | Small tap targets | phones | Header icons are 26-30 px; the List / Cards switch is 32 x 24; the consent "Accept / Decline" buttons are 30 px high; "Create account" and "Forgot password?" are 18 px high. | metrics | L | **Open.** Enlarging them needs a header redesign; they work, they are just small. |
| 19 | Terms-update line | phones | Until it is dismissed it covers the bottom of the page, including the spec's Back / Next bar. | n/a | L | **Open** (one tap dismisses it). |
| 20 | Specification assistant tab (fixed at the right edge at 38 % height) | phones | Not shown locally (no AI provider here). From the code, on a phone it floats over the fields' right edge. | n/a | L | **Open**: needs a check on production with the assistant enabled. |
| 21 | Error text for a non-number | all | "4x" in a number box shows "Required field" instead of "not a number". | n/a | L | **Open**. |

### Not covered or limited

- **Catalog and Configure.** The local catalog had only three test dies, so the motor card and Configure were checked on those (`Points only` motors). They rendered without overflow at 360. The full catalog with propeller curves and plots was not available locally.
- **3-D viewer.** The local API has no CadQuery (App Control blocks OCP on this PC), so the 3-D viewer and the plot pinch behaviour were not checked.
- **Real devices.** The on-screen keyboard and iOS behaviour need a check on a real device after deploy. Headless Chromium has no keyboard.

## Desktop fixes (owner's note: fix desktop bugs too)

| # | Page | Width | Symptom | Screenshot | Sev | Status |
|---|------|-------|---------|------------|-----|--------|
| D1 | Production > BOM (with prices shown) | 1440 | The table is wider than the screen and the box clipped it: **the last columns ("Specification") were cut off and unreachable**, and the Title column broke one letter per line ("Stat / or / cor / e"). | `07_bom_1440_before.png` | M | Fixed: Title at least 140 px, and the table now overflows sideways into the page's scroller (the header still sticks). `07_bom_1440_after.png` |
| D2 | Browser Back on desktop | 1440 | Same as #1: Back left the app from a project or a production. | n/a | M | Fixed by the same change. |
| D3 | Project page header row | 1440 | No back arrow in the fixed row (only Production had one). | `10_spec_1440_after.png` | L | Fixed: the arrow is added. Apart from it, the 1440 layout of the specification is unchanged; compare `10_spec_1440_after.png` with the same page before. |

## Files changed (web only)

- **Back navigation.**
  - New: `lib/historyLevels.ts`, test `lib/__tests__/historyLevels.test.mjs`.
  - Used in `modules/development/components/DevelopmentPage.tsx`, `ProjectProductions.tsx` and `modules/production/components/ProductionPage.tsx`.
  - `lib/openProduction.ts` (the return target).
  - `locales/{en,zh-CN}/production.json` (`backToProject`).
- **Specification.** `specGrid.ts` (+ test), `specWizard/SpecWizard.tsx`, `specWizard/useSpecFields.tsx`, `specWizard/ui.tsx`, `components/support/SupportWidget.tsx`.
- **Shell.** `App.tsx`, `index.css`, `index.html`, `components/common/PanelHeader.tsx`, `components/landing/LandingShell.tsx`, `components/auth/AuthButton.tsx`, `components/common/ActiveFamilyStrip.tsx`.
- **Production.** `BomTable.tsx`, `DiscussionPanel.tsx`, `ProposalsPanel.tsx`.

## Verification

- **Unit tests.** `node --test` on the module tests of development, production and tests (478 tests). The project-productions test was updated for the new `openProduction` argument and the spec-grid test for the new `min-width`. Also run: `historyLevels`, `subTabs`, `landing`, `seo`, `stripContext`, `i18nParity` and `customerPath`; all pass.
- **Failures that were already there.** `supportWidget.test.mjs` (3) and `chinaAccess.test.mjs` (`loadGis`) fail identically on the base commit.
- **Build.** `vite build` and the landing prerender pass. `tsc -b` shows no errors in the changed files; the existing errors elsewhere are unchanged.
- **Lint.** ESLint on the changed files reports nothing new.
- **Back navigation, run at 360 and 390.**

  | Action | Result |
  |---|---|
  | Open a project, press Back | The project list shows. |
  | Open a production, press Back | The production list shows. |
  | Press Back again | Leaves the app, as expected. |

  `history.length` does not grow when a level is opened and closed from the page.
