# Autoresearch product design

This is the design reference for the workbench. The user chose the **ElevenLabs application** as the product reference on 11 October 2026. Apply these rules to new screens and changes to existing ones.

## Reference and observations

The live [ElevenLabs Home](https://elevenlabs.io/app/home) and [Text to Speech workspace](https://elevenlabs.io/app/speech-synthesis/text-to-speech) were inspected in the browser. These are application screens, not the marketing website. The reference requires an account. No account content or screenshots are included in this public repository.

Observed patterns:

- A white working surface and a slightly gray navigation sidebar, separated by a fine line.
- A compact utility header with the current tool, navigation controls and secondary actions.
- Mostly black and gray controls. The principal work action uses a black filled button.
- A quiet selected navigation row, outlined icons and short sentence-case labels.
- A large working area with secondary settings and history in an adjacent panel.
- Rounded inputs and buttons, thin neutral borders, little elevation, and readable regular-weight type.
- Options appear near the work they affect. Detailed controls are disclosed when needed.

Our sizes, colors and fonts below are implementation choices inspired by those observations. They are not claimed to be ElevenLabs' extracted CSS or official design tokens. Use Autoresearch's own identity, content and interaction model.

## What changes from the first build

The first workbench used a blue accent across the brand, actions, selections and tabs; a cold gray canvas; a tall budget card; and repeated oversized section titles with numbered uppercase labels. That hierarchy made every screen read like a presentation.

Use a neutral application shell. Put the campaign goal and the next useful action first. Keep the budget legible in a compact row. Let the protocol, experiments and measurements occupy the working space. Instructions should help someone make a decision; move reference material and implementation details into disclosures.

## Foundations

| Token or rule | Choice |
| --- | --- |
| Canvas / working surface | White, `#ffffff` |
| Navigation surface | Near-white neutral, `#fafafa` |
| Subtle / selected surface | `#f5f5f5` / `#eeeeee` |
| Main text | `#171717` |
| Secondary text | `#666666`; never use faint text for essential instructions |
| Divider / control border | `#e8e8e8` / `#d9d9d9` |
| Primary action | `#171717`, white label; hover `#333333` |
| Positive | Dark green text with a pale green background, only for an actual positive state |
| Attention | Dark amber text with a pale amber background, only for unresolved or blocked work |
| Error / destructive action | Dark red text; filled red is reserved for a destructive confirmation |
| UI type | Locally bundled Inter, chosen deliberately for this application reference |
| Code / exact values | Locally bundled IBM Plex Mono; use tabular numerals for numeric comparisons |
| Type scale | Page title 24–28px; section title 16–18px; body/control 13–14px; secondary label 12px |
| Line height | 1.4–1.55 for prose; compact but readable table rows |
| Space scale | 4, 8, 12, 16, 24, 32px |
| Corners | Controls 8px; grouped panels 10–12px; avoid rounding every item into a pill |
| Border | 1px; use spacing instead of nested bordered cards |
| Shadow | None on ordinary panels; a light neutral shadow only for overlays |
| Icons | One outline family, Lucide; 16–18px in controls, consistent stroke; decorative icons hidden from assistive technology |
| Motion | 120–200ms for feedback and disclosure; honor reduced motion; no ornamental movement |

Represent these choices as CSS custom properties. Do not scatter one-off colors, border radii or transition timings through components. Semantic colors supplement text and icons; color alone never communicates the result.

## Layout and navigation

- Use a roughly 240px collapsible sidebar. A campaign search filters real campaign names and goals. Include a clear reset when there are no matches.
- Use a roughly 52px utility header with the current location and a small number of working controls. No decorative product slogan above every screen.
- Let the content use the available width. Keep long prose readable, and give comparison tables the width needed for units and intervals.
- Use a secondary panel around 280–320px when settings or contextual guidance benefit from it. A panel is optional, not a reason to squeeze every screen into three columns.
- Keep campaign actions near the title. Use one visually dominant action appropriate to the state: assess, approve, run, resume or review results. Never start work because a panel was opened.
- Overview is the campaign entry point. Preparation, Experiments, Comparison, Activity, and Report & export provide the detailed workflows. Do not add navigation destinations without a working screen.
- Below tablet width, navigation becomes a dismissible drawer and secondary panels stack. Use `min-height: 100dvh`. At narrow widths, tables may scroll inside their own region; the whole page must not overflow.

## Workflows and progressive detail

Overview: show the objective, observed KPI values, uncertainty and current work before the historical detail. Group attempts by research strategy, keeping the hypothesis, expected effect and recorded outcome close together. Make failures, unresolved results and untested strategies visible. Each measured result opens its exact experiment evidence. A metric's best confirmed value identifies its candidate and paired reference; values selected from different candidates never form an imaginary combined solution. Parallel preparation and serialized measurement are distinct visible phases, with an explicit task limit.

New campaign: lead with the optimization goal, repository and finite budget. Make execution/model settings secondary but available before creation. Guide users through an existing benchmark command and target metric; retain JSON import for advanced protocols. Examples can prefill a draft; they never invent approved measurements or execute automatically.

Preparation: state what can be measured, what is missing, and the next action. Show a human-readable protocol summary before its exact JSON and hash. Human approval continues to bind the precise protocol version. Changing controls cannot silently preserve an old approval.

Experiments: lead with the hypothesis and outcome, not a 32-character ID. Show strategy, lineage and the reason a run was rejected or skipped. Keep patches, revisions, raw outputs and provenance accessible in details.

Comparison: show the target metric, baseline, candidate, unit and uncertainty. Keep constraints visible. A large improvement percentage cannot replace independent confirmation. Preserve failed, duplicate and inconclusive attempts in history; never style them as wins.

Activity: use a chronological event list with readable descriptions. Technical payloads expand in place. Auto-refresh must not steal focus, reset an open disclosure or overwrite an edited draft.

Report: give a concise conclusion and concrete export/reproduction actions. Draft-PR preparation and publication remain distinct. Publication requires an explicit destination and review of the exact confirmed payload.

## Budget and control are product information

The limit, consumed, engaged, protected-to-finish and available amounts remain visible while reviewing a campaign. A compact row is appropriate; a decorative chart is not required. The accounting scope and enforcement mode remain reachable in one disclosure. Estimated accounting must remain clearly labeled.

Keep Pause and Stop reachable during execution. Recovery must explain what is unknown and display the engine's supported action. Reconciliation does not start work, refund money, or imply that remote billing stopped. A redesign cannot weaken any approval, budget or execution gate.

## Interaction and accessibility

- Every control has a useful hover, pressed, disabled and keyboard-focus state. Use a visible neutral focus ring with sufficient contrast.
- Label inputs explicitly. Keep errors near the relevant input and preserve entered values after an API failure.
- Use real buttons and links. Icon-only controls need an accessible name and a tooltip where helpful.
- Opening navigation or an advanced panel does not submit a form. Escape closes temporary navigation; return focus to its trigger.
- Maintain normal keyboard order and the skip-to-workspace link. Touch targets should reach 44px where practical.
- Loading, no campaigns, no search matches, no measurements, disconnected API, budget block and coordinator recovery are designed states.
- Do not copy upgrade banners, fictitious users, usage numbers, decorative charts or media-generation controls from the reference. The product's real state supplies its content.

## Review before delivery

Inspect the live app on a desktop viewport and a narrow viewport. Check creation, approved protocol, populated comparison, activity and report. Exercise sidebar collapse/search, keyboard focus, validation errors, recovery and the strict-provider gate. Verify that exact metrics, units, uncertainty and budget amounts remain available. Run the relevant React tests and production build.

If a new screen feels like a landing page, a terminal or a grid of dashboard cards, revisit the hierarchy against the observed ElevenLabs workspaces. These guidelines take precedence over generic visual flourishes suggested by design skills.
