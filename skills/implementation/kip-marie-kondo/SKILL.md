---
name: kip-marie-kondo
description: >-
  Tidies the current git branch by removing comments and code that are
  duplicative, superfluous, verbose, or otherwise don't spark joy, scoped
  strictly to what this branch changed (committed + uncommitted) versus the
  base branch. Consolidates repeated logic, deletes dead weight, replaces
  front-end components that re-implement existing base/atomic components,
  and tightens verbose diffs down to their essential form without changing
  behavior. Use when the user says "marie kondo", "spark joy", "tidy up my
  branch/diff", "clean up my changes", "make this diff more concise",
  "remove duplicate code from my branch", "stop reinventing components", or
  before opening a PR when the diff feels bloated.
---

# Marie Kondo

Tidy the active branch's own changes (not the whole codebase). Only touch lines this branch introduced or modified relative to its base branch — never "fix" unrelated pre-existing code just because you're nearby.

## Workflow

Track progress with a todo list (this task is multi-step):

```
- [ ] Scope the diff (base branch, committed + uncommitted)
- [ ] Build per-file list of changed hunks
- [ ] Apply KonMari pass file by file
- [ ] Verify (typecheck/lint/tests) on touched files
- [ ] Report summary of cuts
```

### 1. Scope the diff

Find the base branch and diff everything this branch has done vs it — committed commits AND any uncommitted working-tree changes, in one shot:

```bash
base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@') || base=main
merge_base=$(git merge-base "$base" HEAD)
git diff --stat "$merge_base"
git diff "$merge_base"
```

If `git diff "$merge_base"` is empty, there's nothing to tidy — tell the user and stop. If on the base branch itself, stop and say so.

### 2. Build the per-file hunk list

For each changed file, note only the added/modified line ranges (the `+` lines and surrounding modified context) from the diff above. This is your working set — everything else in the file is out of scope.

### 3. Apply the KonMari pass

For every hunk in scope, ask "does this spark joy?" against each check below. If not, cut it. Preserve behavior exactly — this is a tidying pass, not a refactor or a feature change.

**Comments — cut when they:**
- Restate what the code obviously does (`// increment counter`, `// loop through items`)
- Are leftover narration from drafting ("// now we handle the edge case", "// added this to fix bug")
- Are commented-out code
- Duplicate information already in a nearby docstring/JSDoc

**Keep comments that explain non-obvious WHY** (a workaround, a business rule, a constraint the code can't convey on its own).

**Code — cut or consolidate when you find:**
- The same logic copy-pasted 2+ times in this diff → extract one helper/constant
- Logic that duplicates an existing helper/util elsewhere in the codebase → call that instead of reinventing it
- Unused imports, variables, functions, or exports introduced by this branch
- Debug leftovers (`console.log`, stray `print`, commented experiments)
- Unnecessary indirection: a variable used once that just aliases an expression, a wrapper function that only calls through to one other function, an intermediate step that adds no clarity
- Defensive code (null checks, type guards, try/catch) for cases the surrounding types/flow already rule out
- Verbose control flow that has a more direct equivalent (e.g., nested ifs that can be a guard clause or a single condition) — only simplify if it's clearly more concise AND at least as readable

**Front-end components — don't spark joy when they:**
- Re-implement a component that already exists in the project's base/atomic component library (e.g. a hand-rolled button, modal, dropdown, spinner, or table when `Button`, `Dialog`, `SelectInput`, `Spinner`, `BaseTable`, etc. already exist)
- Build raw markup (`<div>`/`<button>`/`<input>` with manual styling) for something a base atomic component already covers, instead of using that component
- Before assuming something is new, check the project's shared component directory (e.g. `src/components/`) for an existing match — same or near-same visual/behavioral purpose counts, not just identical props
- Fix: replace the bespoke implementation with the existing base component (composing/extending it via props), rather than leaving two parallel ways to render the same UI pattern

**When unsure whether a cut changes behavior, don't make it.** Favor leaving something in over risking a functional regression.

### 4. Verify

After edits, run whatever check fits the repo (in priority order, use what's available): typecheck → lint → relevant unit tests for touched files. If a check fails because of your edit, fix it or revert that specific cut — don't leave the branch broken.

### 5. Report

Summarize per file: what was cut/consolidated and why (one line each), plus the net diff-size change (`git diff --stat "$merge_base"` before vs after). Group by category (comments removed, duplication consolidated, dead code removed, verbosity trimmed).

## Example cuts

**Duplicative comment:**
```typescript
// Bad — restates the code
// Set the user's name to the trimmed input
user.name = input.trim();

// Good — no comment needed, code is self-explanatory
user.name = input.trim();
```

**Unnecessary indirection:**
```typescript
// Bad
const isValid = checkValidity(input);
if (isValid) { ... }

// Good
if (checkValidity(input)) { ... }
```

**Duplicated logic → consolidated:**
```typescript
// Bad — same shape twice in the diff
const activeProps = properties.filter(p => p.status === 'active' && !p.archived);
const activePmcs = pmcs.filter(p => p.status === 'active' && !p.archived);

// Good
const isActive = (item: { status: string; archived: boolean }) =>
  item.status === 'active' && !item.archived;
const activeProps = properties.filter(isActive);
const activePmcs = pmcs.filter(isActive);
```

**Re-implemented component → use the base one:**
```tsx
// Bad — hand-rolled button duplicating the base Button component
<div
  onClick={handleSubmit}
  className="cursor-pointer rounded bg-blue-600 px-4 py-2 text-white hover:bg-blue-700"
>
  Submit
</div>

// Good — use the existing atomic component
<Button onClick={handleSubmit}>Submit</Button>
```
