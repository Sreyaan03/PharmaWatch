# backend/faers/stats.py
# Small statistical helpers shared by the FAERS analytics and the openFDA fallback.

import numpy as np


def zscore_spikes(counts, window=12, min_history=3, threshold=2.0):
    """Flag points more than `threshold` standard deviations above the trailing window.

    Returns (spikes, z_scores), one entry per count. The first `min_history`
    points are never flagged.
    """
    spikes = []
    z_scores = []
    counts_arr = np.array(counts, dtype=float)

    for i in range(len(counts)):
        if i < min_history:
            z_scores.append(0.0)
            spikes.append(False)
        else:
            w = counts_arr[max(0, i - window):i]
            mean = np.mean(w)
            std = np.std(w)
            if std == 0:
                std = 1.0
            z = (counts_arr[i] - mean) / std
            z_scores.append(round(float(z), 2))
            spikes.append(bool(z > threshold))
    return spikes, z_scores


def contingency(con, ingredient_rxcuis, pt, where=None):
    """The 2x2 table for one drug and one event, over de-duplicated cases.

    `ingredient_rxcuis`  one RxNorm ingredient, or several that must all be suspect in the same
                         case (a combination product).
    `pt`                 a MedDRA Preferred Term, matched case-insensitively.
    `where`              optional (sql, params) restricting the cohort, for example a sex or age
                         stratum: ("sex = ?", ["F"]). Written by the application, never by users.

    A drug counts as present when it is primary or secondary suspect. Returns
    {'a', 'b', 'c', 'd', 'n'} with a = drug and event, b = drug without event,
    c = event without drug, d = neither, n = cases in the cohort.
    """
    if isinstance(ingredient_rxcuis, str):
        ingredient_rxcuis = [ingredient_rxcuis]
    ingredient_rxcuis = list(dict.fromkeys(ingredient_rxcuis))
    if not ingredient_rxcuis:
        raise ValueError("at least one ingredient is required")

    cohort_where, cohort_params = (where if where else ("TRUE", []))
    marks = ", ".join("?" for _ in ingredient_rxcuis)
    row = con.execute(f"""
        WITH cohort AS (
            SELECT primaryid FROM faers_demo WHERE {cohort_where}
        ),
        exposed AS (
            SELECT cd.primaryid FROM case_drug cd
            JOIN cohort USING (primaryid)
            WHERE cd.suspect AND cd.ingredient_rxcui IN ({marks})
            GROUP BY cd.primaryid
            HAVING count(DISTINCT cd.ingredient_rxcui) = ?
        ),
        event AS (
            SELECT DISTINCT r.primaryid FROM faers_reac r
            JOIN cohort USING (primaryid)
            WHERE lower(r.pt) = lower(?)
        )
        SELECT (SELECT count(*) FROM cohort),
               (SELECT count(*) FROM exposed),
               (SELECT count(*) FROM event),
               (SELECT count(*) FROM exposed JOIN event USING (primaryid))
    """, list(cohort_params) + ingredient_rxcuis + [len(ingredient_rxcuis), pt]).fetchone()
    n, exposed, event, a = (int(x) for x in row)
    return {"a": a, "b": exposed - a, "c": event - a, "d": n - exposed - event + a, "n": n}
