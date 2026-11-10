# AURORA design language

## Identity
- Name: AURORA. Tagline: "Soft X-ray XAS and XRF stacks from the SGM beamline, Canadian Light Source".
- Mark: rounded square, night-sky gradient, three aurora ribbons (violet, teal, green) whose upper edge is a XANES spectrum, a crisp white line on top, four stars (the four SDDs), a thin horizon bar (the beam). Monochrome variant for 16-32 px and the menu bar on macOS (template icon).
- Wordmark: AURORA in Inter Bold, letter-spacing 8 % of the size, with the mark on the left.

## Colour tokens
| Token | Hex | Use |
|---|---|---|
| night | #0A1430 | title bar, splash, dark theme paper |
| rail | #101E4A | perspectives rail |
| aurora | #38E0C2 | selected perspective, highlights on dark |
| action | #0E7C6B | primary buttons, links on light (4.5:1 on white) |
| violet | #A78BFA | E0 markers, secondary accents |
| warn | #F2A65A | stack bounds, warnings |
| paper | #F3F5F9 | window background (light theme) |
| card | #FFFFFF | panels |
| ink | #15213B | text |
| muted | #5B6B8C | secondary text (4.6:1 on white) |
| line | #DDE3EE | borders |
| ok / warn / bad chips | #E3F8F3 / #0A5F52, #FFF1DD / #8A4B00, #FDE2E2 / #9B1C1C | status chips |

Plots: viridis for maps (magma for XRF/I0 survey maps as in John's export), Okabe-Ito for traces, white plot background in both themes (publication figures), gridlines off by default.

## Type
- Inter (UI): 13 px body, 11 px rail labels, 18 px perspective titles, 13 px semibold section titles.
- JetBrains Mono: every number, path, channel range, energy, so columns align.
- Both OFL. Neither is bundled: the QSS asks for them first and falls back to Segoe UI / SF Pro Text and Consolas /
  SF Mono / Menlo (`UI_FONT`, `MONO_FONT` in `aurora/app/theme.py`).

## Layout
- Top bar 48 px (mark, AURORA, session/day/holder breadcrumb, search, sync/save).
- Left rail 92 px with icon + label per perspective: Home, Survey, Reduce, Check, Compare, Fit, Maps, Report, Settings at the bottom. Selected = aurora pill.
- Workspace: numbered cards ("1 · Scans", "2 · Region...") in the order the user works, 12 px gaps, 12 px radius, 1 px line border. Primary action at the right end of the last card.
- Status chips in the header row (loaded, saturated, rotated, empty) instead of pop-ups.
- Every plot has hover readout, right-click menu, export in PNG/SVG/PDF/CSV.
- Phone-width is irrelevant; minimum window 1200 × 760, panels become scrollable, never clipped.

## Tone
- Calm: "SDD3 deviates from the other three" not "ERROR". Facts first, suggestion second ("lower spike sensitivity?").
- Nothing is hidden: settings used are printed in the output header and in the log panel.

## Qt implementation notes
- QSS theme generated from the tokens (light and dark), `QPalette` set too so native dialogs match.
- Rail = QToolBar vertical with QToolButtons (TextUnderIcon), icons from the inline SVG set in `resources/icons` (stroke 1.8, 22 px).
- Cards = QGroupBox styled flat with a title label; numbered titles are plain text.
- Fonts loaded with QFontDatabase at start; fall back to Segoe UI / SF Pro / system mono.
