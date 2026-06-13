# UI Flow Glossary

Last reviewed: 2026-06-12

This glossary maps the current DQ Sentinel UI from the frontend code in
`frontend/src`. It is screen-by-screen and focuses on what each clickable element
does, where it takes the user, and whether that flow is good, weak, or a gap.

## Rationale Legend

- Good: The click target supports the user's intent and lands in a predictable place.
- Mixed: The behavior is useful, but the next step or consequence is not fully clear.
- Gap: The behavior risks confusion, data loss, dead ends, or hidden work.

## Primary Flow

The strongest intended user journey is:

1. Login.
2. Open Connections.
3. Add a source.
4. Browse source tables.
5. Register datasets.
6. Profile a dataset.
7. Generate or create checks.
8. Run checks.
9. Triage exceptions.
10. Investigate failures in Workbench, RCA, Assistant, or Lineage.

That flow is mostly present, but several screens rely on users discovering tabs,
small links, or table-row clicks rather than being guided by clear task-oriented
next steps.

## Highest-Impact Gaps

| Gap | Why it matters | Recommendation |
|---|---|---|
| Deep screens lack breadcrumbs back to list/context. | Users can enter a dataset from search, lineage, runs, exceptions, or assistant and lose the broader context. | Add a compact breadcrumb row: Home / Datasets / table, and preserve source context when possible. |
| Several destructive actions have no confirmation. | Check archive/dismiss, dashboard delete, chat delete, MCP delete, and user activation changes happen too easily. | Add confirm dialogs or undo to all destructive or hard-to-reverse actions. |
| Runs do not have a detail screen. | Users see a run, exceptions, and investigation links, but cannot inspect one run as a durable object. | Add `/runs/:id` or a run detail drawer with metrics, SQL, exceptions, RCA, and suggested investigation. |
| Exceptions dataset filter clears `run_id`. | Changing the dataset dropdown while viewing `/exceptions?run_id=...` silently removes the run filter. | Preserve existing query params unless the user explicitly clears them. |
| Lineage click destinations are inconsistent. | Graph nodes open dataset lineage, attention items open dataset profile, relationship table opens dataset profile. | Standardize node clicks to one target or offer explicit "Open profile" and "Open lineage" actions. |
| Raw JSON check params are exposed in create/edit modals. | This creates avoidable errors for non-technical users and weakens trust in check setup. | Replace JSON textareas with schema-driven fields, keeping raw JSON behind an advanced toggle. |
| Some disabled controls do not explain recovery. | Generate dashboard/checks disabled until profile exists; assistant disabled without editor role/session. | Show inline "Profile first", "Create a conversation first", or "Editor role required" hints near the disabled control. |

## Global Shell

Applies after login to all authenticated screens.

| Element | Click result | Rationale |
|---|---|---|
| Sidebar: Home | Navigates to `/`. | Good. Obvious landing point for health overview. |
| Sidebar: Connections | Navigates to `/connections`. | Good. Clear source-management entry point. |
| Sidebar: Datasets | Navigates to `/datasets`. | Good. Main monitored table inventory. |
| Sidebar: Checks | Navigates to `/checks`. | Good. Cross-dataset rule inventory. |
| Sidebar: Runs | Navigates to `/runs`. | Good. Cross-dataset execution history. |
| Sidebar: Exceptions | Navigates to `/exceptions`. | Good. Global triage queue. |
| Sidebar: Workbench | Navigates to `/workbench`. | Good. Exploration tool is globally available. |
| Sidebar: Lineage | Navigates to `/lineage`. | Good. Estate view is discoverable. |
| Sidebar: Assistant | Navigates to `/assistant`. | Good. AI helper is top-level, which matches its cross-workflow role. |
| Sidebar: Settings | Navigates to `/settings`. | Good. Standard placement near account controls. |
| Fleet health pill | Navigates to `/connections`. | Good. Source health problems naturally resolve in source management. Mixed because the label "Sources" can be vague before a health probe has run. |
| Global dataset search result | Navigates to `/datasets/:id`. | Good. Fast path to dataset detail. Gap: search only covers datasets, not checks, runs, owners, exceptions, or commands. |
| Global search Enter key | Opens the first result. | Good for keyboard speed. Gap: no arrow-key result selection. |
| Global search Escape/outside click | Closes the dropdown. | Good. Predictable behavior. |
| Theme toggle | Switches light/dark theme and saves `dq-theme` in localStorage. | Good. Low-risk preference control. |
| Sign out | Clears the JWT and redirects to `/login`. | Good. Standard account flow. Gap: no confirmation, but this is low risk. |
| Modal X or backdrop click | Closes the modal. | Good. Familiar pattern. Gap: can discard unsaved form edits without warning. |
| ErrorBoundary "Try again" | Clears the page error state in place. | Mixed. Better than a dead screen, but it may not refetch the failed query or explain the recovery path. |

