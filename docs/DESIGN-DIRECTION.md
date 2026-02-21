# Domus — Design Direction

How Domus looks, feels, and communicates. This document governs every visual decision.

---

## How to Use This Document

This is the **design authority** — it defines what Domus should feel like, the constraints that govern every decision, and the principles that prevent drift toward generic SaaS aesthetics.

**This document defines:** Philosophy, emotional identity, non-negotiable constraints (P1–P12), interaction patterns, component intent, and a validation checklist.

**Source code defines:** Token values, exact dimensions, component implementations, animation parameters. Source is the single source of truth for all implementation details.

**The workflow:** Read this document for *intent and constraints*. Read canonical source files for *exact values*. If source contradicts this document on a design principle, this document wins — update the source. If this document duplicates an exact value that also exists in source, remove it from this document.

### Source Map

| Concern | Canonical Source |
|---|---|
| Color, spacing, typography, radius, shadow tokens | `tokens/tokens.css` |
| Form primitives (Button, Input, Select, Toggle, Checkbox) | `core/ui/` |
| Entity chrome (Window, Card) | `core/entity/` |
| Prompt bar & conversation panel | `core/chat/` |
| Canvas, viewport culling, pan/zoom | `core/canvas/` |
| Bottom sheet | `core/sheet/` |
| Context menu | `core/ui/context-menu.tsx` |
| App Dock | `core/canvas/AppDock.tsx` |
| Animation config (spring parameters, duration tiers) | `lib/motion.ts` |
| App type definitions, entity model | `lib/types.ts` |

If a canonical source file doesn't exist yet, create it following the principles in this document. The first implementation becomes the canonical reference — subsequent work reads from it.

---

## Core Premise

Domus is an environment that responds to you. The design must make the agent's actions feel **spatial** (things appear, move, glow, fade) rather than **textual** (chat bubble, chat bubble, chat bubble). The UI is a room, not a feed.

The agent is not a chatbot that happens to have a canvas. The canvas IS the interface. Chat is one input surface among many.

---

## Design Lineage: OS1 → Domus

