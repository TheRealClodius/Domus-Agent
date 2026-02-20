"""Iframe builder context — tells the agent how to generate React + shadcn/ui code for build_app."""

IFRAME_BUILDER_CONTEXT = """
## Building Custom Apps

You can create interactive apps using the `build_app` tool. Apps are React components
rendered in a sandboxed iframe with these libraries available (no imports needed):

### Available Hooks
- `useAppState(defaults)` -> `[state, setState]` — like useState but synced to the entity.
  State persists across page reloads. Use function updater: `setState(prev => ({ ...prev, count: prev.count + 1 }))`
- `useState`, `useEffect`, `useCallback`, `useMemo`, `useRef` — standard React hooks

### Available Components
Button, Input, Slider, Switch, Dialog, DialogTrigger, DialogContent, DialogHeader,
DialogTitle, DialogDescription, Tooltip, TooltipTrigger, TooltipContent, TooltipProvider

### Available Icons
All Lucide React icons are in scope as PascalCase: `<Calculator />`, `<Plus />`, `<Trash2 />`, etc.

### Styling
Use Tailwind CSS classes. The full Tailwind utility set is available.
Use semantic tokens where possible: `bg-surface`, `text-on-surface`, `border-outline-variant`.

### Code Structure
Your code must define a function called `App` (this is the entry point):

```jsx
function App() {
  const [state, setState] = useAppState({ count: 0 })
  return (
    <div className="p-4 flex flex-col gap-4">
      <h2 className="text-lg font-semibold">{state.count}</h2>
      <Button onClick={() => setState(s => ({ ...s, count: s.count + 1 }))}>
        Increment
      </Button>
    </div>
  )
}
```

### Schema Definition
Define MCP tool schemas so you (the agent) can interact with the app programmatically:

```json
[{
  "name": "set_count",
  "description": "Set the counter value",
  "inputSchema": {
    "type": "object",
    "properties": { "count": { "type": "number" } },
    "required": ["count"]
  }
}]
```

### Guidelines
- Keep code in a single `App` function (no separate files or complex module patterns)
- Use `useAppState` for all persistent state. Use `useState` only for ephemeral UI state (hover, focus)
- Use CSS grid and flexbox for layout — you have full CSS capabilities
- Apps are typically 400x500px windows. Design for that size
- After building, use `get_entity_schema` and `call_entity_tool` to TEST your app.
  Then use `update_app` to fix any issues you find.
- Review your own work: does the layout look right? Are interactions wired up? Is the state shape clean?

### Example: Calculator

```jsx
function App() {
  const [state, setState] = useAppState({ display: '0', operator: null, operand: null, resetNext: false })

  const press = (key) => {
    setState(s => {
      if (key >= '0' && key <= '9' || key === '.') {
        const display = s.resetNext ? key : (s.display === '0' ? key : s.display + key)
        return { ...s, display, resetNext: false }
      }
      if (['+', '-', '*', '/'].includes(key)) {
        return { ...s, operator: key, operand: parseFloat(s.display), resetNext: true }
      }
      if (key === '=' && s.operator && s.operand !== null) {
        const b = parseFloat(s.display)
        const ops = { '+': (a,b) => a+b, '-': (a,b) => a-b, '*': (a,b) => a*b, '/': (a,b) => a/b }
        const result = ops[s.operator](s.operand, b)
        return { display: String(result), operator: null, operand: null, resetNext: true }
      }
      if (key === 'C') return { display: '0', operator: null, operand: null, resetNext: false }
      return s
    })
  }

  return (
    <div className="flex flex-col h-full bg-surface">
      <div className="p-4 text-right text-3xl font-mono bg-muted rounded-lg m-4 mb-2 min-h-[3rem] flex items-center justify-end">
        {state.display}
      </div>
      <div className="grid grid-cols-4 gap-2 p-4 pt-2 flex-1">
        {['C','\\u00b1','%','/',  '7','8','9','*',  '4','5','6','-',  '1','2','3','+',  '0','.','='].map(k => (
          <Button key={k} variant={['/','*','-','+','='].includes(k) ? 'default' : 'outline'}
            className={k === '0' ? 'col-span-2' : ''} onClick={() => press(k)}>
            {k}
          </Button>
        ))}
      </div>
    </div>
  )
}
```
"""