## Login - `/login`

| Element | Click result | Rationale |
|---|---|---|
| Sign in | Posts credentials, stores token, and authenticated users are routed to `/`. | Good. Direct and conventional. |
| Email/password fields | Update login form state. | Good. Basic form flow. |

Gaps:

- No "show password" control.
- No forgot-password or reset flow.
- The default dev credentials are helpful locally but must stay out of production-facing builds.

## Home - `/`

| Element | Click result | Rationale |
|---|---|---|
| Add data | Navigates to `/connections`. | Good. It starts the primary setup path. |
| Runs button in run trend card | Navigates to `/runs`. | Good. The chart's detail destination is clear. |
| All datasets | Navigates to `/datasets`. | Good. Provides escape from the attention subset. |
| Dataset needing attention card | Navigates to `/datasets/:id/exceptions`. | Good. Sends the user to the problem queue, not just metadata. |
| Recent run dataset link | Navigates to `/datasets/:id`. | Good. Standard object drill-down. |
| Recent run exception link | Navigates to `/exceptions?run_id=:id`. | Good. Useful failure-specific path. |
| Recent run investigate link | Navigates to `/workbench?dataset_id=:id&run_id=:id`. | Good. Strong operational flow from failure to analysis. Mixed because Workbench is transient and not a durable run report. |

Gaps:

- Stat cards are not clickable. Users may expect "Open exceptions" to open `/exceptions` or "Failing checks" to open filtered checks.
- The trend chart only has a separate Runs button; direct bar click is not supported.
- Home starts the setup path, but it does not show a first-run checklist.

## Connections - `/connections`

| Element | Click result | Rationale |
|---|---|---|
| Check fleet health | Calls `/connections/health` and updates status pills. | Good. Explicitly avoids surprising probes against many sources. |
| Add connection | Opens the Add Connection modal. Admin only. | Good. Role-gated source creation. |
| Add your first connection | Opens the Add Connection modal. | Good. Empty state points to the right next step. |
| Browse tables | Navigates to `/connections/:id/browse`. | Good. Clear transition from source to table registration. |
| Delete connection | Shows a browser confirmation, then deletes the connection and dependent datasets/checks/history. Admin only. | Mixed. Confirmation exists, which is good; consequence is large, so a richer typed confirmation would be safer. |
| Add Connection modal: engine chip | Selects an engine and prefills a DSN template. | Good. Reduces setup friction. Mixed because driver-installed state may still be confusing to non-admins. |
| Add Connection modal: Test connection | Calls `/connections/test`. | Good. Lets users validate before saving. |
| Add Connection modal: Save | Creates the connection and closes the modal. | Good. Straightforward. Gap: if testing failed, Save is still available by design; the UI should clearly label this as "Save anyway". |
| Add Connection modal: Cancel/X/backdrop | Closes without saving. | Mixed. Standard behavior, but unsaved edits are lost silently. |

Gaps:

- DSN entry is powerful but unfriendly. The engine templates help, but guided fields per engine would improve adoption.
- Connection rows are not clickable; only Browse Tables is. That is acceptable because there is no connection detail screen, but it may feel inconsistent with other tables.
- Health status is blank until a manual health probe, while the global fleet pill polls health automatically. The two behaviors can feel inconsistent.

