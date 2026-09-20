PRAGMA foreign_keys = ON;

-- Columns marked (truth) are filled by generate_dataset.py only; the extraction pipeline leaves them NULL.
CREATE TABLE rfx_lines (
  line_no          INTEGER PRIMARY KEY,
  sku              TEXT NOT NULL,
  style            TEXT NOT NULL DEFAULT 'RSC',
  length_mm        INTEGER NOT NULL,
  width_mm         INTEGER NOT NULL,
  height_mm        INTEGER NOT NULL,
  ply              INTEGER NOT NULL CHECK (ply IN (3,5,7)),
  flute            TEXT NOT NULL,
  liner_gsm        INTEGER NOT NULL,
  gsm_stack        TEXT NOT NULL,
  bf               INTEGER NOT NULL,
  print_spec       TEXT NOT NULL,
  annual_qty       INTEGER NOT NULL,
  uom              TEXT NOT NULL DEFAULT 'piece',
  blank_area_m2    REAL,
  board_weight_kg  REAL,
  should_cost_inr_pc REAL
);

CREATE TABLE vendors (
  vendor_id        TEXT PRIMARY KEY,
  name             TEXT NOT NULL,
  archetype        TEXT,
  response_format  TEXT NOT NULL,
  pricing_posture  TEXT,
  quirk            TEXT,
  distance_km      INTEGER,
  freight_pct_truth REAL,      -- landed-cost model only; vendors quote "freight extra"
  moq_pcs          INTEGER,
  gates_passed_truth INTEGER   -- 1 if all three mandatory gates truly pass
);

CREATE TABLE submissions (
  submission_id    TEXT PRIMARY KEY,
  vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
  file_name        TEXT NOT NULL,       -- planned artifact name
  format           TEXT NOT NULL,
  received_on      TEXT NOT NULL,
  validity_days    INTEGER,
  valid_until      TEXT,
  expired_at_eval  INTEGER NOT NULL,
  eval_date        TEXT NOT NULL,
  incoterm         TEXT,
  currency         TEXT NOT NULL,
  fx_rate          REAL,
  fx_source        TEXT,
  fx_date          TEXT
);

CREATE TABLE bid_fields (
  field_id         INTEGER PRIMARY KEY,
  submission_id    TEXT NOT NULL REFERENCES submissions(submission_id),
  rfx_line_no      INTEGER NOT NULL REFERENCES rfx_lines(line_no),
  field_name       TEXT NOT NULL CHECK (field_name IN ('unit_price','line_total','declared_liner_gsm')),
  value            REAL,                -- as stated by the vendor; NULL when not_quoted / missing
  unit             TEXT,
  basis            TEXT,
  currency         TEXT,
  state            TEXT NOT NULL CHECK (state IN ('extracted','needs_review','not_quoted','missing')),
  reason_code      TEXT,
  anchor           TEXT,                -- planned locator the renderer must honour
  snippet          TEXT,                -- raw text as it will appear at the anchor
  derivation       TEXT,
  resolving_question TEXT,
  norm_inr_pc      REAL,                -- truth: unit_price rows only
  comparability    TEXT CHECK (comparability IN ('comparable','comparable_with_assumptions','not_comparable'))
);

CREATE TABLE tier_rules (
  rule_id          INTEGER PRIMARY KEY,
  vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
  kind             TEXT NOT NULL CHECK (kind IN ('order_value_uplift','order_value_discount','line_qty_break')),
  min_value_inr    REAL,
  max_value_inr    REAL,
  rfx_line_no      INTEGER,
  min_qty          INTEGER,
  effect_pct       REAL NOT NULL,       -- +ve raises price, -ve lowers it
  anchor           TEXT,
  snippet          TEXT
);

CREATE TABLE conditions (
  condition_id     INTEGER PRIMARY KEY,
  submission_id    TEXT NOT NULL REFERENCES submissions(submission_id),
  rfx_line_no      INTEGER,
  kind             TEXT NOT NULL,
  value_num        REAL,
  value_text       TEXT,
  anchor           TEXT,
  snippet          TEXT
);

CREATE TABLE attachments (
  attachment_id    TEXT PRIMARY KEY,
  vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
  kind             TEXT NOT NULL,
  file_name        TEXT NOT NULL,
  issuer           TEXT,
  entity_name      TEXT,
  valid_to         TEXT,
  expired_at_eval  INTEGER NOT NULL,
  supports         TEXT,               -- questionnaire q_no it is offered against
  note             TEXT
);

CREATE TABLE questionnaire_answers (
  answer_id        INTEGER PRIMARY KEY,
  vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
  q_no             INTEGER NOT NULL,
  question         TEXT NOT NULL,
  is_gate          INTEGER NOT NULL,
  gate_code        TEXT,
  answer_text      TEXT NOT NULL,
  answer_bool      INTEGER,
  attachment_id    TEXT REFERENCES attachments(attachment_id),
  max_score        INTEGER,            -- scored questions only
  truth_score      INTEGER,
  truth_gate_status TEXT CHECK (truth_gate_status IN ('pass','fail')),
  truth_reason     TEXT,
  -- Pipeline columns (additive). In truth.sqlite they hold the EXPECTED values the pipeline is graded against.
  state            TEXT CHECK (state IN ('extracted','needs_review','not_quoted','missing','claimed_unsupported')),   -- five-state model; claimed_unsupported lives here, never on bid_fields
  gate_status      TEXT CHECK (gate_status IN ('pass','fail','not_answered')),                                       -- gate rows only
  reason_code      TEXT,
  stance           TEXT,               -- yes | no | partial | unclear | not_applicable
  anchor           TEXT,
  snippet          TEXT,
  expiry_date      TEXT,               -- ISO date the certificate mentioned expires (attachment date preferred over the vendor's statement)
  expiry_source    TEXT,               -- answer | attachment
  evidence_source  TEXT,               -- attachment | response | vendor_master
  resolving_question TEXT,
  UNIQUE (vendor_id, q_no)
);
