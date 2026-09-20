"""Static vendor / questionnaire / attachment text for generate_dataset.py. No physics here."""

EVAL_DATE = "2026-03-16"

# id, name, archetype, format, posture, quirk, distance_km, freight_pct_truth, moq, gates_ok
VENDORS = [
    ("V1", "Sahyadri Packaging Industries Ltd", "Organized converter", "xlsx", "market +3%",
     "Own-layout Excel; rates assume full award, volume tier table on a second sheet", 620, 0.060, 1000, 1),
    ("V2", "Kaveri Board & Cartons Pvt Ltd", "Regional converter", "pdf", "market -2%",
     "Letterhead PDF; 2% discount only in a 7pt footnote; some lines per 100 pcs", 250, 0.030, 3000, 1),
    ("V3", "Om Sai Box Works", "Small local converter", "docx", "aggressive -8%",
     "Prose commercials; 120 GSM liners where 150 asked; vague on GSM; declines 3 lines", 40, 0.012, 500, 0),
    ("V4", "Meridian Pack Trading Co", "Trader / exporter", "jpg", "market",
     "Angled photo of a rate card; USD; some lines per kg", 480, 0.045, 5000, 0),
    ("V5", "Vardhaman Carton Works", "Incumbent", "eml", "last year +4%",
     "One-line email, per kg, 'rest same as last year'", 120, 0.020, None, 1),
]

# received_on, validity_days (None = not stated), incoterm, file
SUBMISSIONS = {
    "V1": ("2026-03-10", 30, "EXW", "V1_Sahyadri_Quotation.xlsx"),
    "V2": ("2026-03-06", 15, None, "V2_Kaveri_Quotation.pdf"),
    "V3": ("2026-03-04", 10, None, "V3_OmSai_Offer.docx"),
    "V4": ("2026-03-07", 14, "EXW", "V4_Meridian_RateCard.jpg"),
    "V5": ("2026-03-11", None, None, "V5_Vardhaman_Reply.eml"),
}

QUESTIONS = [
    (1, "G1", "Do you hold a current BRCGS Packaging Materials certification? State grade and expiry; attach certificate."),
    (2, "G2", "Do you operate an in-house box compression (BCT) testing facility? State equipment and calibration date."),
    (3, "G3", "Do you hold FSC Chain-of-Custody certification covering the products quoted? State licence no.; attach."),
    (4, None, "BIS IS:2771 test certificate for the quoted board grade - attach."),
    (5, None, "ISO 9001 certification status and expiry - attach."),
    (6, None, "Monthly production capacity (tonnes) and current utilisation."),
    (7, None, "Dedicated capacity commitment for this contract."),
    (8, None, "Recycled content % of the board."),
    (9, None, "Batch-level traceability and test-sample retention period."),
    (10, None, "Manufacturing locations and distance to buyer plants."),
    (11, None, "Contingency / secondary plant in case of a line stoppage."),
    (12, None, "Price escalation mechanism: index, reset frequency, cap."),
    (13, None, "Payment terms offered and any early-payment discount."),
    (14, None, "Rejection handling: replacement lead time and debit-note process."),
]

# What each RFx question asks the vendor to evidence (buyer-side: part of the RFx, not of truth)
EVIDENCE_KIND = {1: "brcgs_packaging_cert", 2: "bct_calibration_cert", 3: "fsc_coc_cert", 4: "bis_is2771_report", 5: "iso9001_cert"}

# id, vendor, kind, file, issuer, entity, valid_to, supports, note
ATTACHMENTS = [
    ("A1", "V1", "brcgs_packaging_cert", "V1_BRCGS_Certificate.pdf", "BRCGS via SGS India", "Sahyadri Packaging Industries Ltd", "2026-11-30", "1", "Grade AA"),
    ("A2", "V1", "fsc_coc_cert", "V1_FSC_CoC.pdf", "FSC via Rainforest Alliance", "Sahyadri Packaging Industries Ltd", "2027-01-31", "3", "FSC-C104417, Mix Credit"),
    ("A3", "V1", "bct_calibration_cert", "V1_BCT_Calibration.pdf", "NABL lab", "Sahyadri Packaging Industries Ltd", "2026-12-15", "2", "50 kN compression tester"),
    ("A4", "V1", "iso9001_cert", "V1_ISO9001.pdf", "TUV India", "Sahyadri Packaging Industries Ltd", "2027-06-30", "5", ""),
    ("A5", "V1", "bis_is2771_report", "V1_IS2771_Report.pdf", "BIS-recognised lab", "Sahyadri Packaging Industries Ltd", "2026-12-31", "4", "Scanned-look PDF"),
    ("A6", "V2", "brcgs_packaging_cert", "V2_BRCGS_Certificate.pdf", "BRCGS via Intertek", "Kaveri Board & Cartons Pvt Ltd", "2027-02-28", "1", "Grade A"),
    ("A7", "V2", "fsc_coc_cert", "V2_FSC_CoC.pdf", "FSC via Control Union", "Kaveri Board & Cartons Pvt Ltd", "2026-09-30", "3", "FSC-C118820, valid but expires in 6 months"),
    ("A8", "V2", "bct_calibration_cert", "V2_BCT_Calibration.pdf", "NABL lab", "Kaveri Board & Cartons Pvt Ltd", "2026-10-10", "2", ""),
    ("A9", "V3", "iso9001_cert", "V3_ISO9001.pdf", "Local certifier", "Om Sai Box Works", "2025-09-14", "5", "EXPIRED"),
    ("A10", "V3", "third_party_lab_report", "V3_Lab_Test_Report.pdf", "Shri Ram Test House", "Om Sai Box Works", None, "2", "Compression testing is outsourced, so G2 fails"),
    ("A11", "V4", "fsc_coc_cert", "V4_FSC_CoC.pdf", "FSC via Control Union", "Meridian Paper Exports LLP", "2025-12-31", "3", "EXPIRED and licensee is a different entity"),
]