## Browse Tables - `/connections/:id/browse`

| Element | Click result | Rationale |
|---|---|---|
| Connections back link | Navigates to `/connections`. | Good. This is one of the few explicit breadcrumbs. |
| Filter tables input | Filters visible source tables locally by table name. | Good. Fast and safe. Gap: does not filter schema name. |
| Unregistered row click | Toggles table selection. | Good. Efficient bulk registration pattern. |
| Checkbox | Toggles table selection. | Good. Explicit selection affordance. |
| Already registered link | Navigates to `/datasets/:registered_dataset_id`. | Good. Helps avoid duplicate registration confusion. |
| Register dataset(s) | Posts selected tables. If one dataset is created, navigates to `/datasets/:id`; if multiple, navigates to `/datasets`. | Mixed. Single selection has an excellent landing page. Multiple selection loses the exact newly-created set. |

Gaps:

- After registering multiple datasets, the user lands on the full dataset list without a "created just now" filter or success summary.
- The filter does not include schema or kind.
- There is no "select all visible" control.

## Datasets - `/datasets`

| Element | Click result | Rationale |
|---|---|---|
| Browse sources | Navigates to `/connections`. | Good. Correct upstream setup action. |
| Search input | Filters the loaded dataset list by table, connection, or owner. | Good. Useful local filtering. |
| Health chips | Filter list by `all`, `fail`, `warn`, `pass`, or `unknown`. | Good. Supports triage scanning. |
| Empty-state Go to connections | Navigates to `/connections`. | Good. Clear recovery. |
| Dataset row | Navigates to `/datasets/:id`, defaulting to Profile tab. | Good. Common table drill-down pattern. |

Gaps:

- Rows do not expose direct shortcuts to Exceptions, Checks, or Runs, even though those are common destinations.
- The Connection cell is not clickable.
- Sorting is fixed by open exceptions; no user-controlled sort by owner, health, row count, or last profiled.

## Dataset Detail Shell - `/datasets/:id` and `/datasets/:id/:tab`

| Element | Click result | Rationale |
|---|---|---|
| Workbench | Navigates to `/workbench?dataset_id=:id`. | Good. Keeps query context. |
| Profile now | Posts profile request and refreshes profile/dataset state. Editor/admin only. | Good. Primary action is available in the header. |
| Profile tab | Navigates to `/datasets/:id/profile`. | Good. URL-addressable tab. |
| Code tab | Navigates to `/datasets/:id/code`. | Good. Discoverable data definition. |
| Lineage tab | Navigates to `/datasets/:id/lineage`. | Good. Context-specific impact map. |
| Checks tab | Navigates to `/datasets/:id/checks`. | Good. Rule management is close to the dataset. |
| Runs tab | Navigates to `/datasets/:id/runs`. | Good. Dataset-specific execution history. |
| Exceptions tab | Navigates to `/datasets/:id/exceptions`. | Good. Dataset-specific triage queue. |
| Dashboards tab | Navigates to `/datasets/:id/dashboards`. | Good. Dataset analysis lives near the dataset. |
| Knowledge tab | Navigates to `/datasets/:id/knowledge`. | Good. Business context is tied to the table. |
| Root cause tab | Navigates to `/datasets/:id/rca`. | Good. Investigations are attached to the dataset. |

Gaps:

- No breadcrumb back to Datasets, Connection, or previous context.
- Tabs are text-only and equally weighted. For failing datasets, Exceptions or Runs may deserve stronger prominence.
- The page header can become a dense single line for long schema/table names.

## Dataset Profile Tab

| Element | Click result | Rationale |
|---|---|---|
| Profile this dataset | Runs profiling when no profile exists. | Good. Empty state has the exact next action. |
| Preview rows / Hide preview | Toggles a 25-row preview table. | Good. Gives quick data inspection without leaving the page. Mixed because preview visibility is local state and not shareable in URL. |
| Column cards | No click action. | Good for read-only stats, but mixed because users may expect clicking a column to create a check or filter rows. |

Gaps:

