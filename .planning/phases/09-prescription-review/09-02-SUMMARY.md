# 09-02 Summary: review service, confirmation gate, report

- Report stays locked (409) until the user confirms the extracted list.
- Findings: allergy conflicts, documented interactions (with label severity), boxed warnings, duplicate therapy, model-predicted interactions, FAERS signals ranked by IC025. Evidence kinds are labelled; doses are not checked; nothing is called "safe".
- Templated summary and PDF report (PyMuPDF). Routes under /api/prescription.
