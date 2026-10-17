"""
Builds small FAERS quarterly zips in the exact layout of the FDA files (ASCII/DEMO24Q1.txt,
Deleted/DELETE24Q1.txt, '$'-delimited, CRLF) for tests. This is the only place that
generates FAERS-shaped data; the production loader refuses anything this small.

The two-quarter fixture plants the situations the ingest must get right:

  case 1001  two versions: v1 in 2024q1, v2 in 2024q2          -> keep v2 only
  case 1002  age 6 MON                                         -> 0.5 years
  case 1003  age 7 DEC                                         -> 70 years
  case 1004  in 2024q1, listed as deleted in 2024q2            -> dropped entirely
  case 1005  age 150 YR, and age with no unit                  -> NULL age
  case 1006  full event and therapy start dates                -> 31 days to onset
  one malformed line (extra delimiter) in the 2024q1 DEMO file -> counted, skipped
"""

import os
import zipfile

DEMO_COLS = ("primaryid caseid caseversion i_f_code event_dt mfr_dt init_fda_dt fda_dt rept_cod auth_num "
             "mfr_num mfr_sndr lit_ref age age_cod age_grp sex e_sub wt wt_cod rept_dt to_mfr occp_cod "
             "reporter_country occr_country").split()
DRUG_COLS = ("primaryid caseid drug_seq role_cod drugname prod_ai val_vbm route dose_vbm cum_dose_chr "
             "cum_dose_unit dechal rechal lot_num exp_dt nda_num dose_amt dose_unit dose_form dose_freq").split()
REAC_COLS = "primaryid caseid pt drug_rec_act".split()
THER_COLS = "primaryid caseid dsg_drug_seq start_dt end_dt dur dur_cod".split()
OUTC_COLS = "primaryid caseid outc_cod".split()

LAYOUT = {"DEMO": DEMO_COLS, "DRUG": DRUG_COLS, "REAC": REAC_COLS, "THER": THER_COLS, "OUTC": OUTC_COLS}


def _lines(columns, rows):
    out = ["$".join(columns)]
    for row in rows:
        out.append("$".join(str(row.get(c, "")) for c in columns))
    return out


def write_quarter(path, label, demo=(), drug=(), reac=(), ther=(), outc=(), deleted=(), extra_demo_lines=()):
    """Write one quarter zip. `label` like '2024q1'. Rows are dicts; missing fields are blank."""
    yy, q = label[2:4], label[-1]
    tag = f"{yy}Q{q}"
    tables = {"DEMO": demo, "DRUG": drug, "REAC": reac, "THER": ther, "OUTC": outc}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Readme.pdf", b"%PDF-1.4 fixture")
        for name, rows in tables.items():
            lines = _lines(LAYOUT[name], rows)
            if name == "DEMO":
                lines += list(extra_demo_lines)
            zf.writestr(f"ASCII/{name}{tag}.txt", "\r\n".join(lines) + "\r\n")
        # the real DELETE file starts with a blank line, then one case id per line
        zf.writestr(f"Deleted/DELETE{tag}.txt", "\r\n".join([" "] + list(deleted)) + "\r\n")
    return path