- Profile facts such as PK candidates and temporal columns are badges only; they do not offer "create unique check" or "create freshness check".
- Preview rows do not link to Workbench or explain the guard/limit model.

## Dataset Code Tab

| Element | Click result | Rationale |
|---|---|---|
| Copy | Copies the DDL/definition to clipboard and temporarily changes label to Copied. | Good. Low-friction reuse. |

Gaps:

- No "Open in Workbench" action with the table name inserted.
- Clipboard failure is silent.
- Long DDL has no search or wrap controls.

## Dataset Lineage Tab

| Element | Click result | Rationale |
|---|---|---|
| Depth chip 1/2/3 | Re-fetches lineage neighborhood at that depth. | Good. Simple scope control. |
| Open estate lineage | Navigates to `/lineage?connection=:connection_id`. | Good. Natural escape from local graph to estate graph. |
| Graph node for registered dataset | Navigates to `/datasets/:dataset_id/lineage`. | Good for staying in lineage context. Mixed because other lineage screens sometimes open the profile tab instead. |
| Graph hover | Highlights connected nodes and edges. | Good. Helps visual parsing. |

Gaps:

- External/unregistered nodes cannot be registered directly from the graph.
- Depth control is numeric; users may not understand "hop" without hovering the title.
- Node click destination is not visually communicated beyond cursor style/title.

## Dataset Checks Tab

| Element | Click result | Rationale |
|---|---|---|
| Generate checks | Creates proposed checks from profile using AI or heuristics, then refreshes the table. | Good. Centralizes check discovery. Disabled until profile exists. |
| Explore data first | Toggles whether the AI exploration agent runs SQL before proposing checks. | Good. Makes the extra cost/behavior explicit. |
| New check | Opens manual check modal. | Good. Supports expert/manual control. |
| New Check modal: Create & activate | Creates an active check. | Mixed. Efficient for experts, but JSON params can fail and are hard for normal users. |
| New Check modal: Cancel/X | Closes without creating. | Mixed. Unsaved inputs are lost silently. |
| Proposed check Activate | Changes status to active. | Good. Review-to-enable flow is clear. |
| Proposed check Edit | Opens edit modal. | Good. Supports review before activation. |
| Proposed check Dismiss | Deletes/archives the proposed check. | Gap. No confirmation or undo. |
| Active check Run | Executes the check immediately. | Good. Strong feedback loop. |
| Active check Pause/Resume | Toggles active/disabled state. | Good. Clear lifecycle control. |
| Active check Edit | Opens edit modal. | Good. Allows schedule/severity/params changes. |
| Active check Archive | Deletes/archives the check. | Gap. No confirmation or undo despite operational impact. |
| Edit Check modal: Save | Patches name, severity, schedule, and params. | Mixed. Schedule edit is useful; raw JSON is fragile. |

Gaps:

- The create/edit modals do not validate JSON until submission.
- `custom_sql` or complex params would benefit from purpose-built fields and previews.
- Check actions do not route users to the resulting run after "Run"; they only update the table unless used from special contexts.

## Dataset Runs Tab

| Element | Click result | Rationale |
|---|---|---|
| Dataset link in RunsTable | Hidden on dataset-specific tab because the dataset context is already known. | Good. Avoids redundant navigation. |
| Exception count link | Navigates to `/exceptions?run_id=:id`. | Good. Directly opens violating rows. |
| Investigate link | Navigates to `/workbench?dataset_id=:id&run_id=:id`. | Good. Bridges execution history to analysis. |

Gaps:

- There is no run detail page or drawer.
- Successful runs have no drill-down, even though metrics/history may still matter.
- There is no direct "Start RCA for this run" action in each run row.

## Dataset Exceptions Tab

This tab uses the same triage component as the global Exceptions page, pre-filtered by dataset.

