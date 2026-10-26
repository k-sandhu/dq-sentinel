# Quality workspace UI simplification

Date: October 26, 2026. Tracking: [#326](https://github.com/k-sandhu/dq-sentinel/issues/326).

## Baseline and preservation

The local `main` was fast-forwarded from `7b4b628` to `141a8f9`, bringing in 85 completed commits before this implementation. Existing tracked modifications and untracked documents/screenshots were copied to an independent backup and also retained in a Git stash. The stash was applied, not dropped. Conflicts in the planning document and stylesheet were resolved by retaining the upstream additions and the local planning extension/Docs presentation.

The UI implementation lives on `feat/simplify-quality-workflows`. Pre-existing local documents and Docs presentation changes remain separate from its commit. The running demo retains those local presentation changes.

## Changes

- Global navigation now presents **Overview, Datasets, Checks, Issues, Reports, Explore**, plus Settings. Secondary tools appear in the current section's contextual navigation. Existing URLs, favorites, search, appearance options, and account controls remain available.
- Dataset navigation exposes **Overview, Checks, Monitors, Issues, Contract**, with seven secondary sections under **More**. Overview continues to display the existing Profile content. Technical deep links and unsaved-editor guards are preserved.
- Dataset/global check views open on **Results**. **Proposals** has a separate count and review view. Results prioritize run errors, failures, and warnings, then group by column/dataset. Details and secondary actions use disclosures. Existing bulk activation/dismissal, confirmations, permissions, and generation behavior are retained.
- Check views retain portable URL filters, including legacy proposal links. Switching views preserves valid Results filters; successful generation opens proposal review without activating checks.
- Exception filtering uses a compact search/status/assignee strip, progressive **More filters**, removable active-filter chips, and a counted saved-view picker. Sorting, grouping, export, facets, paging, multi-selection, and triage remain available. The existing all-status default is preserved.
- Status selection supports multiple values, Escape, Tab-away, and outside-click dismissal without triggering row shortcuts or clearing queue selection.
- The Docs disclosure sits in the header. Its hidden links are removed from keyboard navigation; opening, Escape/focus restoration, and leaving the popup are tested. Local Docs labeling is retained in the working demo.
- Supporting-text and selected-action colors were adjusted across the standard themes; search fields receive shared form styling. Tablet navigation fits without a long horizontal task strip, and centered navigation stays below the sticky header.

No backend API/model changes or new dependencies were introduced. This is the navigation/check-view/filter slice of the broader roadmap; monitoring metrics, check-generation traces, and checks-as-code remain separate work.

CI also identified an inherited `certifi==2026.7.22` trust-bundle advisory. The existing pin was updated to `2026.10.19`, the fixed version documented by the [upstream advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2), with other pins retained. This follow-up is tracked in [#328](https://github.com/k-sandhu/dq-sentinel/issues/328).

## Verification

- Full backend pytest suite passed after updating to current main and again after the certifi refresh.
- Frontend typecheck and production build passed.
- All **280 frontend tests across 32 files** passed, including production chunk budgets.
- Added behavioral coverage for navigation reachability/deep links, disclosure keyboard behavior, Results/Proposals isolation and filter roundtrips, generation review, viewer restrictions, active-filter removal, saved views, search clearing, paging reset, and status-picker dismissal.
- Cross-review regressions cover generation completing after navigation or dataset replacement, saved views with identical filters/different presentation or aliases, and cancellation of guarded menu navigation. Modal focus restoration captures its opener before child autofocus runs.
- A PostgreSQL dump of the previous demo metadata was restored to a separate rehearsal database. Startup migrated it from `0008_data_contracts` to `0013_schema_baseline_scopes`, retaining 1 user, 5 datasets, 70 checks, and 50 exceptions. The standing demo then upgraded successfully using the supported development Compose overlay.
- Browser checks used synthetic data at 1280×720 and 768×900. Checked separate result/proposal views, saved-view application, removal of one advanced filter without dropping others, all six standard theme/mode combinations, and contextual navigation. Component tests cover mutation/permission behavior without invoking paid generation.

## Measured presentation

The earlier measurements came from the older local demo. The updated measurements include both upstream improvements and this change, rather than attributing all differences to this patch.

| Surface | Earlier local demo | Updated workspace |
|---|---:|---:|
| Always-present primary task links | 13, plus Settings | 6, plus Settings; secondary links are contextual |
| Dataset section strip | 12 choices wrapping onto two lines | 5 core sections plus More; one line at tested widths |
| Operational check result | Proposals pushed first active result to about y=1368 | Results open directly; operational rows are visible in the first desktop viewport |
| Exception filter panel, default desktop | About 357 px | About 127 px |
| First exception row, default desktop | About y=633 | About y=452 |
| Tablet page overflow | Long global/dataset navigation | No page-level overflow at 768 px; tables retain their own scrolling |

### Sampled contrast ratios

Ratios use the computed standard theme colors. They assess these specific text/background pairs, not whole-product accessibility conformance or every custom accent.

| Theme/mode | Supporting text on card | Supporting text on page | Selected primary navigation |
|---|---:|---:|---:|
| Aurora light | 4.97:1 | 4.79:1 | 5.10:1 |
| Graphite light | 5.03:1 | 4.65:1 | 4.55:1 |
| Editorial light | 5.07:1 | 4.82:1 | 7.41:1 |
| Aurora dark | 5.91:1 | 6.57:1 | 6.66:1 |
| Graphite dark | 6.53:1 | 7.09:1 | 9.30:1 |
| Editorial dark | 6.27:1 | 6.79:1 | 5.57:1 |

Browser screenshots and measurement artifacts are generated locally under `output/playwright/`. No customer datasets or source databases were modified.