# per vendor: q_no -> (answer_text, answer_bool, attachment_id, score /5, gate_status, truth_reason)
ANSWERS = {
    "V1": {
        1: ("Yes. BRCGS Packaging Materials Grade AA, valid to 30-Nov-2026.", 1, "A1", None, "pass", "valid certificate attached"),
        2: ("Yes. In-house BCT lab, 50 kN compression tester, calibrated Jan-2026.", 1, "A3", None, "pass", "calibration cert attached"),
        3: ("Yes. FSC-C104417 (Mix Credit).", 1, "A2", None, "pass", "valid to 2027-01-31"),
        4: ("Attached.", 1, "A5", 5, None, None),
        5: ("ISO 9001:2015, valid to 30-Jun-2027.", 1, "A4", 5, None, None),
        6: ("3,200 t/month, 74% utilised.", None, None, 5, None, None),
        7: ("Up to 12% of monthly capacity reserved for this contract.", 1, None, 4, None, None),
        8: ("Approx. 65% recycled content in medium; liners virgin kraft.", None, None, 4, None, None),
        9: ("Batch-coded; samples retained 12 months.", 1, None, 5, None, None),
        10: ("Three plants; nearest is 620 km from your plants.", None, None, 3, None, None),
        11: ("Yes, sister plant 180 km from the primary.", 1, None, 4, None, None),
        12: ("Quarterly reset on kraft index, +/-5% cap.", None, None, 4, None, None),
        13: ("45 days net; 1% for payment within 10 days.", None, None, 4, None, None),
        14: ("Replacement in 5 days; debit note within 7.", None, None, 4, None, None),
    },
    "V2": {
        1: ("Yes. BRCGS Packaging Materials Grade A, valid to 28-Feb-2027.", 1, "A6", None, "pass", "valid certificate attached"),
        2: ("Yes. In-house BCT lab, calibrated Oct-2025.", 1, "A8", None, "pass", "calibration cert attached"),
        3: ("Yes. FSC-C118820 (Mix Credit).", 1, "A7", None, "pass", "valid to 2026-09-30"),
        4: ("Test report available; sent separately.", 1, None, 3, None, None),
        5: ("ISO 9001:2015 certified; certificate on request.", 1, None, 3, None, None),
        6: ("1,400 t/month, 81% utilised.", None, None, 3, None, None),
        7: ("8% of capacity reserved.", 1, None, 3, None, None),
        8: ("About 70% recycled content.", None, None, 4, None, None),
        9: ("Batch-coded; samples kept 6 months.", 1, None, 3, None, None),
        10: ("Two plants; nearest is 250 km away.", None, None, 4, None, None),
        11: ("Secondary plant covers up to 40% of volume.", 1, None, 3, None, None),
        12: ("Monthly reset on kraft index, no cap.", None, None, 2, None, None),
        13: ("30 days net; 2% settlement discount on orders above Rs 50 lakh.", None, None, 3, None, None),
        14: ("Replacement in 7 days.", None, None, 3, None, None),
    },
    "V3": {
        1: ("In progress; audit scheduled for Q3 2026.", 0, None, None, "fail", "no BRCGS certificate held"),
        2: ("Testing is done at an outside test house.", 0, "A10", None, "fail", "compression testing outsourced, not in-house"),
        3: ("Yes, FSC material is available on request.", 1, None, None, "fail", "claim with no certificate attached"),
        4: ("Will send.", None, None, 1, None, None),
        5: ("ISO 9001 certificate attached.", 1, "A9", 1, None, None),
        6: ("300 t/month, running at about 90%.", None, None, 2, None, None),
        7: ("Cannot commit dedicated capacity.", 0, None, 0, None, None),
        8: ("Mostly recycled, about 90%.", None, None, 3, None, None),
        9: ("Job card only.", 0, None, 1, None, None),
        10: ("One plant, 40 km from your plants.", None, None, 3, None, None),
        11: ("No.", 0, None, 0, None, None),
        12: ("As per market.", None, None, 1, None, None),
        13: ("50% advance, balance before dispatch.", None, None, 1, None, None),
        14: ("Replaced against the next order.", None, None, 2, None, None),
    },
    "V4": {
        1: ("No. We source from certified converters.", 0, None, None, "fail", "trader, no BRCGS certificate of its own"),
        2: ("Testing is done by our supplying converters.", 0, None, None, "fail", "no facility of its own"),
        3: ("Yes. FSC certificate attached.", 1, "A11", None, "fail", "attached certificate is expired and names a different entity"),
        4: ("Supplier certificates available on request.", None, None, 1, None, None),
        5: ("Not applicable to a trading company.", 0, None, 0, None, None),
        6: ("Sourced from four converters; no fixed figure.", None, None, 2, None, None),
        7: ("Subject to supplier confirmation.", None, None, 1, None, None),
        8: ("Varies by supplier.", None, None, 1, None, None),
        9: ("Traceable to supplier lot, not to batch.", None, None, 1, None, None),
        10: ("No plants; warehouse 480 km away.", None, None, 1, None, None),
        11: ("Multiple suppliers act as backup.", 1, None, 3, None, None),
        12: ("Fixed for the validity period; USD/INR risk with buyer.", None, None, 2, None, None),
        13: ("30% advance, balance against documents.", None, None, 1, None, None),
        14: ("Replacement subject to supplier acceptance.", None, None, 1, None, None),
    },
    "V5": {
        1: ("Yes, as per the FY25 vendor file.", 1, None, None, "pass", "certificate on file in buyer vendor master, not attached"),
        2: ("Yes, as per the FY25 vendor file.", 1, None, None, "pass", "on file in buyer vendor master, not attached"),
        3: ("Yes, as per the FY25 vendor file.", 1, None, None, "pass", "on file in buyer vendor master, not attached"),
        4: ("As on file.", 1, None, 3, None, None),
        5: ("As on file.", 1, None, 3, None, None),
        6: ("900 t/month, 68% utilised.", None, None, 3, None, None),
        7: ("Same arrangement as last year.", None, None, 3, None, None),
        8: ("As last year.", None, None, 3, None, None),
        9: ("As last year.", None, None, 3, None, None),
        10: ("120 km from your plants.", None, None, 4, None, None),
        11: ("As last year.", None, None, 2, None, None),
        12: ("As last year.", None, None, 2, None, None),
        13: ("As last year.", None, None, 3, None, None),
        14: ("As last year.", None, None, 3, None, None),
    },
}