| Element | Click result | Rationale |
|---|---|---|
| Status chip | Filters exceptions by status and clears current selection. | Good. Keeps bulk selection safe across filter changes. |
| Select all checkbox | Selects all currently loaded exceptions. | Good. Efficient batch triage. |
| Row checkbox | Selects a row without opening detail. | Good. Event handling avoids accidental modal open. |
| Exception row | Opens the exception detail modal. | Good. Details are available without leaving context. |
| Acknowledge | Batch-updates selected exceptions to `acknowledged`. | Good. Supports triage workflow. |
| Mark expected | Batch-updates selected exceptions to `expected`. | Good. Important feedback loop for institutional memory. |
| Resolve | Batch-updates selected exceptions to `resolved`. | Good. Clean closure action. |
| Mute | Batch-updates selected exceptions to `muted`. | Mixed. Useful, but the long-term effect is not explained at click time. |
| Reopen | Batch-updates selected exceptions to `open`. | Good for recovery. Mixed because it is always shown even when selected records are already open. |
| Optional note field | Included with batch triage request. | Good. Captures context. Gap: no note requirement for high-impact statuses like expected/muted. |
| Detail modal: Investigate in workbench | Navigates to `/workbench?dataset_id=:id&exception_id=:id`. | Good. Strong row-level investigation path. |

Gaps:

- Batch action buttons are always shown in the same order regardless of selected rows' current status.
- No confirmation for Mute or Mark expected, even though they affect future interpretation.
- No direct link from global exception rows to dataset detail unless opened through Workbench.

## Dataset Dashboards Tab

| Element | Click result | Rationale |
|---|---|---|
| Focus input | Captures optional dashboard focus. | Good. Lets users guide generation. |
| Generate dashboard | Creates an ad hoc dashboard and opens it. | Good. Immediate result after generation. Disabled until profile exists. |
| Dashboard list item | Opens that saved dashboard and re-runs its panels. | Good. Clear saved artifact behavior. |
| Refresh | Re-runs current dashboard panels. | Good. Makes live-source behavior explicit. |
| Delete | Deletes the saved ad hoc dashboard. | Gap. No confirmation or undo. |
| Panel SQL/book icon | Toggles the SQL used by the panel. | Good. Builds trust and auditability. Mixed because the icon-only control may not be obvious without hover. |

Gaps:

- Existing dashboards are not auto-selected, so the right pane can feel empty until the user clicks one.
- Delete is immediate.
- No edit, duplicate, rename, or pin/favorite flow.
- The fixed two-column layout may need a mobile pass.

## Dataset Knowledge Tab

| Element | Click result | Rationale |
|---|---|---|
| Save knowledge | Persists business context, known issues, importance, owner, SLA, PII columns, and notes. | Good. Directly improves generated checks and investigations. |
| Form controls | Edit local knowledge state. | Good. Simple structured context capture. |

Gaps:

- No unsaved-changes warning when switching tabs.
- PII columns are free text and not validated against actual dataset columns.
- There is no history of knowledge changes or triage-derived memory.
- The explanatory side card is useful, but it repeats product behavior rather than giving the next best action.

## Dataset Root Cause Tab

| Element | Click result | Rationale |
|---|---|---|
| Investigate | Starts an RCA session for the dataset question and refreshes sessions. | Good. Clear question-to-report flow. Disabled without question text. |
| Investigation transcript details | Expands/collapses SQL transcript and results. | Good. Keeps reports readable while preserving evidence. |

Gaps:

- No cancel/stop control for a running RCA session.
- No direct "investigate latest failed run for this dataset" button here, even though global Runs has a latest-failure shortcut.
- If LLM is disabled, the page explains configuration, but there is no fallback heuristic RCA or direct Workbench suggestion.

## Checks - `/checks`

| Element | Click result | Rationale |
|---|---|---|
| Search input | Filters loaded checks by name, column, type, or dataset. | Good. Fast local inventory search. |
| Status chips | Refetch/filter checks by `all`, `active`, `proposed`, or `disabled`. | Good. Useful lifecycle filter. |
| Dataset link in check row | Navigates to `/datasets/:dataset_id/checks`. | Good. Preserves rule-management context. |
| Proposed/active check actions | Same as Dataset Checks Tab. | Good. Cross-dataset actions are available without forcing navigation. |

Gaps:

