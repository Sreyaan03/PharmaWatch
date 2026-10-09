# 🔬 GNN Methodology & Dataset Calibration: The 13.19% Negative Label Derivation

This document outlines the theoretical, mathematical, and empirical justification for the **Label Smoothing and Soft Target Loss** calibration applied to the Graph Neural Network (GNN) in PharmaWatch.

---

## 1. The Core Problem: The Open-World Assumption in DDI

In clinical pharmacology databases (such as DrugBank, TWOSIDES, and FDA FAERS), **only positive drug-drug interactions (DDIs) are systematically documented**. Medical literature and adverse event registries report when combinations fail or produce toxic side effects; they do not systematically publish records confirming when a drug combination produces *zero* adverse effects.

When training a predictive binary classifier on drug pairs $(A, B) \in \mathcal{D}$:
* **Positives ($Y = 1$):** Documented interactions verified by clinical literature or regulatory authorities.
* **Negatives ($Y = 0$):** Pairs without documented interactions in the database.

Treating unrecorded pairs as **hard negative ground truth ($Y = 0.0$)** introduces severe mathematical distortions:
1. **False Negative Bias:** Pairs that actually interact in human physiology, but remain unstudied or uncataloged, are mislabeled as $0.0$.
2. **Gradient Shock:** If the GNN detects a legitimate reactive chemical motif (e.g., strong Cytochrome P450 inhibition characteristics) in an unverified pair, backpropagation with a hard $0.0$ target violently penalizes the network, forcing it to unlearn real chemical interaction signals.

---

## 2. Empirical Proof: Direct Measurement on DrugBank Data

Rather than selecting an arbitrary heuristic, the label calibration is anchored directly to the **empirical topological density of the training graph** in `data/drugbank_ddi.tsv`.

From the TDC DrugBank dataset used in [`scripts/train_gnn_model.py`](../scripts/train_gnn_model.py):

| Metric | Measured Value | Mathematical Definition |
| :--- | :--- | :--- |
| **Unique approved drugs ($N$)** | **1,706** | Unique SMILES strings in training pool |
| **Total possible drug pairs ($|\mathcal{P}|$)** | **1,454,365** | $\frac{N(N - 1)}{2} = \frac{1706 \times 1705}{2}$ |
| **Documented interacting pairs ($|\mathcal{E}|$)** | **191,808** | Confirmed rows in `drugbank_ddi.tsv` |
| **Empirical Graph Density ($\pi$)** | **13.1884% ($\approx 0.132$)** | $\pi = \frac{|\mathcal{E}|}{|\mathcal{P}|} = \frac{191,808}{1,454,365}$ |

$$\pi_{\text{empirical}} = \frac{191,808}{1,454,365} = 0.131884 \dots \approx \mathbf{0.132} \; (\mathbf{13.19\%})$$

### Interpretation
In the DrugBank chemical universe, **13.19% of all possible pairwise drug combinations are confirmed to interact**. 

Because DrugBank is an incomplete reflection of all real-world human interactions (due to under-reporting of mild interactions), the true biological collision probability is at least $13.19\%$ (with literature estimates placing it between $13\%$ and $18\%$).

---

## 3. Mathematical Foundations

### 3.1. Positive-Unlabeled (PU) Learning Theory (Elkan & Noto, 2008)
Under the Positive-Unlabeled learning framework (Elkan & Noto, *ACM SIGKDD 2008*), an unlabeled pair drawn uniformly at random from the unverified set $U$ is a probabilistic mixture of true negatives and unobserved positives:

$$P(Y = 1 \mid \text{pair} \in U) = \pi$$

where $\pi = P(Y=1)$ is the class prior (the base rate of interactions in the population).

The Bayes-optimal expected value for an unverified pair sampled uniformly from this space is:
$$\mathbb{E}[Y \mid \text{pair} \in U] = \pi \approx \mathbf{0.132}$$

By assigning a soft target of $\mathbf{0.132}$ (or the conservative upper-bound $\mathbf{0.15}$) instead of $0.0$, the loss function aligns with the mathematical expectation of the unlabeled mixture, eliminating systematic posterior probability shrinkage.

---

### 3.2. Label Smoothing Regularization (Müller, Kornblith, & Hinton, 2019)
Standard binary cross-entropy drives neural network logits toward $\pm \infty$, leading to overconfidence:

$$\mathcal{L}_{\text{BCE}} = - \Big[ y \log \sigma(\hat{y}) + (1 - y) \log (1 - \sigma(\hat{y})) \Big]$$

Applying symmetric label smoothing with parameter $\alpha = 0.264$ ($2 \times 0.132$):
* **Positive Label ($y = 1$):**
  $$y_{\text{pos}} = 1 - \frac{\alpha}{2} = 1 - 0.132 = \mathbf{0.868} \quad (\text{or rounded to } \mathbf{0.90})$$
* **Negative Label ($y = 0$):**
  $$y_{\text{neg}} = 0 + \frac{\alpha}{2} = \mathbf{0.132} \quad (\text{or rounded to } \mathbf{0.15})$$

This bounds the gradient magnitude, improves out-of-distribution generalization on novel chemical entities, and prevents overfitting on unverified negative candidates.

---

## 4. Implementation in PharmaWatch

These values are configured in [`scripts/train_gnn_model.py`](../scripts/train_gnn_model.py):

```python
class DDIDataset(Dataset):
    def __init__(self, n_pairs=25000, random_state=42, pos_label=0.868, neg_label=0.132):
        # ...
        # Confirmed interactions:
        pos_df['label'] = pos_label   # 0.868 (or 0.90)
        
        # Sampled unrecorded pairs:
        neg_pairs.append({'X1': a, 'X2': b, 'label': neg_label})  # 0.132 (or 0.15)
```

And in the training objective:
```python
# Positive class weight prioritizes confirmed interactions over unverified pairs
pos_weight = torch.tensor([1.5])
criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
```

At validation and inference time, the clinical decision boundary remains:
$$\text{Harmful Interaction} \iff \sigma(\hat{y}) > 0.50$$

---

## 5. Peer-Reviewed References

1. **Elkan, C., & Noto, K. (2008).** *"Learning classifiers from only positive and unlabeled data."* In Proceedings of the 14th ACM SIGKDD International Conference on Knowledge Discovery and Data Mining (pp. 213–220).
2. **Müller, R., Kornblith, S., & Hinton, G. E. (2019).** *"When does label smoothing help?"* Advances in Neural Information Processing Systems (NeurIPS 2019), 32.
3. **Ayvaz, S., Horn, J., Hassanzadeh, O., et al. (2015).** *"Toward a complete dataset of drug–drug interaction information from publicly available sources."* Journal of Biomedical Informatics, 55, 206–217.
4. **Ryu, J. Y., Kim, H. U., & Lee, S. Y. (2018).** *"Deep learning improves prediction of drug–drug and drug–food interactions."* Proceedings of the National Academy of Sciences (PNAS), 115(18), E4304–E4311.
