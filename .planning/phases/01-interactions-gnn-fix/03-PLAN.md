---
phase: 1
plan: 03
type: feature
wave: 2
depends_on: [01]
files_modified:
  - src/_interactions_new.js
  - src/style.css
autonomous: true
requirements:
  - GNN-11
  - GNN-12
  - GNN-13
---

# Plan 03 — Graph UX Polish: Zoom/Pan, Label Truncation Fix, Parallel PubChem Resolution

<objective>
Add zoom and pan to the polypharmacy D3 graph, fix the aggressive drug name truncation
(12 chars is too short), and make PubChem resolution requests run in parallel instead of
serially so the UI feels faster.
</objective>

<tasks>

## Task 1 — Add D3 Zoom and Pan to the Polypharmacy SVG

<read_first>
- src/_interactions_new.js lines 550-622 (drawPolypharmacyGraph — svg setup and simulation)
</read_first>

<action>
In `drawPolypharmacyGraph`, after creating the `svg` selection (around line 550-552),
wrap the graph content group in a `<g>` that receives the zoom transform, and attach
a D3 zoom behaviour to the SVG:

```javascript
// Replace current svg creation + all appended groups with:
const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H)
    .style('background', '#0d1b2a').style('border-radius', '12px');

// Zoom behaviour
const zoom = d3.zoom()
    .scaleExtent([0.4, 3])
    .on('zoom', (event) => g.attr('transform', event.transform));
svg.call(zoom);

// All graph content goes into this group
const g = svg.append('g');
```

Then change all subsequent `svg.append('g')` calls to `g.append('g')`:
- The link `<g>` group
- The node `<circle>` group
- The labels `<text>` group

Add a zoom-reset button: after creating the SVG, insert a small HTML button above the container:
```javascript
const resetBtn = document.createElement('button');
resetBtn.textContent = '⟳ Reset zoom';
resetBtn.style.cssText = 'position:absolute;top:8px;right:8px;z-index:10;' +
    'background:rgba(255,255,255,0.1);color:#ccc;border:none;border-radius:4px;' +
    'padding:3px 8px;font-size:0.72rem;cursor:pointer;';
resetBtn.addEventListener('click', () => svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity));
container.style.position = 'relative';
container.appendChild(resetBtn);
```
</action>

<acceptance_criteria>
- src/_interactions_new.js `drawPolypharmacyGraph` contains `d3.zoom()` call
- src/_interactions_new.js contains `.scaleExtent([0.4, 3])`
- src/_interactions_new.js contains `g = svg.append('g')` (content wrapper group)
- src/_interactions_new.js contains `⟳ Reset zoom` button text
- src/_interactions_new.js `link` and `node` selections append to `g` not `svg`
</acceptance_criteria>

---

## Task 2 — Fix Drug Name Label Truncation

<read_first>
- src/_interactions_new.js lines 606-610 (labels selection in drawPolypharmacyGraph)
</read_first>

<action>
Change the label truncation limit from 12 to 18 characters:

Current:
```javascript
.text(d => d.id.length > 12 ? d.id.slice(0, 11) + '…' : d.id)
.attr('font-size', '10px')
```

Replace with:
```javascript
.text(d => d.id.length > 18 ? d.id.slice(0, 17) + '…' : d.id)
.attr('font-size', '11px')
.attr('font-weight', '500')
```

Also increase the label vertical offset from `dy=34` to `dy=38` so labels clear
the larger node circles when `harmful_count > 0` causes radius to grow.
</action>

<acceptance_criteria>
- src/_interactions_new.js labels text function contains `length > 18`
- src/_interactions_new.js does NOT contain `length > 12` in the labels selection
- src/_interactions_new.js labels `dy` attribute value is `38` (was 34)
</acceptance_criteria>

---

## Task 3 — Parallel PubChem Resolution in Polypharmacy Backend

<read_first>
- backend/app.py lines 815-826 (resolve loop in polypharmacy_analysis — serial loop)
- backend/app.py lines 484-506 (_resolve_drug helper)
</read_first>

<action>
In `backend/app.py` inside `polypharmacy_analysis`, replace the serial resolution loop
(iterates drugs one-by-one) with parallel resolution using `concurrent.futures.ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

# Replace the serial for loop:
resolved_drugs = [None] * len(drugs)
errors_resolve = []

def _resolve_one(idx_drug):
    idx, d = idx_drug
    r = _resolve_drug(d.strip())
    return idx, d.strip(), r

with ThreadPoolExecutor(max_workers=min(len(drugs), 5)) as ex:
    futures = {ex.submit(_resolve_one, (i, d)): i for i, d in enumerate(drugs)}
    for fut in as_completed(futures):
        idx, raw, result = fut.result()
        if result is None:
            errors_resolve.append(raw)
        else:
            if result.get("input_type") == "name":
                result["name"] = raw.title()
            resolved_drugs[idx] = result

if errors_resolve:
    return jsonify({"error": f"Could not resolve: {', '.join(errors_resolve)}"}), 404
```

This cuts the PubChem wait time from N×(2-3 s) to max(2-3 s) for N drugs.
</action>

<acceptance_criteria>
- backend/app.py `polypharmacy_analysis` contains `ThreadPoolExecutor`
- backend/app.py contains `as_completed` import
- backend/app.py `polypharmacy_analysis` does NOT contain the old serial `for d in drugs:` resolution loop
- backend/app.py `resolved_drugs = [None] * len(drugs)` pattern appears
</acceptance_criteria>

---

## Task 4 — Add CSS for New poly-pair-item__se Side-Effect Row

<read_first>
- src/style.css (search for `.poly-pair-item` to find existing styles)
</read_first>

<action>
In `src/style.css`, find the `.poly-pair-item` rule block and add a new sub-class
immediately after the existing `.poly-pair-item__badge` rule:

```css
.poly-pair-item__se {
    font-size: 0.72rem;
    color: var(--gray-500, #9ca3af);
    font-style: italic;
    margin-top: 2px;
    grid-column: 1 / -1;  /* spans full width of the flex/grid row */
    padding-left: 2px;
}
```
</action>

<acceptance_criteria>
- src/style.css contains `.poly-pair-item__se` rule
- src/style.css `.poly-pair-item__se` contains `font-style: italic`
- src/style.css `.poly-pair-item__se` contains `font-size: 0.72rem`
</acceptance_criteria>

</tasks>

<verification>
1. Open Interactions → Polypharmacy tab.
2. Add 4–5 drugs and run analysis.
3. Scroll-wheel zoom in/out on the graph — nodes and edges should scale.
4. Drag the graph canvas to pan. Confirm ⟳ Reset zoom button appears top-right of graph.
5. Add a drug with a long name (e.g., "Acetaminophen", "Atorvastatin") — confirm label is not cut off at 12 chars.
6. Open browser DevTools → Network tab. Confirm the 5 PubChem fetch calls fire nearly simultaneously (not one after another).
</verification>

<must_haves>
- D3 zoom/pan works on the polypharmacy graph
- Drug label truncation threshold is 18 characters
- PubChem resolution calls are parallel
- Side-effect CSS class exists for Plan 02's new HTML
</must_haves>
