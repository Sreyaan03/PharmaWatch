# 📚 PharmaWatch — Documentation Index

All project documentation is organised here. Start with the file that matches what you need.

---

## 🚀 Getting started

| File | What it covers |
|---|---|
| [`../README.md`](../README.md) | What the tool is and is not, the seven areas, quick start, validation headline, licence. |
| [`DEPLOYMENT.md`](./DEPLOYMENT.md) | Install, configure, run (Docker profiles or no-Docker), back up, upgrade and host on a server. |
| [`PILOT_GUIDE.md`](./PILOT_GUIDE.md) | For college staff: purpose and limits, roles and accounts, teaching exercises, feedback, data protection, reporting problems. |

> The older `README_SIMPLE.md` and `how_to_run.txt` have been folded into the README and
> `DEPLOYMENT.md`; they now point here.

---

## 🤖 Methods & validation

| File | What it covers |
|---|---|
| [`METHODS.md`](./METHODS.md) | Every formula, threshold, data rule and limitation: statistics, interaction model, text mining, lead-time study, validation. |

Measured accuracy is shown in the app (Methods & Validation) and regenerated with
`python -m validation.run_all`.

---

## 📊 Data & big data

| File | What it covers |
|---|---|
| [`big_data_implementation_guide.md`](./big_data_implementation_guide.md) | DuckDB, Parquet, FAERS pipeline. |
| [`boxed_warning_literature_and_insights.md`](./boxed_warning_literature_and_insights.md) | FDA boxed-warning data sources and analysis. |
| [`SCALING.md`](./SCALING.md) | State audit and horizontal scale-out notes. |

---

## 🔧 Development & maintenance

| File | What it covers |
|---|---|
| [`PROJECT_DEFINITION.md`](./PROJECT_DEFINITION.md) | Project goals, scope and vision. |
| [`FEATURE_AUDIT.md`](./FEATURE_AUDIT.md) | Audit of features — what works, what's pending. |
| [`UAT_CHECKLIST.md`](./UAT_CHECKLIST.md) | User-acceptance checklist, worked as each role. |

---

## 🐳 Deployment

See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full guide. The [`docker/`](../docker/README.md)
folder documents the image, the single `compose.yml` with its `core` / `kafka` / `demo` profiles,
and the separate scale-out file.