# Expected pipeline reading of each vendor's questionnaire, from what the response DOCUMENTS and the attachment files actually contain:
# q_no -> (state, gate_status, stance, expiry_date, evidence_source). Stance and expiry are graded on questions 1-5 only.
# claimed_unsupported = the vendor claims it holds something and no valid attachment supports it (missing, expired, or issued to someone else).
_DESCRIPTIVE = ("extracted", None, None, None, "response")
QTRUTH = {
    "V1": {1: ("extracted", "pass", "yes", "2026-11-30", "attachment"), 2: ("extracted", "pass", "yes", "2026-12-15", "attachment"),
           3: ("extracted", "pass", "yes", "2027-01-31", "attachment"), 4: ("extracted", None, "yes", "2026-12-31", "attachment"),
           5: ("extracted", None, "yes", "2027-06-30", "attachment"), **{n: _DESCRIPTIVE for n in range(6, 15)}},
    "V2": {1: ("extracted", "pass", "yes", "2027-02-28", "attachment"), 2: ("extracted", "pass", "yes", "2026-10-10", "attachment"),
           3: ("extracted", "pass", "yes", "2026-09-30", "attachment"), 4: ("claimed_unsupported", None, "yes", None, "response"),
           5: ("claimed_unsupported", None, "yes", None, "response"), **{n: _DESCRIPTIVE for n in range(6, 15)}},
    "V3": {1: ("extracted", "fail", "partial", None, "response"), 2: ("extracted", "fail", "no", None, "response"),
           3: ("claimed_unsupported", "fail", "yes", None, "response"), 4: ("extracted", None, "partial", None, "response"),
           5: ("claimed_unsupported", None, "yes", "2025-09-14", "attachment"), **{n: _DESCRIPTIVE for n in range(6, 15)}},
    # V5's email carries no questionnaire; its gates clear only on the buyer's vendor-master record (dataset/artifacts/vendor_master.json)
    "V5": {**{n: ("missing", "pass", None, None, "vendor_master") for n in (1, 2, 3)}, **{n: ("missing", None, None, None, None) for n in range(4, 15)}},
}

