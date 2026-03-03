"""Iframe builder context — tells the agent how to generate React + shadcn/ui code for build_app."""

IFRAME_BUILDER_CONTEXT = """
## Building Custom Apps

You can create interactive apps using the `build_app` tool. Apps are React components
rendered in a sandboxed iframe with the Domus design system available. No imports needed.

### Design Philosophy

Domus is **warm and quiet** — not sterile-white productivity tool, not neon-dark hacker aesthetic.
Every generated app must feel like it belongs in the Domus environment: warm tonal surfaces,
generous spacing, soft corners, and restrained color. Think of the best macOS native apps.

**Core rules:**
- Never use raw color values (no `bg-gray-100`, no `#hex`, no `rgb(...)`). Use semantic tokens only.
- Every spacing value is a multiple of 4px. Use Tailwind spacing: `p-2` (8px), `p-3` (12px), `p-4` (16px), `gap-2`, `gap-3`, etc.
- Generous whitespace. Let the content breathe. Dense UIs feel wrong in Domus.
- No gradients. Flat tonal surfaces with shadow for depth.
- Radius: use `rounded-lg` (12px) for cards/sections, `rounded-md` (6px) for buttons/inputs, `rounded-2xl` (20px) for large containers.

### Semantic Color Tokens (Tailwind classes)

**Surfaces** (7-level tonal hierarchy, brightest → dimmest):
- `bg-surface-lowest` — brightest, for entity chrome / main app background
- `bg-surface-low` — cards inside the app, secondary panels
- `bg-surface` — input wells, code blocks, nested cards
- `bg-surface-high` — elevated panels
- `bg-surface-dim` — dimmest, canvas background

**Text:**
- `text-on-surface` — primary text (high contrast)
- `text-on-surface-muted` — secondary text, labels, metadata

**Borders:**
- `border-outline` — strong borders (dividers, focus rings)
- `border-outline-variant` — subtle borders (card edges, separators)

**Accents** (use sparingly — accent scarcity is a design principle):
- `bg-primary` / `text-primary` — brand purple. Only for active states, selected items, CTAs
- `bg-primary-container` / `text-on-primary-container` — soft purple background + text pair
- `bg-secondary-container` / `text-on-secondary-container` — muted purple surface pair
- `bg-tertiary-container` / `text-on-tertiary-container` — contrasting accent (pink) pair
- `text-error` / `bg-error-container` / `text-on-error-container` — error states

**Interactive states:**
- Hover: `hover:bg-on-surface/8` (semi-transparent overlay, works on any surface)
- Use `transition-colors` for smooth hover transitions

### Typography

- `font-sans` — Inter, the body font. Use for everything.
- `text-sm` (14px) — default body text and UI chrome
- `text-xs` (12px) — metadata, timestamps, labels
- `text-base` (16px) — section headers, prominent text
- `text-lg` to `text-3xl` — display text, large numbers, hero values
- `font-medium` for emphasis. No bold body text. No italic for emphasis.
- `font-mono` for numbers, codes, data displays
- `tracking-tight` for large display numbers (looks cleaner)

### Layout Patterns

Apps fill their window. Always start with:
```jsx
<div className="flex flex-col h-full bg-surface-lowest">
  {/* content */}
</div>
```

**Common layouts:**
- Header + scrollable body: header is sticky/fixed, body is `flex-1 overflow-auto`
- Grid of items: `grid grid-cols-2 gap-3 p-4` or `grid grid-cols-3 gap-2 p-4`
- List of items: `flex flex-col gap-2 p-4`
- Split panel: `flex h-full` with left panel + main area

**Sizing:** Apps render in windows typically 480×560px. Design for that size but use
flex/grid so they scale when the user resizes the window.

### Available Hooks

- `useAppState(defaults)` → `[state, setState]` — persistent state synced to the entity.
  Use function updater: `setState(prev => ({ ...prev, count: prev.count + 1 }))`
- `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef` — standard React hooks

### Available Components

**Button** — variants: `default` (primary purple), `ghost` (transparent hover), `destructive` (red)
  Sizes: `default` (h-9), `sm` (h-8), `xs` (h-6), `lg` (h-10), `icon` (square 36px), `icon-sm` (square 32px)
  ```jsx
  <Button variant="ghost" size="sm" onClick={...}>Label</Button>
  <Button variant="ghost" size="icon-sm"><Plus className="size-4" /></Button>
  ```

**Input** — text input. Use `className` for sizing.
  ```jsx
  <Input placeholder="Search..." className="h-8 bg-surface" />
  ```

**Slider** — range input. Props: `value`, `onValueChange`, `min`, `max`, `step`
  ```jsx
  <Slider value={[state.volume]} onValueChange={([v]) => setState(s => ({...s, volume: v}))} min={0} max={100} />
  ```

**Switch** — toggle. Props: `checked`, `onCheckedChange`

**Dialog** — modal dialog. Use DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogClose.

**Tooltip** — wrap with TooltipProvider at app root, then Tooltip > TooltipTrigger + TooltipContent.

### Available Icons

All Lucide React icons in scope as PascalCase: `<Calculator />`, `<Plus />`, `<Trash2 />`, `<Star />`, etc.
Use `className="size-4"` for standard icon size, `size-3` for small, `size-5` for large.

### Code Structure

Code must define a function called `App`:

```jsx
function App() {
  const [state, setState] = useAppState({ /* persistent defaults */ })
  const [uiState, setUiState] = useState(null) // ephemeral UI state

  return (
    <div className="flex flex-col h-full bg-surface-lowest">
      {/* app content */}
    </div>
  )
}
```

### Schema Definition

Define MCP tool schemas so you (the agent) can interact with the app programmatically:

```json
[{
  "name": "set_value",
  "description": "Set a value in the app",
  "inputSchema": {
    "type": "object",
    "properties": { "value": { "type": "number" } },
    "required": ["value"]
  }
}]
```

### Visual Quality Checklist

Before calling build_app, mentally verify:
1. **Is the app background `bg-surface-lowest`?** This matches the window chrome.
2. **Are sections separated with `bg-surface-low` or `bg-surface` cards?** Create visual hierarchy.
3. **Is there generous padding?** `p-4` minimum on outer container, `p-3` on inner cards.
4. **Are interactive elements obvious?** Buttons have clear affordances, not just text.
5. **Is color used sparingly?** Primary purple for 1-2 key elements max. Everything else is tonal grays.
6. **Do numbers/data use `font-mono tracking-tight`?** Prevents layout shift, looks professional.
7. **Are empty states handled?** Show muted centered text, not blank space.

### Guidelines

- Keep code in a single `App` function (helper functions above it are fine)
- Use `useAppState` for all persistent state. Use `useState` only for ephemeral UI (hover, open/close)
- Use CSS grid and flexbox for layout
- Request appropriate `width` and `height` in build_app — don't use the default for every app:
  - Small utilities (timer, converter): 360×400
  - Medium apps (calculator, tracker): 480×560
  - Large apps (dashboard, planner): 600×680
- After building, use `get_entity_schema` and `call_entity_tool` to TEST your app
- Review your own work: does it feel warm and polished? Would it look at home on a Mac desktop?

### Example: Calculator

```jsx
function App() {
  const [state, setState] = useAppState({
    display: '0',
    operator: null,
    operand: null,
    resetNext: false,
  })

  const press = (key) => {
    setState(s => {
      if (key >= '0' && key <= '9' || key === '.') {
        const display = s.resetNext ? key : (s.display === '0' && key !== '.' ? key : s.display + key)
        return { ...s, display, resetNext: false }
      }
      if (['+', '−', '×', '÷'].includes(key)) {
        const opMap = { '+': '+', '−': '-', '×': '*', '÷': '/' }
        return { ...s, operator: opMap[key], operand: parseFloat(s.display), resetNext: true }
      }
      if (key === '=' && s.operator && s.operand !== null) {
        const b = parseFloat(s.display)
        const ops = { '+': (a,b)=>a+b, '-': (a,b)=>a-b, '*': (a,b)=>a*b, '/': (a,b)=>b?a/b:0 }
        const result = ops[s.operator](s.operand, b)
        return { display: String(parseFloat(result.toFixed(10))), operator: null, operand: null, resetNext: true }
      }
      if (key === 'AC') return { display: '0', operator: null, operand: null, resetNext: false }
      if (key === '⌫') return { ...s, display: s.display.length > 1 ? s.display.slice(0, -1) : '0' }
      if (key === '±') return { ...s, display: String(-parseFloat(s.display)) }
      if (key === '%') return { ...s, display: String(parseFloat(s.display) / 100), resetNext: true }
      return s
    })
  }

  const ops = ['+', '−', '×', '÷']

  return (
    <div className="flex flex-col h-full bg-surface-lowest">
      {/* Display */}
      <div className="px-5 pt-6 pb-3">
        <div className="text-right text-on-surface-muted text-xs h-5 font-mono">
          {state.operand !== null ? `${state.operand} ${state.operator}` : ''}
        </div>
        <div className="text-right text-on-surface text-4xl font-mono tracking-tight font-medium truncate">
          {state.display}
        </div>
      </div>

      {/* Keypad */}
      <div className="flex-1 grid grid-cols-4 gap-1.5 px-3 pb-3">
        {['AC', '±', '%', '÷',
          '7', '8', '9', '×',
          '4', '5', '6', '−',
          '1', '2', '3', '+',
          '0', '.', '⌫', '='
        ].map(k => (
          <button
            key={k}
            onClick={() => press(k)}
            className={[
              'rounded-lg text-base font-medium transition-colors',
              k === '0' ? '' : '',
              ops.includes(k) || k === '='
                ? 'bg-primary text-on-primary hover:bg-primary/80'
                : k === 'AC' || k === '±' || k === '%' || k === '⌫'
                  ? 'bg-surface-low text-on-surface hover:bg-on-surface/8'
                  : 'bg-surface text-on-surface hover:bg-on-surface/8',
            ].join(' ')}
          >
            {k === '⌫' ? <span className="flex items-center justify-center"><ArrowLeft className="size-4" /></span> : k}
          </button>
        ))}
      </div>
    </div>
  )
}
```
"""
