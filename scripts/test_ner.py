import sys
import os

# Add backend folder to sys.path so we can import app
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import run_ner, clean_and_validate_entity

text = """
Chief Complaint:
Shortness of breath, fatigue, and swelling in both legs for 5 days.

History of Present Illness:
The patient presented to the emergency department with progressive shortness of breath on exertion, bilateral lower limb edema, and generalized fatigue. No history of chest pain, fever, or recent travel. Past medical history significant for hypertension, atrial fibrillation, and type 2 diabetes mellitus.

Hospital Course:
The patient was admitted for management of acute decompensated heart failure. Intravenous diuretics were administered with good clinical response. Cardiology consultation was obtained. Echocardiography demonstrated reduced left ventricular ejection fraction of 35%. Blood glucose levels were monitored and managed throughout hospitalization. Symptoms improved significantly prior to discharge.

Final Diagnoses:
1. Acute decompensated congestive heart failure
2. Atrial fibrillation
3. Hypertension
4. Type 2 diabetes mellitus

Laboratory Findings:
Hemoglobin: 12.8 g/dL
White Blood Cell Count: 7.2 x10^9/L
Platelets: 220 x10^9/L
Serum Creatinine: 1.1 mg/dL
Blood Urea Nitrogen: 24 mg/dL
Potassium: 4.2 mmol/L
HbA1c: 7.4%

Discharge Medications:
1. Warfarin 5 mg orally once daily
2. Aspirin 81 mg orally once daily
3. Metformin 500 mg orally twice daily
4. Furosemide 40 mg orally once daily
5. Lisinopril 10 mg orally once daily
6. Atorvastatin 20 mg orally at bedtime

Discharge Condition:
Stable. Ambulating independently. No acute distress.
"""

print("Running NER on test text...")
entities = run_ner(text)
for e in entities:
    print(f"Token: '{e['token']}' | Label: {e['label']} | Score: {e['score']}")
