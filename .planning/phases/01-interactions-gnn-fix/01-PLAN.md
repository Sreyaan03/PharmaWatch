---
phase: 1
plan: 01
type: feature
wave: 1
depends_on: []
files_modified:
  - scripts/train_gnn_model.py
  - backend/app.py
  - src/_interactions_new.js
autonomous: true
requirements:
  - GNN-01
  - GNN-02
  - GNN-03
  - GNN-04
  - GNN-05
  - GNN-06
  - GNN-07
---

# Plan 01 — Fix GNN Training Pipeline (Negative Examples + Balanced Dataset)

<objective>
Repair the fundamental flaw in the GNN drug-drug interaction predictor: the model is currently
trained on ALL-POSITIVE labels (every row in TWOSIDES is label=1.0), making it unable to
distinguish safe from harmful pairs. This plan adds synthetic negative examples, increases
training data volume, and raises epoch count so the model outputs meaningful predictions.
</objective>

<tasks>

## Task 1 — Add Negative Sampling to DDIDataset

<read_first>
- scripts/train_gnn_model.py (full file — understand current DDIDataset and smiles_to_graph)
- data/ directory listing (confirm twosides.parquet path)
</read_first>

<action>
In `scripts/train_gnn_model.py`, replace the `DDIDataset` class (lines 54-75) with a version
that generates a balanced 50/50 positive/negative dataset:

1. Load the positive pairs from `twosides.parquet` (columns X1=SMILES_A, X2=SMILES_B).
   Use `.sample(min(25000, len(df)))` instead of `.head(5000)` — 25 000 positives.

2. Generate an equal number of NEGATIVE pairs by:
   - Collecting all unique SMILES strings from column X1 and X2 into a single pool.
   - Randomly shuffling that pool and pairing entry i with entry i+1 (wrap-around).
   - Removing any pair whose sorted (smi_a, smi_b) already appears in the positive set
     (use a set of frozensets for fast O(1) lookup).
   - Keep up to 25 000 negative pairs.

3. Concatenate positives (label=1.0) and negatives (label=0.0) into a single DataFrame,
   shuffle rows (`df.sample(frac=1, random_state=42).reset_index(drop=True)`).

4. Store as `self.df` and `self.labels` (separate list so __getitem__ reads labels[idx]).

5. Update `__len__` and `__getitem__` accordingly.
   `__getitem__` must return `(graph1, graph2, torch.FloatTensor([self.labels[idx]]))`.

Concrete replacement for the class signature and __init__:
```python
class DDIDataset(Dataset):
    def __init__(self, parquet_path, n_pairs=25000, random_state=42):
        print(f"Loading data from {parquet_path}...")
        pos_df = pd.read_parquet(parquet_path).dropna(subset=['X1','X2'])
        pos_df = pos_df.sample(min(n_pairs, len(pos_df)), random_state=random_state)[['X1','X2']]
        pos_df['label'] = 1.0

        # Build negative pairs from shuffled SMILES pool
        all_smiles = list(set(pos_df['X1'].tolist() + pos_df['X2'].tolist()))
        import random; rng = random.Random(random_state)
        rng.shuffle(all_smiles)
        pos_set = set(frozenset(r) for r in pos_df[['X1','X2']].values)
        neg_pairs = []
        for i in range(len(all_smiles)):
            a = all_smiles[i]; b = all_smiles[(i+1) % len(all_smiles)]
            if frozenset({a,b}) not in pos_set and a != b:
                neg_pairs.append({'X1': a, 'X2': b, 'label': 0.0})
            if len(neg_pairs) >= n_pairs: break
        neg_df = pd.DataFrame(neg_pairs)

        combined = pd.concat([pos_df, neg_df], ignore_index=True)
        combined = combined.sample(frac=1, random_state=random_state).reset_index(drop=True)
        self.df = combined
        print(f"Dataset: {len(self.df)} pairs ({int(self.df['label'].sum())} pos / {int((self.df['label']==0).sum())} neg)")
```
</action>

<acceptance_criteria>
- scripts/train_gnn_model.py contains `label = 0.0` (negative pairs exist)
- scripts/train_gnn_model.py contains `neg_pairs` variable
- scripts/train_gnn_model.py contains `sample(min(n_pairs` (larger sample size)
- scripts/train_gnn_model.py does NOT contain `.head(5000)` (old truncation removed)
- scripts/train_gnn_model.py does NOT contain `label = torch.FloatTensor([1.0])` (all-positive label gone)
</acceptance_criteria>

