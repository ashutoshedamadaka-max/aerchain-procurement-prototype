# Procurement UI

Run from the repository root:

    pip install streamlit
    streamlit run app/main.py

The default source is procurement.db at the repository root. Set PROCUREMENT_DB
to select another pipeline database. The app opens SQLite read-only, supports empty
tables created from scripts/schema.sql, and never initializes or populates data.
Do not point it at the frozen truth database.

The four pages are Event, Comparison, Ask and RFx. Comparison displays raw rates
with their original basis/currency, all four recorded states, and vendor/line
dropdowns for verbatim source evidence. The latest submission per vendor is shown consistently in the grid and evidence panel.
Ask is a disabled integration placeholder; no analysis or extraction is implemented.

Generator dependencies: openpyxl reportlab python-docx.
Conversion and shell test dependencies: pytest streamlit.
Run: python -m pytest tests/test_conversions.py app/test_shell.py

## Integration notes

Generate the four responses with: python scripts/generate_vendor_docs.py

The renderer reads dataset/truth/truth.sqlite read-only and checks outcomes.json.
It writes exactly V1_response.xlsx, V2_response.pdf, V3_response.docx and
V5_response.txt under dataset/artifacts. These requested filenames differ from the
planned filenames in submissions. V4 is untouched.

V1 keeps Quotation!H10:H39 and K10:K39; V2 keeps quote rows on the planned
three pages, with the questionnaire and 7pt discount footnote on page 2.
V3 preserves commercial paragraph anchors; para29 names only the first declined
size, with the remaining two absent, as requested. Its shared truth regret snippet
therefore intentionally differs from the rendered prose.

Truth overrides the earlier brief: V1 has four award-value slabs (base plus three
uplifts); V2 has 30-day net payment, with no 15-day settlement requirement recorded.
The stored incorrect line totals on V1 line 12 and V2 line 20 remain incorrect.

Conversion results unpack as (value, derivation). Currency conversion results
unpack as (value, derivation, provenance), with source/date/rate in provenance.
The functions do not round intermediate results: the reference box weighs
0.902538 kg and costs 37.906596 per piece at 42/kg.