def two_quarter_fixture(directory):
    """Create faers_ascii_2024q1.zip and faers_ascii_2024q2.zip in `directory`; return both paths."""
    os.makedirs(directory, exist_ok=True)

    q1_demo = [
        dict(primaryid="10011", caseid="1001", caseversion="1", i_f_code="I", fda_dt="20240110",
             age="65", age_cod="YR", sex="F", occr_country="US", event_dt="20231120"),
        dict(primaryid="10021", caseid="1002", caseversion="1", i_f_code="I", fda_dt="20240215",
             age="6", age_cod="MON", sex="M", occr_country="GB"),
        dict(primaryid="10031", caseid="1003", caseversion="1", i_f_code="I", fda_dt="20240220",
             age="7", age_cod="DEC", sex="F", occr_country="US"),
        dict(primaryid="10041", caseid="1004", caseversion="1", i_f_code="I", fda_dt="20240301",
             age="40", age_cod="YR", sex="M", occr_country="US"),
    ]
    q1_drug = [
        dict(primaryid="10011", caseid="1001", drug_seq="1", role_cod="PS", drugname="WARFARIN SODIUM", prod_ai="WARFARIN"),
        dict(primaryid="10011", caseid="1001", drug_seq="2", role_cod="C", drugname="ASPIRIN", prod_ai="ASPIRIN"),
        dict(primaryid="10021", caseid="1002", drug_seq="1", role_cod="PS", drugname="AMOXICILLIN", prod_ai="AMOXICILLIN"),
        dict(primaryid="10031", caseid="1003", drug_seq="1", role_cod="PS", drugname="METFORMIN", prod_ai="METFORMIN HYDROCHLORIDE"),
        dict(primaryid="10041", caseid="1004", drug_seq="1", role_cod="PS", drugname="IBUPROFEN", prod_ai="IBUPROFEN"),
    ]
    q1_reac = [
        dict(primaryid="10011", caseid="1001", pt="Nausea"),
        dict(primaryid="10021", caseid="1002", pt="Rash"),
        dict(primaryid="10031", caseid="1003", pt="Lactic acidosis"),
        dict(primaryid="10041", caseid="1004", pt="Headache"),
    ]
    q1_ther = [dict(primaryid="10011", caseid="1001", dsg_drug_seq="1", start_dt="20230101")]
    q1_outc = [dict(primaryid="10011", caseid="1001", outc_cod="OT"),
               dict(primaryid="10041", caseid="1004", outc_cod="HO")]
    # extra '$' => 26 fields instead of 25: counted as malformed and skipped
    malformed = "10099$1099$1$I$$$$20240101$$$$$$40$YR$$M$$$$$$$$US$US$EXTRA"

    q2_demo = [
        dict(primaryid="10012", caseid="1001", caseversion="2", i_f_code="F", fda_dt="20240410",
             age="66", age_cod="YR", sex="F", occr_country="US", event_dt="20231120"),
        dict(primaryid="10051", caseid="1005", caseversion="1", i_f_code="I", fda_dt="20240415",
             age="150", age_cod="YR", sex="F", occr_country="DE"),
        dict(primaryid="10052", caseid="1007", caseversion="1", i_f_code="I", fda_dt="20240416",
             age="30", age_cod="", sex="M", occr_country="DE"),
        dict(primaryid="10061", caseid="1006", caseversion="1", i_f_code="I", fda_dt="20240420",
             age="50", age_cod="YR", sex="M", occr_country="US", event_dt="20240102"),
    ]
    q2_drug = [
        dict(primaryid="10012", caseid="1001", drug_seq="1", role_cod="PS", drugname="COUMADIN", prod_ai="WARFARIN"),
        dict(primaryid="10051", caseid="1005", drug_seq="1", role_cod="SS", drugname="LISINOPRIL", prod_ai="LISINOPRIL"),
        dict(primaryid="10052", caseid="1007", drug_seq="1", role_cod="PS", drugname="SERTRALINE", prod_ai="SERTRALINE"),
        dict(primaryid="10061", caseid="1006", drug_seq="1", role_cod="PS", drugname="WARFARIN", prod_ai="WARFARIN"),
    ]
    q2_reac = [
        dict(primaryid="10012", caseid="1001", pt="Haemorrhage"),
        dict(primaryid="10051", caseid="1005", pt="Cough"),
        dict(primaryid="10052", caseid="1007", pt="Nausea"),
        dict(primaryid="10061", caseid="1006", pt="Haemorrhage"),
    ]
    q2_ther = [dict(primaryid="10061", caseid="1006", dsg_drug_seq="1", start_dt="20231202")]
    q2_outc = [dict(primaryid="10012", caseid="1001", outc_cod="HO")]

    p1 = write_quarter(os.path.join(directory, "faers_ascii_2024q1.zip"), "2024q1", q1_demo, q1_drug, q1_reac,
                       q1_ther, q1_outc, deleted=[], extra_demo_lines=[malformed])
    p2 = write_quarter(os.path.join(directory, "faers_ascii_2024q2.zip"), "2024q2", q2_demo, q2_drug, q2_reac,
                       q2_ther, q2_outc, deleted=["1004"])
    return p1, p2


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    for p in two_quarter_fixture(target):
        print("wrote", p)