---

## Task 2 — Add Richer Atom Features (8 → 16 features)

<read_first>
- scripts/train_gnn_model.py (smiles_to_graph function and GNN_Predictor node_feature_dim)
</read_first>

<action>
Extend `smiles_to_graph` to produce 16-dimensional node feature vectors (from current 8).
Add to the per-atom encoding list after the existing 8 entries:

```python
float(atom.GetTotalNumHs()),           # hydrogen count
float(atom.GetIsAromatic()),           # aromaticity flag  0/1
float(atom.IsInRing()),                # ring membership  0/1
float(atom.GetChiralTag() != 0),       # stereo center    0/1
1.0 if symbol == 'P'  else 0.0,        # Phosphorus (common in drugs)
1.0 if symbol == 'Br' else 0.0,        # Bromine
1.0 if symbol == 'I'  else 0.0,        # Iodine
float(atom.GetHybridization().name in ('SP2','SP3'))  # hybridization
```

Update `GNN_Predictor.__init__` to use `node_feature_dim=16`.
Update `_load_gnn()` in `backend/app.py` to also pass `node_feature_dim=16`.
Update the fallback dummy graph in DDIDataset `__getitem__` to use `torch.zeros((1,16))`.
</action>

<acceptance_criteria>
- scripts/train_gnn_model.py contains `GetTotalNumHs` (new feature)
- scripts/train_gnn_model.py contains `GetIsAromatic` (new feature)
- scripts/train_gnn_model.py `GNN_Predictor(node_feature_dim=16` appears
- backend/app.py `GNN_Predictor(node_feature_dim=16` appears (load_gnn updated)
- scripts/train_gnn_model.py contains `torch.zeros((1,16))` (dummy fallback updated)
</acceptance_criteria>

---

## Task 3 — Increase Training to 20 Epochs + Add Validation Split

<read_first>
- scripts/train_gnn_model.py (train_model function, lines 115-151)
</read_first>

<action>
Replace the `train_model()` function body with:

1. Change `epochs = 5` → `epochs = 20`.
2. After building the dataset, split 80/20 train/val:
   ```python
   from torch.utils.data import random_split
   train_size = int(0.8 * len(dataset))
   val_size   = len(dataset) - train_size
   train_ds, val_ds = random_split(dataset, [train_size, val_size], generator=torch.Generator().manual_seed(42))
   train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
   val_loader   = DataLoader(val_ds,   batch_size=256, shuffle=False)
   ```
3. After each epoch, run a validation loop that computes:
   - val_loss (BCEWithLogitsLoss)
   - val_accuracy (sigmoid(out) > 0.5 vs label)
   Print: `Epoch {e}/{epochs} | train_loss={x:.4f} | val_loss={x:.4f} | val_acc={x:.1%}`
4. Save model only if val_accuracy improves (best-model checkpoint):
   ```python
   best_val_acc = 0.0
   # ... inside epoch loop ...
   if val_acc > best_val_acc:
       best_val_acc = val_acc
       torch.save(model.state_dict(), model_path)
       print(f"  ✓ New best val_acc={val_acc:.1%} — model saved")
   ```
5. Print final summary: `Training complete. Best val_accuracy={best_val_acc:.1%}`
</action>

<acceptance_criteria>
- scripts/train_gnn_model.py contains `epochs = 20`
- scripts/train_gnn_model.py contains `val_acc` (validation accuracy computed)
- scripts/train_gnn_model.py contains `random_split` (train/val split)
- scripts/train_gnn_model.py contains `best_val_acc` (best-model checkpoint)
- scripts/train_gnn_model.py does NOT contain `epochs = 5`
</acceptance_criteria>

</tasks>

<verification>
1. Run the training script and confirm it executes without error:
   `cd c:\Users\Sreyaan\...\PharmaWatch && python scripts/train_gnn_model.py`
2. Confirm output contains both positive and negative pair counts in dataset summary line.
3. Confirm val_accuracy printed per epoch.
4. Confirm `backend/trained_models/twosides_gnn_model.pth` is updated (newer timestamp).
</verification>

<must_haves>
- Model is no longer trained on 100% positive labels
- Dataset contains ≥ 25 000 pairs with ~50% positive / ~50% negative
- Validation accuracy is measurable and printed
- Model saves only when validation accuracy improves
- node_feature_dim=16 is consistent across train script and backend loader
</must_haves>
