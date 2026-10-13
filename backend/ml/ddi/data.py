# backend/ml/ddi/data.py
# Per-drug molecular graph and fingerprint precomputation shared by training and calibration.

from ml.ddi.model import smiles_to_molecular_graph, smiles_to_fingerprint


def precompute_drug_structures(drug_metadata):
    """Pre-computes molecular graphs and fingerprints for all drugs once."""
    print(f"[CACHE] Pre-computing molecular graphs and fingerprints for {len(drug_metadata['drug_smiles'])} drugs...", flush=True)
    graph_dict = {}
    fp_dict = {}

    for drug_id, smi in drug_metadata['drug_smiles'].items():
        g = smiles_to_molecular_graph(smi)
        if g is None:
            g = smiles_to_molecular_graph('C')
        graph_dict[drug_id] = g
        fp_dict[drug_id] = smiles_to_fingerprint(smi)

    print("[CACHE] Pre-computation complete.", flush=True)
    return graph_dict, fp_dict