Domus is a rewrite of [OS1](https://github.com/TheRealClodius/OS1), the previous version of this project. OS1 established the core design identity — Domus inherits its visual language and evolves it into a more complete spatial system. Understanding what carries over and what changes prevents drift toward generic SaaS aesthetics.

### What We Inherit from OS1

**Warmth as identity.** Warm tonal surfaces as the core visual identity. Every UI element participates in a warm color system — depth comes from transparency and tonal shifts, not from competing colors. Warm hue tints on every surface, accent scarcity, and the feeling that the interface is a *place* rather than a *page*.

**The interface as environment.** The screen as a spatial surface, not a document. The canvas is a room you walk into, not a page you scroll through. Entities have positions, not rows.

**Agent presence as motion.** Visual indicators communicate agent activity and aliveness. The agent glow: a warm halo on entity borders that fades over seconds, communicating "the agent was just here" without a dedicated animation widget.

**Radical restraint.** Limited color, minimal chrome, opacity as the primary tool for hierarchy. One typeface, constrained sizes, accent color in exactly three places.

**Glassmorphism on overlays.** `backdrop-filter: blur()` with semi-transparent surfaces on overlay elements (prompt bar, conversation panel, context menus, bottom sheet) while flat tonal surfaces for multiplied entity elements.

### What We Evolve

| OS1 Approach | Domus Approach | Why |
|---|---|---|
| Monochrome-leaning palette | Semantic token system (tonal palettes from seed hues) | Multiple entity types and states — pure monochrome can't communicate enough. Tokens preserve warmth while adding semantic range. |
| Depth from transparency layers | Shadows for entities, blur for overlays | Spatial canvas with overlapping windows — shadows communicate stacking order. Overlay surfaces keep OS1's blur. |
| Single-surface interaction | Windowed spatial canvas (entities in draggable windows/cards) | Workspace with rich visual entities — needs the window metaphor. |
| Ultra-light typography | Functional typography | Dense information in windows — readability wins over aesthetics. |
| Pill-shaped / organic geometry | Soft rectangles (token-based radius scale) | Entities contain structured content. Rectangular containers are functional. Generous radius keeps it soft. Prompt bar retains pill shape. |

### The Emotional Test

When evaluating any Domus UI, apply this gut check:

1. **Does it feel warm?** — If it could be a Notion clone or a generic dashboard, it's too cold. The warm hue tint in surfaces should be perceptible.
2. **Does it feel quiet?** — If your eye is pulled in multiple directions by competing colors or chrome, it's too noisy. The agent glow should be the loudest thing on screen.
3. **Does it feel spatial?** — If it reads like a list or a page, it's too flat. Entities should feel like objects in a room.
4. **Does it feel alive?** — If nothing moves or glows, the agent feels absent. The glow and animations are how the agent's presence is *felt*, not just read.

---

## Core Design Patterns

Non-negotiable constraints. Every component, every screen, every UI element must conform. Check your work against each pattern.

### P1: Token-Only Color

Never use raw color values in components. Every `bg-`, `text-`, and `border-` class must reference a semantic token. No `bg-gray-100`. No `#d1684e`. No `rgb(...)` in component code.

If you need a color that doesn't have a token, extend the design system in `tokens.css` first. Don't work around it with a hardcoded value.

**Rationale:** The warmth of Domus is encoded in the token pipeline. Raw colors bypass the tonal system and break theme consistency.

### P2: Depth Through Elevation and Layering

Depth comes from two mechanisms: the shadow scale (`shadow-resting` → `shadow-elevated`) and the 7-level surface tone scale (`surface-dim` → `surface-lowest`). See the Tonal Logic section for the full hierarchy.

For **entity container surfaces** (windows, cards): flat tonal backgrounds with shadows. No gradients. No blur on the container itself. These are multiplied across the canvas — they must be cheap to render.

For **overlay surfaces** (prompt bar, conversation panel, context menus, bottom sheet, popovers): `backdrop-filter: blur()` with semi-transparent backgrounds. These are singleton elements that float above the entity layer.

**Clarification:** Transient overlays *within* entity windows (dropdowns, select panels, popovers) may use blur — they are singleton overlays, not entity container surfaces.

No borders stacked on borders to fake depth. No background images or noise textures.

### P3: The Agent Glow Is Sacred

The warm glow on entity borders is the single most important visual signal in Domus. It means *"the agent just did something here."* No other UI element may use a similar glow effect.

Don't add glows to buttons, inputs, hover states, or decorative elements. The glow is reserved exclusively for agent-origin entity changes.

**Rule:** If `created_by === 'agent'` and the entity was touched recently → glow. Otherwise → no glow. No exceptions.

→ *Glow implementation: entity chrome components in `core/entity/`*

### P4: Two Font Families, Three Sizes, Two Weights

Two font families serve distinct roles:

| Variable | Font | Use |
|---|---|---|
| `--font-body` | Inter (via `next/font`) → system stack fallback | All UI chrome, body text |
| `--font-display` | Kalice Trial → body fallback | Card titles, space header, display type |

Three font sizes for chrome:

| Token | Use |
|---|---|
| `text-body` | Everything — default for all UI chrome |
| `text-label` | Metadata, timestamps, entity type badges |
| `text-title` | Window titles, section headers |

Rendered content inside entities (markdown, rich text) uses an extended content typography scale for headings, code, blockquotes, and lists. These extended sizes only exist inside entity content areas, never in chrome.

No bold body text. No italic for emphasis. If your chrome element needs a font size outside the three-size table, the design is wrong — restructure it.

→ *Token values: `tokens/tokens.css`. Content typography: entity content components.*

### P5: Spacing Is a Multiple of 4

Every margin, padding, and gap is a multiple of 4px. Use the token scale defined in `tokens.css`. Don't eyeball spacing — use the tokens.

Spacing between elements encodes the relationship between them. Three relational levels:

- **Tight coupling** — heading → paragraph, icon → label
- **Sibling elements** — paragraph → paragraph, list items, form fields
- **Content → action** — body text → CTA button, description → action bar

→ *Spacing scale and relational gap tokens: `tokens/tokens.css`*

### P6: Agent Animates, User Is Immediate

When the agent creates, moves, or updates an entity: animate with spring physics. When the user drags, resizes, types, or clicks: zero transition delay, instant response.

This asymmetry is how the user subconsciously distinguishes "I did that" from "the agent did that." It's communicative, not cosmetic.

Three duration tiers: fast (micro-interactions), medium (component transitions), slow (entity creation/archival). Plus the agent glow fade, which is deliberately slow because it's ambient.

→ *Spring parameters and duration values: `lib/motion.ts`*

### P7: Accent Scarcity

The `primary` color appears in exactly three contexts:

1. Focused entity borders
2. Interactive element hover states
3. The agent glow

**Exception:** Focus rings on interactive elements use `primary` at low opacity for accessibility. These are low-intensity indicators, not decorative accents — they don't compete with the agent glow.

Beyond this, `primary` needs explicit justification. Color scarcity is what makes the agent's actions visible. If everything is colorful, nothing stands out.

### P8: No Chrome Sprawl

The total icon budget:

- App icons in entity headers (one per window)
- App icons in the App Dock (one per app type)
- Window controls: close (top-left), plus app-specific option buttons (top-right)
- Chat send button
- Context menu item icons (where semantically useful)

That's it. No icon-heavy toolbars. No floating action buttons. Every icon added dilutes the spatial interface.

### P9: Flat Surfaces, Real Shadows

Entity surfaces are flat solid colors from the tonal palette. Shadows are the sole elevation indicator for entities. Radius is soft on everything, but nothing is circular except avatars.

Overlay surfaces use glassmorphism (semi-transparent + blur). This visually separates the spatial entity layer from the floating chrome layer.

No gradients. No noise textures. No background images.

### P10: Entities, Not Pages

There are no "pages" in Domus. Everything is an entity rendered at a position on a spatial canvas. If you're building something that feels like a full-page layout — you're building the wrong thing. Build an entity type that renders inside a window or card.

### P11: Respect User Preferences

Honor `prefers-reduced-motion` (all animations → instant, glow → static border highlight), `prefers-color-scheme` (automatic theme switching), and system font size settings. Domus lives inside the user's OS — it doesn't fight the environment.

### P12: Inline Feedback, No Interruptions

Errors, confirmations, and status updates appear inline — inside the chat flow as chips, inside entity chrome as state changes, or as the agent glow. Never use toast notifications, snackbars, or banners. Modals are acceptable only for destructive action confirmations.

Entities loading async content use warm shimmer placeholders — not heavy skeleton screens that mimic final layout.

If the agent fails, it says so in chat. If it succeeds, the entity glows. The spatial interface is the feedback mechanism.

### P13: Sibling Elements Share a Bounding Box

When elements appear as siblings in a list, row, or grid, they must share identical bounding boxes — even if their visual content differs in shape or density. A lucide icon next to a custom illustration next to a brand logo: all three sit inside the same-sized container, centered within it. The bounding box is the alignment primitive, not the content edge.

This prevents ragged visual alignment without requiring every icon to be the same shape. It also means swapping one icon for another never shifts layout.

---

## Design System Concepts

The design system is implemented in `tokens.css` and the component library. This section explains the *thinking* behind the system — the intent that should guide implementation and extension.

### Tonal Logic

We adopt Material Design 3's **relational color system** — not its components, not its specific palettes, but its core insight: colors are generated from relationships, not picked from swatches. Specifically, we use MD3's tonal elevation model: surfaces at different nesting depths use different tonal stops, and contrast is guaranteed by maintaining minimum OKLCH Lightness distance between surface/text pairs.

1. **Seed hues** — Two brand hues define the identity. A primary (purple, 264°) and an agent accent (orange, 40°). Surface hue tracks the seed hue at very low chroma (C=0.01) — subtle enough to be barely perceptible, but tonally cohesive with the primary accent.

2. **Tonal palettes** — From each seed, generate a multi-step tonal palette in OKLCH. Light theme pulls from the light end. Dark theme pulls from the dark end. Same hues, different tones.

3. **7-level surface hierarchy** — Inspired by MD3's surface container scale. Ordered brightest → dimmest in both themes so the same Tailwind class produces the correct visual hierarchy regardless of theme:

| Role | Usage | Light L | Dark L |
|---|---|---|---|
| `surface-lowest` | Entity chrome — windows, cards. Always brightest. | 1.00 | 0.24 |
| `surface-low` | Cards inside windows, secondary surfaces | 0.97 | 0.20 |
| `surface-bright` | Bright accent surface | 0.98 | 0.26 |
| `surface` | Base — input wells, nested cards, code blocks | 0.955 | 0.18 |
| `surface-high` | Sheets, popovers, elevated panels | 0.94 | 0.16 |
| `surface-highest` | Dialogs, modals — maximum depth | 0.92 | 0.15 |
| `surface-dim` | Canvas background — always dimmest | 0.90 | 0.14 |

**Nesting rule:** each layer deeper picks the next surface level. Window (`surface-lowest`) → card inside window (`surface-low`) → input well inside card (`surface`).

4. **Semantic roles** — Every surface, text, and border maps to a role:

| Role | What it means |
|---|---|
| `on-surface` | Primary text on any surface |
| `on-surface-muted` | Secondary text, metadata |
| `outline` | Strong borders — focus rings, explicit dividers |
| `outline-variant` | Subtle borders — card edges, separators, grid lines |
| `primary` | Brand accent, interactive elements |
| `on-primary` | Text on primary-colored backgrounds |
| `primary-container` | Colored background for selected states, badges |
| `on-primary-container` | Text on primary-container backgrounds |
| `secondary` / `on-secondary` | Supporting UI — same hue as primary, lower chroma |
| `secondary-container` / `on-secondary-container` | Secondary-colored surface + text pair |
| `tertiary` / `on-tertiary` | Contrasting accent — offset hue (+60° from seed), medium chroma |
| `tertiary-container` / `on-tertiary-container` | Tertiary-colored surface + text pair |
| `agent` | Agent-origin indicator (the glow) |
| `agent-container` / `on-agent-container` | Agent-colored surface + text pair |
| `error` | Error states |
| `error-container` / `on-error-container` | Error-colored surface + text pair |

5. **Contrast rule** — Minimum OKLCH Lightness distance between surface and text: `|L(surface) - L(on-surface)| ≥ 0.50` for primary text (WCAG AA), `≥ 0.40` for muted text (3:1 for secondary). All accent container pairs maintain ≥ 0.70 L-distance.

6. **Scheme variants** — Named presets that control chroma/hue relationships across all palettes. The user picks a variant; the token pipeline adjusts everything downstream:

| Variant | Effect |
|---|---|
| `tonal` (default) | Base chroma levels. Balanced, quiet. |
| `vibrant` | 1.5× chroma multiplier. Surfaces gain subtle tint. |
| `muted` | 0.6× chroma. Near-monochrome but retains hue identity. |
| `expressive` | 1.2× chroma. Secondary hue shifts +30° from primary for more color variety. |
| `monochrome` | All chroma → 0.005. Pure grayscale. Agent glow and error retain their sacred hues. |

Settings UI exposes variant picker + intensity slider (chroma multiplier). Saved themes capture variant + hue + intensity.

7. **Hover state layers** — M3 state layer pattern: hover states use semi-transparent `on-surface` overlays (`hover:bg-on-surface/8`) instead of fixed surface levels. This guarantees visible contrast against any background surface, regardless of nesting depth or scheme variant. Never use `hover:bg-surface-*` for interactive list items on surface containers — the lightness step between adjacent surface levels is too small to be perceptible.

8. **No raw colors in components.** Every `bg-`, `text-`, `border-` class references a semantic token. If you write `bg-orange-500` in a component, you're doing it wrong. Write `bg-agent` or `bg-primary`.

→ *Token definitions, theme values, and Tailwind v4 integration: `tokens/tokens.css`*

### Typography Principles

Two typefaces: Inter for body text (via `next/font/google`), Kalice Trial for display/title type. Two weights. Three sizes for chrome. An extended scale for rendered content inside entities.

Inter provides a clean, functional reading experience. Kalice Trial adds personality to card titles and space headers — the display font is intentionally limited to decorative/title contexts.

Monospace content uses the system monospace stack. Line-height is consistent across all sizes for comfortable readability.

→ *Type scale values: `tokens/tokens.css`. Content typography styles: entity content components.*

### Spacing Principles

4px base unit. Everything is a multiple of 4. Spacing is semantic — the gap between two elements reflects their relationship (tightly coupled, siblings, or separated by intent).

Container internal padding is consistent and defined by token. No magic numbers.

→ *Spacing scale and relational gap tokens: `tokens/tokens.css`*

### Radius Principles

Domus is soft but not bubbly. A six-step scale from tight to generous, matched to Figma specs:

| Token | Value | Use |
|---|---|---|
| `radius-xs` | 4px | Bubble corners, small button padding |
| `radius-sm` | 6px | Buttons, inputs, chips |
| `radius-md` | 10px | Dropdowns, popovers |
| `radius-lg` | 12px | App dock, folder thumbnails, bubble sides |
| `radius-xl` | 16px | Chat sidebar |
| `radius-2xl` | 20px | Windows, cards, bottom sheets, prompt input |

Nothing is circular except avatars.

**Concentric radius rule:** Inner elements derive their radius from the parent to maintain visual concentricity. `child-radius = parent-radius - parent-padding`. If the result is ≤ 0, no radius.

→ *Radius values: `tokens/tokens.css`*

### Shadow & Elevation

Shadows are the sole depth cue for entities. Dark theme shadows are stronger to maintain perceptibility.

| Token | Use |
|---|---|
| `shadow-card` | Cards, thumbnails — lightweight single-layer shadow |
| `shadow-resting` | Default entity elevation at rest |
| `shadow-window` | Focused windows — wider spread, medium-lightness warm neutral |
| `shadow-elevated` | Sheets, popovers |
| `shadow-overlay` | Prompt bar, conversation panel |

Entities have exactly two elevation levels: **resting** (unfocused) and **active** (focused). There is no additional elevation for dragging or resizing — interaction does not change shadow.

#### Tonal shadow rule

Shadow color must be tonally matched to the surface family. All Domus surfaces use **hue 55** (warm neutral). Shadow tokens use `oklch(L C 55 / α)`:

- **Light mode base**: `oklch(0.1 0.015 55)` — near-black warm neutral, low opacity (7–16%)
- **Dark mode base**: `oklch(0.04 0.01 55)` — near-black warm neutral, higher opacity (25–65%) to compensate for dark canvas
- **`shadow-window` (light)**: `oklch(0.55 0.02 55 / 0.3)` — medium warm-neutral halo; intentionally lighter to create a soft lift above the near-white canvas

Do **not** use `rgb(0 0 0 / α)` (achromatic) or cool-tinted greys — they read as slightly off against warm surfaces. Do **not** use the same opacity in dark mode as light mode — dark canvas (L=0.14) requires higher opacity to produce the same perceived contrast.

→ *Shadow values: `tokens/tokens.css`*

---

## Visual Feedback Vocabulary

The agent acts on the world. The user must see those actions *spatially*, not just read about them in chat. Every agent action has a visual consequence.

### Entity States

| State | Visual Treatment |
|---|---|
| **Agent-creating** | Scales up from origin point, flies to resting position. Agent glow on border. Spring easing, crisp settle. |
| **Agent-updating** | Brief pulse on the changed region — a highlight sweep across the updated content area. |
| **Agent-moving** | Smooth spring position transition. The entity glides, not teleports. |
| **User-dragging** | No transition. Direct 1:1 pointer tracking. Shadow stays at current level (no elevation change). |
| **Focused** | Active shadow. Title bar at full opacity. |
| **Unfocused** | Resting shadow. Title bar dims. Content stays readable. |
| **Archiving** | Scale-down toward origin point, opacity fades. Reverses the creation animation. |

→ *Animation implementations: `lib/motion.ts` and entity chrome components.*

### The Agent Glow

When the agent creates or significantly updates an entity, it gets a **warm glow** — a soft colored shadow that fades over seconds. This is the single most important visual signal in Domus. It answers: "what did the agent just do?"

- Appears instantly on agent action
- Fades to normal shadow gradually
- Only on entity chrome (window/card border), not on content
- Color comes from the `agent` token — warm, not neon

→ *Glow CSS and timing: `core/entity/` chrome components.*

### Canvas Indicators

| Indicator | What it communicates |
|---|---|
| **Agent activity pulse** | Agent is processing. Subtle radial pulse from the chat area — concentric rings at very low opacity, expanding outward. Sonar ping. |
| **Entity connection lines** | Two entities are related. Thin dashed lines shown on hover of either entity or when the agent references the relationship. Uses `outline` token. |
| **Drop zone** | Entity being dragged near a rearrangement target. Faint highlight rectangle. Free placement, no snapping. |

### Chat Indicators

The chat panel is the secondary interface. It confirms what the spatial UI shows, it doesn't replace it.

| Indicator | Visual |
|---|---|
| **Agent thinking** | Animated ellipsis. Text appears in chunks as it streams. |
| **Tool call in progress** | Inline chip with shimmer: `[creating note...]` |
| **Tool call complete** | Chip resolves to entity name as clickable link → focuses entity on canvas. |
| **Error** | Error-tinted chip inline in conversation. No modal. No toast. |
| **Model indicator** | Tiny muted label below agent messages. Only shown if multi-model is enabled. |

### Entity Transitive States

Entities are not always settled. They load data, get created, get archived.

| State | When | Visual Treatment |
|---|---|---|
| **Loading** | Content being fetched/generated | Entity chrome renders immediately. Content area shows a warm shimmer — abstract placeholder blocks, not a structural skeleton. Agent glow active if agent-created. |
| **Creating** | Tool call in flight, entity not yet persisted | Chat chip shows shimmer. Entity appears on canvas only when persisted. |
| **Archiving** | Being removed | Scale-down + fade animation. Spring easing. |
| **Error** | Failed to load or action failed | Content area shows centered muted error text with accent left border. Inline, not modal. |
| **Content pending** | Item sent but not yet confirmed (optimistic update) | Item renders immediately with a muted pending indicator. On server confirmation: clear pending state, swap temp ID for real ID. On failure: show inline error on the item with retry affordance. Never use modals or toasts for send failures — the item itself communicates its state. |

**Loading shimmer is not a skeleton screen.** It doesn't mimic the exact layout of final content. It's a minimal, warm indicator — a few abstract rounded-rectangle blocks on a sunken background. When content arrives, cross-fade to real content.

### Empty States

Every entity that can contain dynamic content must define an empty state.

- **Empty canvas:** Centered muted text — "Talk to the agent or open an app from the dock." No illustrations. No onboarding wizard.
- **Empty entity:** Centered muted text following the pattern "[action verb] to get started." No decorative graphics.
- **No search results:** "No results for [query]."

---

## Component Patterns

These describe the *intent and structure* of core components. Exact dimensions, padding, and styling live in the canonical component implementations.

### Space Header

Full-width bar at the top of the canvas. Displays the space name in `font-display` and pill-shaped action buttons (favorite, switch space).

- Pill buttons: glassmorphic background (`rgba(255,255,255,0.64)`), 0.5px white border, asymmetric radius (rounded on three corners, sharp on bottom-left). Icon-only, 32px height.
- Left group: space name + star pill. Right group: swap pill.

→ *Implementation: `core/canvas/SpaceHeader.tsx`*

### Windows

```
┌──────────────────────────────────────┐  ← rounded, active shadow (focused)
│  ●                     [options...]  │  ← transparent drag zone (close left, app options right)
│                                      │
│   [App content, padded]              │  ← bg-surface-lowest
│                                      │
└──────────────────────────────────────┘
```

- Title bar: close control on **left**, app-specific option buttons on **right**. No background — controls float over content. Content scrolls edge-to-edge under the header. The `scroll-fade` mask (sized to match header height) fades content as it passes beneath the floating controls — no background or separator needed.
- Close = hide (`presentation: 'hidden'`). Entity persists, agent can reopen it. This is like minimizing to a dock — not deletion.
- Focus: active shadow (`shadow-window`) + full-opacity controls. Unfocused: resting shadow + dimmed controls.
- Drag: entire top zone is the handle (~40px).
- Resize: corner and edge handles.
- No tabs. No nested navigation. One entity = one window = one view.

→ *Implementation: `core/entity/`*

#### App Layout Within Windows

Windows provide chrome and horizontal padding. Apps own everything vertical — clearing the floating header, structuring their scroll area, and positioning any floating controls. The canonical template:

```
App root (flex column, full height, top padding to clear floating header)
  ├─ App header (optional — sticky or inline, e.g. calendar toolbar)
  ├─ Scroll area (fills remaining space, overflow-auto, scroll-fade)
  │    ├─ top inset padding (clears floating header if no app header)
  │    ├─ content
  │    └─ bottom inset padding (clears floating input + breathing room)
  └─ Floating input (absolute-positioned at bottom, above scroll area)
```

**Rules:**

- **Clear the floating header.** The WindowHeader is absolutely positioned over content. The app root adds top padding equal to the header height so content starts below it.
- **Floating elements are siblings, not children, of the scroll container.** This keeps them outside `scroll-fade`'s mask — if a floating input were inside the scroll view, the edge fade would clip it.
- **Bottom padding inside the scroll view matches the floating element.** The last content item should be scrollable past the floating control. Size the padding to the control's height plus comfortable breathing room.
- **Window provides `px` only — apps own `py`.** The window's horizontal padding is the only spacing the window contributes. All vertical structure — header clearance, scroll insets, floating element positioning — is the app's responsibility.

Apps without floating controls or app headers follow a simplified version: a single scroll area with top padding to clear the WindowHeader and `scroll-fade` applied directly.

→ *See also: Edge Fade (Surface Principles) for mask behavior details.*

### Cards

Cards are compact entity previews on the canvas. Portrait proportion. Two variants.

**Image Card:**

```
┌───────────────────────┐
│                       │
│  [Image, edge-to-edge]│  ← no padding, fills to rounded corners
│                       │
│                       │
│  type · timestamp     │  ← metadata row
└───────────────────────┘
```

**Text Card:**

```
┌───────────────────────┐
│  Title                │
│                       │
│  Summary text that    │  ← truncated preview of full content
│  previews the doc...  │
│                       │
│  type · timestamp     │  ← metadata row
└───────────────────────┘
```

**Hover:** Action icons appear top-right with tonal backgrounds for contrast (no scrim). Icons: add to context, maximize.

- Image card: image fills edge-to-edge, clipped by overflow hidden + border radius.
- Text card: padded content zone with title + summary (truncated to card height). The summary previews what the user sees in the full sheet/window.
- Both variants: metadata row at bottom (type + timestamp).
- Click: opens full content — expands to window or bottom sheet.
- Drag: entire card is the handle.
- Fixed size per card type, defined in app type definitions.

→ *Implementation: `core/entity/CanvasCard.tsx`*

#### App Presentation Modes

Every app receives a `mode` (`'window'` or `'card'`) and is responsible for rendering both presentations. The card variant is a *different component*, not a shrunk-down window.

- **Window mode:** Full interactive layout — scroll areas, floating controls, overlays, all internal structure.
- **Card mode:** A self-contained compact preview. No scroll, no floating elements, no overlays. It shows just enough to communicate what the entity contains — a title, a summary line, a thumbnail. Think of it as a poster for the full experience.

The app decides what to show in each mode. The entity chrome (Window vs Card) handles everything external — shadow, drag, resize, focus — so the app never needs to worry about chrome differences.

### In-Window Overlays

Apps sometimes need secondary panels within the window — filters, navigation, settings, or modal-like flows. These overlays live *within the window bounds*; they don't escape into canvas space.

Two patterns:

**Sidebar Overlay** — a panel that slides in from one edge of the window:

- Covers the full window area (extends past window padding to reach window edges).
- Panel scales in from its anchor edge using the `popIn` spring, with `transformOrigin` at the anchor edge (following the Panel Spawn surface principle).
- A transparent click-to-close backdrop sits as a sibling — not a parent — of the panel.
- Panel respects the window header (top margin) and window bottom edge (bottom margin).
- Wrapper is pointer-events-none; panel and backdrop individually opt back in.

**Full-Panel Overlay** — a panel that fills the window for modal-like flows:

- Same coverage area as the sidebar overlay.
- Panel fills the available window space (minus header and edge margins).
- Scales from center (`transformOrigin: center center`).
- For flows that need the full window real estate — confirmation screens, multi-step forms, detail views.

**Rules:**

- Both patterns use the entity shadow scale (`shadow-elevated`), not the overlay shadow scale. They're inside an entity, not floating above the canvas.
- The overlay is a sibling of the app content, not a child of the scroll area — so `scroll-fade` doesn't mask it.
- Dismiss: click backdrop, Escape key, or explicit close control.

→ *See Panel Spawn (Surface Principles) for the spring and origin pattern.*

### Folder Stacks

When entities are grouped or collapsed on the canvas, they render as a **folder stack** — 2–3 card thumbnails (73×94px) layered with CSS rotation, creating a casual "pile of cards" look.

- Thumbnails: `rounded-lg` (12px), `shadow-card`, skeleton placeholder lines inside.
- Rotations: ~-9°, ~2°, ~5° (back-to-front).
- Click expands the group or navigates into it.
- Presentation type: `'folder'` on the entity model.

→ *Implementation: `core/entity/FolderStack.tsx`*

### App Dock

The App Dock is where the space's apps are stacked and accessible. Left-aligned, vertical.

- Can fully hide — not just collapse to icons. The Canvas reclaims the space when the dock is hidden.
- App launcher: two sections. **Built-in apps** (always shown): vertical stack of built-in app types (icon + name). **Space apps** (shown if they exist): composed app types that have active entities in this space, filtered by usage. Click creates a new entity at viewport center with standard creation animation. For composed types, clicking asks the agent to create a new one.
- Bottom section: space name, user avatar, settings.

Both the user (via App Dock) and the agent (via `create_entity`) can create entities. The dock is the user's direct creation path; the agent is the conversational path.

→ *Implementation: `core/canvas/AppDock.tsx`*

### Prompt Bar & Conversation Panel

The agent chat is a bottom-center prompt bar with a conversation panel that pops up on demand. NOT a sidebar or fixed dock.

**Prompt bar** (always visible):

- Fixed bottom-center, above all entities.
- Resting: compact, pill-shaped input with placeholder. Glassmorphic background.
- Active: widens, context and send buttons appear. Spring animation.
- Expanded: grows vertically for multi-line input. Internal scroll past max lines.
- Send: Enter to send, Shift+Enter for newline.

**Conversation panel** (on demand):

- Triggered by sending a message. Chat bubble appears above prompt bar.
- Expanding the bubble reveals the full conversation. Glassmorphic, springs upward.
- Dismiss: minimize button, or click outside.

**Chat content:**

- Minimal chrome. User messages right-aligned, agent left-aligned. No avatars. Timestamps on hover.
- Tool call chips inline with message flow.
- Scrolls to bottom on new messages.

→ *Implementation: `core/chat/`*

### Bottom Sheet

Full-width overlay sliding up from the bottom. For focused content or document-length viewing. Used for: entity maximization (cards), login page, image viewing.

```
┌─────────────────────────────────────────────────┐
│                                                 │  ← top inset (canvas visible,
│   ┌─────────────────────────────────────────┐   │     scaled down to 0.96 + dimmed)
│   │ [scaled-down canvas behind]             │   │
│   └─────────────────────────────────────────┘   │
├─────────────────────────────────────────────────┤
│  [×]                          [Act] [Act] [Act] │  ← header: close left, actions right
├─────────────────────────────────────────────────┤
│                                                 │
│  [Sheet content — scrollable, edge-fade mask]   │  ← bg-surface-lowest
│                                                 │
└─────────────────────────────────────────────────┘
```

- Canvas behind scales to `scale(0.96)` with `transform-origin: top center` and dims (`bg-black/40`). Maintains spatial orientation — iOS-style depth.
- Dismiss: close button (left, uses `WindowControl`), click visible canvas above, Escape key.
- Spring animation from bottom edge (`gentle` preset). Top-corner `radius-2xl` only.
- Full viewport width. No side margins. Shadow: `shadow-overlay`.
- Header: `h-12`, close button left, action slots right. Apps define actions, sheet defines layout.
- Body: scrollable with edge-fade masking (CSS `mask-image`). Content is fully composable.
- Canvas is fully locked while sheet is open (`pointer-events: none`).
- Reduced motion: skip scale, simple opacity fade.
- Only one sheet at a time.

→ *Implementation: `core/sheet/`*

### Context Menu

Right-click on an entity:

- **Archive** — plays archive animation
- **Change presentation** → submenu: Window, Card
- **Duplicate** — copy at offset position
- **Add to agent context** — pins for next agent message

Glassmorphic overlay surface. Appears at cursor, constrained to viewport. Spring open, fade close.

No canvas context menu. Right-clicking empty canvas does nothing. All entity creation flows through the App Dock or agent.

→ *Implementation: `core/layout/`*

---

## Canvas Behavior

The Canvas is the space's visual container — a full-viewport inset card with slight padding from all four browser edges and rounded corners, filled with `surface-dim`. The browser background behind it uses `surface`, creating a tonal frame that communicates "you've entered a space." The inset and radius make the space feel like a contained environment, not a webpage edge-to-edge.

Within the Canvas, the spatial surface is infinite — pannable and zoomable. Entities live at absolute positions.

### Pan & Zoom

- Infinite pan + zoom. No boundaries.
- Pan: click-drag on empty space, or middle-mouse anywhere.
- Zoom: scroll wheel or pinch toward cursor position.
- Zoom range: 25%–200%, default 100%.
- Zoom-to-fit: keyboard shortcut that frames all entities with comfortable padding.

### Agent Placement

The agent places entities in loose clusters near the origin, checking for collisions to avoid overlap. The workspace stays compact and navigable — no random scatter across infinite space.

### Viewport Culling

Only entities within the visible viewport (plus a margin buffer) are rendered. Off-screen entities are unmounted. Essential for performance at scale.

### Background

The browser viewport fills with `surface`. The Canvas card (inset, rounded) sits on top in `surface-dim`. Inside the Canvas, an optional subtle dot grid at very low opacity provides spatial orientation during panning. Toggleable in settings.

### Entry Choreography

The app loads in layers — choreographed and hierarchical, not all at once. Each surface appears in sequence with deliberate timing:

1. **Background** — `surface` viewport fill, instant.
2. **Canvas** — fades in with a subtle scale-up (from ~98% to 100%). The inset card emerges as if the space is opening.
3. **App Dock + Prompt Bar** — fade in together, anchoring the interface chrome.
4. **Entities** — cards and windows appear as their data is ready. Each uses the standard spawn animation (scale-up from origin, spring settle, agent glow if agent-created).

If entity content isn't ready, the entity chrome appears immediately with a warm shimmer in the content area, then cross-fades to the loaded state. No pop-in, no layout shift.

`prefers-reduced-motion`: all layers appear instantly with no scale or fade. Sequence is preserved (background → canvas → chrome → entities) but transitions are zero duration.

→ *Canvas implementation: `core/canvas/`*

---

## Entity Sizing & Overlap

### Size Constraints

| Presentation | Resizable | Notes |
|---|---|---|
| Window | Yes (corner + edge handles) | Min dimensions enforced. No max. Default size per app type. |
| Card | No | Fixed size per app type. |

### Stacking

Entities overlap freely like desktop windows. Z-index determines order:

| Layer | Elements |
|---|---|
| Canvas surface | `surface-dim` inset card, optional dot grid |
| Entities | Windows, cards — focus brings to top |
| Overlay surfaces | Context menus, dropdowns, popovers |
| App Dock + Prompt bar | App Dock, prompt bar + conversation panel — float above all other surfaces |
| Bottom sheet | Sheet + scrim — the only surface that covers the dock and prompt bar |

Focus = top. Agent-created entities spawn at the top. Dragging over another entity doesn't push it — free spatial placement.

### Scrolling Inside Entities

- Content overflows vertically with OS-native scrollbars.
- No horizontal scroll unless content demands it (code, wide tables).
- Programmatic scrolls (chat auto-scroll) use smooth behavior.
- The canvas itself pans — no browser scrollbars.

#### Auto-Scroll with User Override

Any app with live-updating scrollable content (chat messages, activity feeds, logs) needs auto-scroll that respects user intent:

- **Track "at bottom":** If the scroll position is within a small threshold of the bottom edge, the user is considered "at bottom."
- **Auto-scroll on new content:** Only scroll to bottom when the user is already at bottom. If the user has scrolled up to read earlier content, new items appear below without yanking the viewport.
- **Coalesce rapid updates:** When multiple items arrive in quick succession, batch scroll adjustments into a single animation frame to avoid jank.
- **Re-engage automatically:** When the user scrolls back to the bottom, auto-scroll resumes — no manual toggle needed.

---

## Form Primitives

All apps compose from shared form primitives. The agent uses these same primitives when building new app UIs. Never use raw HTML form elements.

**Available primitives:** Input, Textarea, Select, Toggle, Checkbox, Button (primary / ghost / danger variants).

One button height. All inputs share consistent height. See the component implementations for exact dimensions and state behavior.

→ *Implementation: `core/ui/`*

---

## Interactive States

Four universal rules govern every interactive element:

| State | Visual Treatment |
|---|---|
| **Hover** | Surface lightens one tonal step |
| **Focus** | Ring using `primary` at low opacity. Replaces browser default. |
| **Active / Pressed** | Surface darkens one tonal step |
| **Disabled** | Reduced opacity, no pointer events |

These rules apply consistently across all components. Per-component state implementations live in the component source.

→ *Component implementations: `src/components/ui/`*

---

## Image Fill Behavior

- **Cards:** Images go edge-to-edge, clipped by overflow hidden + border radius. No padding.
- **Windows:** Content images respect standard content padding. Exception: full-bleed backgrounds fill edge-to-edge.
- **Grids:** Image grids use tight gaps (mosaic feel). Content tile grids use wider gaps (breathing room).

---

## Motion Principles

### 1. Agent Animates, User Is Immediate

Agent creates a window → springs into existence. User drags a window → tracks the pointer instantly.

### 2. Everything Comes From Somewhere — And Returns There

Every element has a spatial origin. No elements materialize from nowhere, and no elements vanish into nowhere. The exit animation is the entrance animation in reverse — elements retract toward their origin point when dismissed.

- A bottom sheet slides up from the bottom edge — and slides back down on close.
- A window scales up from the icon or button that spawned it — and scales back down on close.
- A context menu expands from the click point — and collapses back to it.
- A card action overlay fades in from the card surface — and fades back into it.

If there's no spatial trigger (keyboard shortcut), the entity grows from a seed shape at viewport center — and shrinks back to it on dismiss.

### 3. Spawn Animation

New entities start scaled-down at their origin point, then scale up and fly to resting position. iOS app-launch pattern adapted for a spatial canvas. Archival reverses it.

### 4. Spring Physics

All animations use spring easing. Crisp settle with minimal overshoot — professional, not playful. Closer to the Linear/Vercel motion feel than iOS bounce.

**Spring presets** (defined in `lib/motion.ts`):

| Preset | Stiffness | Damping | Mass | Character | Use case |
|--------|-----------|---------|------|-----------|----------|
| `agent` | 300 | 30 | 1 | Crisp, precise | Default for agent-origin animations |
| `snappy` | 500 | 35 | 0.8 | Quick snap | UI transitions, toggles |
| `popIn` | 400 | 25 | 0.8 | Bouncy settle | Entity creation spawn |
| `gentle` | 200 | 20 | 1 | Soft ease | Expand/collapse, accordion |
| `page` | 120 | 20 | 1 | Deliberate slide | Full-screen sheet, page-level transitions |
| `prompt` | 300 | 20 | — | Underdamped oscillation | Prompt input idle/active morph |

The `page` spring is the base language spring for large-surface transitions — snappy but controlled, with high damping to minimize bounce and mass of 1 for a deliberate, weighty feel.

### 5. Duration Tiers

Three tiers: fast (hover/press feedback), medium (component transitions), slow (entity creation/archival). Exception: agent glow fade is deliberately slow because it's ambient.

### 6. Reduce Motion

Respect `prefers-reduced-motion`. All animations → instant. Glow → static border highlight. Spatial origin principles still apply conceptually.

### 7. Presentation Morphs

When an entity changes presentation mode (card → window, etc.), it morphs between states:

1. Capture current bounding rect.
2. Calculate target bounding rect from new presentation.
3. Animate between them with spring physics.
4. Chrome elements cross-fade during the morph.
5. Content scales and clips within the morphing container.
6. `prefers-reduced-motion`: instant swap.

→ *Spring parameters, duration values, and animation utilities: `lib/motion.ts`*

---

## Color Philosophy

Domus is **warm and quiet**. Not sterile-white productivity tool. Not neon-dark hacker aesthetic.

- **Theme:** Follows `prefers-color-scheme`. User can override.
- **Light:** Warm off-white (primary hue tint). High contrast text. The feel of good paper.
- **Dark:** Deep warm gray, not pure black. Primary hue tint. The feel of a well-lit room at night.
- **Accent scarcity:** `primary` on focused borders, interactive hover states, and the agent glow. That's it.
- **Spatial depth:** Canvas is `surface-dim`. Entities are `surface-lowest`. Overlays use glassmorphism. Nesting follows the 7-level tonal hierarchy.

---

## Anti-Patterns

Things we will not do:

- **Gradients on surfaces.** Flat tonal backgrounds with shadow.
- **Blur on entity container surfaces.** Flat for entities. Blur for overlays only. Transient child overlays within entities (dropdowns, popovers) may use blur.
- **Icon-heavy navigation.** See P8 icon budget.
- **Toast notifications.** Spatial + inline feedback only.
- **Heavy skeleton screens.** Warm shimmer, not layout-mimicking skeletons.
- **Confetti, particles, celebratory animations.** This is a workspace.
- **Custom scrollbars.** OS default.
- **Pages or full-screen layouts.** Entities on a spatial canvas.
- **Raw HTML form elements.** Use Domus form primitives.

---

## Surface Principles

Internalize these before building any component. They govern how surfaces behave, appear, and transition — the physical grammar of the interface.

### Organic Growth

Surfaces grow from an origin. Nothing pops into existence. Every entity, panel, menu, and overlay emerges from a source point and expands fluidly into its resting state. All transformations between states — opening, closing, resizing, morphing — are animated. The user should always be able to answer: *where did that come from?*

### Panel Spawn

Anchored panels (sidebars, drawers, dropdown menus) grow from their anchor edge using spring physics — not slide-in, not instant. The panel starts at a smaller scale (both axes) and springs to full size. `transformOrigin` is set to the anchor edge, centered on the cross-axis: `left center` for a left-anchored sidebar, `right center` for a right-anchored one, `bottom center` for a bottom-anchored surface. The panel grows outward from its edge while staying vertically (or horizontally) centered — symmetric expansion on the cross-axis.

The spawn uses the same `popIn` spring as other surface births (context chips, entity creation). The overlay backdrop fades in with a short opacity tween, independent of the panel spring — so the backdrop settles before the panel finishes its bounce.

Exit reverses the entrance: panel scales back down toward the anchor edge while the backdrop fades out.

### Edge Fade

In windows, cards, and sheets, scrollable content fades to transparent at the top and bottom edges of the scroll container. A slide-off effect that softens the hard clip of the container boundary — content dissolves into the surface rather than being abruptly cut. Inspiration: macOS 26.

On any scrollable surface with floating header controls, top and bottom padding both match the fade size. Top padding pushes initial content below the controls; bottom padding lets final content clear the fade zone when fully scrolled. As the user scrolls, content slides under the header and dissolves through the fade — keeping controls legible without a background or separator.

#### Tonal fades — always use the surface's own color

The fade gradient must go from the **element's own surface color** to `transparent` — never from `transparent` to `transparent` (which is invisible). Using a color-matched gradient means items dissolve *into* the surface, not *through* it into whatever is rendered behind.

#### Where to apply `scroll-fade`

**`scroll-fade` goes on the scroll view itself** — the element with `overflow-auto`. The mask fades that element's painted output at its edges. Floating siblings (headers, inputs, menus) positioned outside the scroll view are unaffected because they're separate elements in the DOM, not children of the masked surface.

Never apply `scroll-fade` to a parent container that wraps both the scroll view and floating elements — `mask-image` clips *all* painted content within the element, including absolutely-positioned children.

→ *See App Layout Within Windows (Component Patterns → Windows) for the canonical layout template showing where `scroll-fade` sits relative to floating elements.*

#### Overlay gradient divs

Required for floating surfaces (sidebars, popovers) that sit above a *differently-colored* background. The gradient runs from the floating surface's own color to `transparent`, so content dissolves into the sidebar — not through it into whatever is behind.

```
sidebar overlay (absolute z-20, bg-surface-chat-sidebar)
  └─ GroupsPanel (relative overflow-hidden)
       ├─ top div: bg-gradient-to-b from-surface-chat-sidebar to-transparent
       ├─ scroll div (overflow-auto)
       └─ bottom div: bg-gradient-to-t from-surface-chat-sidebar to-transparent
```

### Sheet Depth

When a full-screen sheet opens, the canvas surface scales down and remains visible as a narrow inset at the top of the screen. This preserves spatial context — the user never loses sight of where they came from. The scaled-down canvas is dimmed and non-interactive, acting as both a visual anchor and a dismiss target (tap it to close the sheet).

---

## Agent Guardrails Checklist

Run through this before considering any component complete. **Verify exact values against the canonical source files, not this document.**

### Before Writing Any Component

- [ ] Read the entity model. Your component renders inside a window or card. It doesn't own layout, chrome, or positioning.
- [ ] Identify the presentation type — each has different chrome, sizing, and interaction rules.
- [ ] Check if an existing app covers this. Don't create a new entity type if an existing one can be extended.

### Color

- [ ] Every `bg-`, `text-`, `border-` uses semantic tokens. No raw colors.
- [ ] `primary` only used for: focused borders, hover states, agent glow (+ focus rings for accessibility).

### Typography

- [ ] Chrome uses only the three token sizes with correct weights.
- [ ] Content areas may use the extended scale from entity content components.
- [ ] System font stack only. No custom fonts.

### Spacing

- [ ] All values are multiples of 4px, using token scale from `tokens/tokens.css`.
- [ ] No magic numbers for margins or padding.
- [ ] Relational gaps reflect element relationships (tight, normal, loose).

### Elevation

- [ ] Correct shadow level (resting vs elevated) per component type.
- [ ] No blur on entity container surfaces. Blur only on overlay surfaces.
- [ ] No gradients.
- [ ] Radius uses token scale. Inner radius maintains concentricity.

### Motion

- [ ] Agent changes animate with springs. User changes are instant.
- [ ] Duration matches the appropriate tier from `lib/motion.ts`.
- [ ] New elements have a spatial origin.
- [ ] `prefers-reduced-motion` respected.

### Form Primitives

- [ ] All inputs use Domus primitives from `core/ui/`. No raw HTML elements.
- [ ] Buttons use one of three variants (primary, ghost, danger).
- [ ] All interactive elements implement hover, focus, active, disabled states.

### Transitive States

- [ ] Loading state defined (shimmer, not skeleton).
- [ ] Empty state defined (centered muted text, no illustrations).
- [ ] Errors are inline, not modal/toast.

### Feedback

- [ ] No toasts, snackbars, or floating banners.
- [ ] Agent glow used only for agent-origin changes.

### Chrome

- [ ] No icons beyond the P8 budget.
- [ ] No toolbars or nested navigation within windows.
- [ ] One entity = one window = one view.

### Accessibility

- [ ] `prefers-reduced-motion` handled.
- [ ] `prefers-color-scheme` respected via tokens.
- [ ] Visible focus states on all interactive elements.
- [ ] Text contrast meets WCAG AA.
