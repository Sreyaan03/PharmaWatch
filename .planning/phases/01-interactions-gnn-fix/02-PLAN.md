---
phase: 1
plan: 02
type: feature
wave: 2
depends_on: [01]
files_modified:
  - backend/app.py
  - src/_interactions_new.js
autonomous: true
requirements:
  - GNN-08
  - GNN-09
  - GNN-10
---

# Plan 02 — Show Side-Effects on Polypharmacy Graph + Fix "All Safe" Graph Disappearing

<objective>
Surface the TWOSIDES `side_effect` data (already stored in HBase) on polypharmacy graph
edges so users see *what* the interaction causes, not just a red line. Also fix the
UX bug where the graph disappears entirely when no harmful pairs are found instead of
showing the safe drug network with green edges.
</objective>

<tasks>

## Task 1 — Enrich polypharmacy pairs with TWOSIDES side-effect lookup

<read_first>
- backend/app.py lines 795-887 (polypharmacy_analysis route and graph builder)
- backend/app.py lines 637-721 (get_twosides_graph — shows HBase schema and row key format)
</read_first>

<action>
In `backend/app.py`, inside the `polypharmacy_analysis` route after `_run_gnn_pair()` succeeds
(around line 836), add a HBase side-effect lookup:

```python
# After is_harmful, confidence = _run_gnn_pair(...)
side_effects = []
if is_harmful:
    try:
        conn = _hbase_connect()
        table = conn.table('interactions')
        # Try both orderings of the drug pair
        for key_candidate in [
            f"{ra['name'].upper()}_{rb['name'].upper()}".encode(),
            f"{rb['name'].upper()}_{ra['name'].upper()}".encode()
        ]:
            row = table.row(key_candidate)
            if row:
                se = row.get(b'info:side_effect', b'').decode().strip()
                if se:
                    side_effects.append(se)
                break
        conn.close()
    except Exception:
        pass  # HBase offline — just skip enrichment
```

Add `side_effects` to the `pairs.append(...)` dict:
```python
pairs.append({
    "drug_a":       ra["name"],
    "drug_b":       rb["name"],
    "is_harmful":   is_harmful,
    "confidence":   confidence,
    "side_effects": side_effects,   # NEW
    "index_a":      i,
    "index_b":      j
})
```

Also add `side_effects` to the `links` list in the graph builder:
```python
links = [
    {
        "source":      p["drug_a"],
        "target":      p["drug_b"],
        "is_harmful":  p["is_harmful"],
        "confidence":  p["confidence"],
        "side_effects": p.get("side_effects", []),   # NEW
        "value":       2 if p["is_harmful"] else 1
    }
    for p in pairs
]
```
</action>

<acceptance_criteria>
- backend/app.py contains `side_effects` variable inside `polypharmacy_analysis`
- backend/app.py contains `info:side_effect` string (HBase lookup)
- backend/app.py `pairs.append` block contains `"side_effects"` key
- backend/app.py `links` list comprehension contains `"side_effects"` key
</acceptance_criteria>

---

## Task 2 — Show side-effects in graph edge tooltip and pair list

<read_first>
- src/_interactions_new.js lines 568-584 (link mouseover tooltip construction)
- src/_interactions_new.js lines 625-655 (renderPolyPairs function)
- src/_interactions_new.js lines 483-503 (normalizePolyGraph — must pass side_effects through)
</read_first>

<action>
**2a. Update `normalizePolyGraph`** (line ~489) to copy `side_effects` from the pairs map
into each link object:
```javascript
return {
    ...link,
    source,
    target,
    is_harmful:   pair.is_harmful ?? link.is_harmful,
    confidence:   Number(pair.confidence ?? link.confidence ?? 0),
    side_effects: pair.side_effects ?? link.side_effects ?? [],   // ADD
    pair_key:     makePairKey(source, target)
};
```

**2b. Update edge tooltip** (link `mouseover` handler, line ~573):
```javascript
.on('mouseover', (event, d) => {
    const conf = (d.confidence * 100).toFixed(0);
    const severity = getInteractionSeverity(d);
    const se = (d.side_effects || []).slice(0, 2).join('; ');
    tip.innerHTML = `<strong>${escHtml(d.source.id || d.source)} + ${escHtml(d.target.id || d.target)}</strong><br>
        ${escHtml(severity.label)} · ${conf}% confidence${se ? `<br><em>${escHtml(se)}</em>` : ''}`;
    tip.style.display = 'block';
})
```

**2c. Update `renderPolyPairs`** to show side effects under each pair card:
Inside the `.map(p => ...)` template (line ~643), after the confidence badge, add:
```javascript
const seText = (p.side_effects || []).slice(0, 2).join(', ');
// Inside the returned HTML template string, add after the badge span:
${seText ? `<div class="poly-pair-item__se">${escHtml(seText)}</div>` : ''}
```
</action>

<acceptance_criteria>
- src/_interactions_new.js `normalizePolyGraph` contains `side_effects: pair.side_effects`
- src/_interactions_new.js edge tooltip mouseover contains `d.side_effects`
- src/_interactions_new.js `renderPolyPairs` map template contains `p.side_effects`
</acceptance_criteria>

---

## Task 3 — Fix graph disappearing when all pairs are safe

<read_first>
- src/_interactions_new.js lines 529-548 (drawPolypharmacyGraph early return conditions)
</read_first>

<action>
In `drawPolypharmacyGraph`, replace the early-return guard that hides the graph when no
harmful pairs exist (lines ~545-548):

**Current (broken):**
```javascript
if (!showSafePairs && !hasHarmfulLinks) {
    renderPolyGraphMessage(container, 'No harmful interactions found. Turn on "Show safe pairs" to view all checked combinations.');
    return;
}
```

**Replace with:** Remove the early return entirely. Instead, always draw the graph with
whatever `visibleLinks` are available. Only apply the message overlay as a *caption below
the graph*, not as a replacement:

```javascript
// After SVG and simulation are set up, if no harmful links, show info banner inside the SVG
if (!showSafePairs && !hasHarmfulLinks) {
    svg.append('text')
        .attr('x', W / 2).attr('y', 20)
        .attr('text-anchor', 'middle')
        .attr('fill', '#69db7c')
        .attr('font-size', '12px')
        .text('✓ No harmful interactions in this combination — all pairs appear safe');
}
```

This means the graph always shows the drug nodes (even with zero edges when safe-pairs toggle
is off), which is more informative than a blank panel.
</action>

<acceptance_criteria>
- src/_interactions_new.js does NOT contain `return;` immediately after `!hasHarmfulLinks` early-return block
- src/_interactions_new.js contains `✓ No harmful interactions` as SVG text element
- src/_interactions_new.js the `drawPolypharmacyGraph` function proceeds to build simulation even when `!hasHarmfulLinks`
</acceptance_criteria>

</tasks>

<verification>
1. Start Flask backend: `python backend/app.py`
2. Open the app and go to Interactions → Polypharmacy tab.
3. Add Aspirin + Warfarin. Analyze. Confirm the pair list shows side effects (e.g., "Bleeding").
4. Hover over the edge between nodes. Confirm tooltip shows side effect text.
5. Add three drugs that are all predicted safe. Confirm the graph still renders with green nodes and the "✓ No harmful interactions" caption — graph must NOT disappear.
</verification>

<must_haves>
- Side effects from HBase are shown in edge tooltip and pair list
- Graph never disappears when pairs are all safe
- normalizePolyGraph passes side_effects from pairs → links
</must_haves>