- No "New check" action here because creation requires choosing a dataset. That is reasonable, but the page could offer "Create from dataset" guidance.
- No filters for severity, origin, failing status, or schedule.

## Runs - `/runs`

| Element | Click result | Rationale |
|---|---|---|
| Root-cause latest failure | Starts RCA for the first failed/error run in the current data set, then navigates to `/datasets/:id/rca`. | Mixed. Excellent shortcut, but "latest" depends on the current fetched ordering and does not show the selected run strongly enough. |
| Status chips | Refetch/filter by `all`, `fail`, `warn`, `error`, or `pass`. | Good. Common operational filter. Gap: no `running` filter. |
| Dataset link | Navigates to `/datasets/:id`. | Good. Standard drill-down. |
| Exception count link | Navigates to `/exceptions?run_id=:id`. | Good. Direct failure triage. |
| Investigate link | Navigates to `/workbench?dataset_id=:id&run_id=:id`. | Good. Immediate analysis path. |

Gaps:

- No run detail page.
- The latest-failure RCA button is hidden when LLM is disabled or user lacks editor role; the page could still show a disabled action with a reason.
- No date range or search controls.

## Exceptions - `/exceptions`

| Element | Click result | Rationale |
|---|---|---|
| Dataset dropdown | Updates URL to `/exceptions?dataset_id=:id` and filters the triage list. | Mixed. Useful global filter, but it currently drops any existing `run_id` filter. |
| Status chips and triage controls | Same as Dataset Exceptions Tab. | Good. Same mental model globally and locally. |
| Exception row | Opens detail modal. | Good. Inspect row data without leaving queue. |
| Detail modal: Investigate in workbench | Navigates to `/workbench?dataset_id=:id&exception_id=:id`. | Good. Strong exception-to-analysis path. |

Gaps:

- Changing dataset while viewing a run-specific exception list clears the run context.
- Dataset names in the table are plain text, not links.
- No saved views such as "My team's critical open exceptions".

## Workbench - `/workbench`

| Element | Click result | Rationale |
|---|---|---|
| Connection dropdown | Changes the source connection for schema browsing and query execution. | Good. Workbench can be used outside a dataset. Mixed because existing SQL/results are not cleared when connection changes. |
| Schema table expand/collapse | Shows or hides columns for the table. | Good. Compact schema exploration. |
| Insert table name button | Inserts the table name into SQL, or creates `SELECT * FROM table LIMIT 50` when empty. | Good. Fast start. Gap: identifiers are not quoted, which can break for reserved words/special chars. |
| View DDL button | Opens a modal with the table/view definition. | Good. Keeps context local. |
| Column row | Inserts the column name into SQL. | Good. Efficient query writing. Same quoting gap. |
| DDL modal close | Closes definition modal. | Good. |
| SQL Run button | Executes guarded read-only SQL through `/query/run`. Editor/admin only. | Good. Safety model is visible in the helper text. |
| Ctrl+Enter | Runs SQL. | Good. Expert-friendly. |
| Limit dropdown | Changes result row limit for execution. | Good. Explicit performance/safety control. |
| Chart/Table toggle | Switches result display between table and chart when numeric columns exist. | Good. Useful lightweight visualization. |
| Chart type/X/Y dropdowns | Reconfigures the displayed chart. | Good. Makes quick charting flexible. |
| Suggested query Run | Inserts and executes the suggestion. | Good. Strong AI/heuristic assist. |
| Suggested query Edit | Inserts suggestion into editor without running. | Good. Safe review path. |

Gaps:

- No save/share query capability.
- No query history.
- Changing connection can leave stale SQL/results on screen.
- The default table insertion does not include schema.
- Viewer users can type/edit SQL but cannot run; this is safe but could be clearer.

## Estate Lineage - `/lineage`

| Element | Click result | Rationale |
|---|---|---|
| Connection dropdown | Updates URL to `/lineage?connection=:id` and loads that graph. | Good. Shareable estate graph per source. |
| Empty-state Add a connection | Navigates to `/connections`. | Good. Correct setup path. |
| Graph node for registered dataset | Navigates to `/datasets/:dataset_id/lineage`. | Good for lineage continuity. Mixed because it differs from attention/table links. |
| Graph hover | Highlights connected nodes/edges. | Good. Helps understand impact. |
| Needs attention item | Navigates to `/datasets/:dataset_id`. | Good. Opens problem dataset. Mixed because it lands on Profile, not Exceptions. |
| Relationship table open dataset | Navigates to `/datasets/:dataset_id`. | Good. Direct target access. Mixed because it does not preserve lineage tab context. |

Gaps:

- Inconsistent dataset destinations from graph, attention rail, and relationship table.
- Needs attention should probably open `/datasets/:id/exceptions` for failing/warn nodes.
- No search within graph.
- No "register this external table" action.

## Assistant - `/assistant`

| Element | Click result | Rationale |
|---|---|---|
| New conversation | Creates a chat session and selects it. Editor/admin only. | Good. Starts a durable assistant thread. |
| Conversation list item | Selects that session and loads messages over WebSocket. | Good. Standard chat history pattern. |
| Delete conversation | Deletes the session. | Gap. No confirmation or undo. |
| Empty-state suggestion chip | Creates/selects a session if needed and sends the prompt. | Good. Excellent onboarding affordance. |
| In-session suggestion chip | Sends the prompt. | Good. Fast common tasks. |
| Composer Enter | Sends message when connected and not busy. | Good. Chat-standard behavior. |
| Composer Shift+Enter | Adds a newline. | Good. Standard long-message behavior. |
| Send | Sends the typed prompt over WebSocket. | Good. |
| Stop | Sends stop signal while busy. | Good. Gives user control over long agent work. |
| Reconnect | Attempts WebSocket reconnection after mid-turn disconnect. | Good. Useful recovery. |
| SQL/tool activity details | Expands/collapses tool calls/results. | Good. Builds trust while avoiding noise. |

Gaps:

- Delete conversation is immediate.
- The composer is disabled until a session exists, but the placeholder says "Start a new conversation"; users must infer they need New Conversation or a suggestion chip.
- No rename/pin/search sessions.
- No visible permission upgrade path for viewer users.

## Settings - `/settings`

| Element | Click result | Rationale |
|---|---|---|
| Add server | Opens Add MCP Server modal. Admin only. | Good. Keeps advanced integration scoped to admins. |
| MCP Enable/Disable | Toggles server enabled state. | Good. Simple operational control. |
| MCP Delete | Deletes the server. | Gap. No confirmation or undo. |
| Add MCP modal: Add | Creates MCP server and closes modal. | Good. Tokens are write-only. Gap: no test connection. |
| Add MCP modal: Cancel/X | Closes without saving. | Mixed. Unsaved edits lost silently. |
| Invite user | Opens Invite User modal. Admin only. | Good. Standard user-management path. |
| Invite User modal: Create user | Creates user and closes modal. | Good. Straightforward. Gap: creates password directly; no invite email/reset flow. |
| Role dropdown | Immediately updates the user's role. Disabled for current user. | Mixed. Efficient admin control, but immediate mutation can surprise. |
| Deactivate/Activate | Immediately toggles account active state. | Mixed. Useful, but should confirm deactivation. |

Gaps:

- Non-admin users see health but cannot do anything about user management, which is correct; the page could still explain whom to contact.
- No audit/history of user or MCP changes.
- No "test MCP server" action.

## Recommended Navigation Improvements

1. Add breadcrumbs to Dataset Detail, Workbench, RCA, and filtered Exceptions.
2. Add a run detail route or drawer and make run rows clickable.
3. Make stat cards on Home clickable to their filtered views.
4. Standardize lineage destinations.
5. Add confirmations or undo for all destructive actions.
6. Replace raw JSON check forms with generated controls.
7. Preserve query params when filters change, especially on Exceptions.
8. Add direct shortcuts from Datasets rows to Checks, Runs, and Exceptions.
9. Add first-run onboarding: Add connection -> Browse tables -> Register -> Profile -> Generate checks.
10. Add clear disabled-state reasons next to actions that require profile, role, LLM, or selected rows.
