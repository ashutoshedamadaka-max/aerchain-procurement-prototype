"""Read-only award-note adapter: presentation only; scenarios use the award engine."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from app import award_words, fmt

ROOT = Path(__file__).resolve().parents[1]


def cell(value: Any) -> str:
    """Escape database text for a Markdown table without rendering embedded HTML."""
    if value is None:
        return "Not recorded"
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\n', '<br>').replace('\r', '')


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows])


def money(value: float | None) -> str:
    return 'Not available' if value is None else f'INR {value:,.2f}'


def quote(text: str | None) -> str:
    """Preserve the verbatim snippet, even when it contains Markdown fences."""
    if text is None:
        return 'Not recorded'
    fence = '~' * max(3, max((len(x) for x in text.split() if set(x) == {'~'}), default=0) + 1)
    while fence in text:
        fence += '~'
    return f'{fence}text\n{text}\n{fence}'


def generate_award_note(db: Path, strategy: str = 'gated_split', basis: str = 'landed') -> tuple[str, list[str]]:
    """Render one consistent DB snapshot plus existing engine results; never write data.

    Missing data remains explicit in both the document and returned UI notices.
    Exceptions use repriced awarded-line share, matching the engine block rule.
    """
    from src import award, settings as engine_settings
    from src.extract.questionnaire import gate_results

    if strategy not in award_words.SCENARIOS or basis not in award.BASES:
        raise ValueError('Unsupported award scenario or cost basis')
    db = db.resolve()
    if (ROOT / 'dataset/truth').resolve() in db.parents:
        raise ValueError('Frozen truth is not an export source')
    notices: list[str] = []
    def missing(message: str) -> str:
        if message not in notices:
            notices.append(message)
        return 'Unavailable: ' + message

    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as con:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(name: str) -> list[dict]:
            if name not in tables:
                return []
            cur = con.execute('SELECT * FROM ' + name)
            return [dict(zip([d[0] for d in cur.description], r)) for r in cur]
        lines, vendors, submissions = rows('rfx_lines'), rows('vendors'), rows('submissions')
        norms, answers, assumptions = rows('norm_prices'), rows('questionnaire_answers'), rows('assumptions')
        fields, attachments = rows('bid_fields'), rows('attachments')
        settings = {r['assumption_id']: r for r in assumptions}
        threshold = engine_settings.get(con, 'review_block_threshold_pct') / 100
        threshold_source = 'settings.review_block_threshold_pct' if any(r['key'] == 'review_block_threshold_pct' for r in rows('settings')) else 'award engine default (no database override)'
        event = settings.get('event_id', {}).get('value_text')
        parts = ['# Award note', '## Header', f'Export date (UTC): {datetime.now(timezone.utc).isoformat(timespec="seconds")}',
                 'Event ID: ' + (cell(event) if event else missing('Event ID is not recorded in procurement.db.'))]
        complete_cost = bool(lines) and all(r.get('should_cost_inr_pc') is not None and r.get('annual_qty') is not None for r in lines)
        parts.append('Total value at should-cost: ' + (money(sum(r['annual_qty'] * r['should_cost_inr_pc'] for r in lines)) if complete_cost else missing('Should-cost values are absent or incomplete; no partial total is presented.')))
        result = None
        repriced = None
        gates, detail = gate_results(con)
        parts += ['## Recommendation', f'Scenario: {award_words.SCENARIOS[strategy]}; cost basis: {"ex-freight" if basis == "ex_freight" else "landed"}.']
        if norms and lines and submissions:
            ctx = award.load(con, basis=basis)
            result = award_words.run_scenario(award, ctx, strategy, gates)
            parts.append(award_words.markdown(award_words.build(con, strategy, basis)))
            # Always require evaluated gates, including the single-vendor view.
            parts += [f'Status: {result["status"]}; recommendable: {result["recommendable"]}.',
                      'Coverage: ' + cell(result.get('coverage')),
                      'MOQ policy: ' + cell(result.get('moq_policy', ctx.moq_policy))]
            if result.get('allocation'):
                allocation = {int(n): {v: ctx.lines[int(n)]} for n, v in result['allocation'].items()}
                repriced = award.reprice(ctx, allocation)
                parts.append(table(['Vendor', 'Awarded lines', 'Value share', 'Total after volume pricing (' + ('ex-freight' if basis == 'ex_freight' else 'landed') + ')'],
                    [[v, ', '.join(str(n) for n, av in result['allocation'].items() if av == v), f'{x["share"]:.2%}', money(x['total'])]
                     for v, x in result['per_vendor'].items()]))
                parts.append('Total landed cost: ' + (money(result['repriced_total']) if basis == 'landed' else missing('Scenario uses ex-freight prices; its total is not a landed cost.')))
                parts.append('Scenario total: ' + money(result['repriced_total']))
            else:
                parts.append(missing('No awarded allocation was produced; vendor allocation and total landed cost are unavailable.'))
            saving = result.get('saving')
            if saving:
                pass   # the plain-language summary above already states the result against single-sourcing
            else:
                parts.append(missing('No comparable full-coverage baseline/saving was returned by the award engine.'))
            for warning in [w for w in result.get('warnings', []) if w['code'] != 'saving_after_repricing']:   # the plain-language summary above says the same thing
                parts.append(f'> {cell(warning["severity"])} / {cell(warning["code"])}: {cell(warning["text"])}')
                if warning['severity'] == 'block':
                    notices.append(warning['text'])
        else:
            parts.append(missing('Normalized prices, RFx lines or submissions are missing; the award engine cannot run.'))

        attachment_index = {a['attachment_id']: a for a in attachments}
        parts.append('## Gates evaluated')
        if gates is None:
            parts.append(missing('Questionnaire gate results are not recorded; no vendor is assumed to pass.'))
        parts.append(table(['Vendor', 'Overall result', 'Gate', 'Result', 'Source', 'Reason / evidence'], [
            [v['vendor_id'], ('Not evaluated' if gates is None else 'pass' if v['vendor_id'] in gates else 'fail / incomplete'),
             a.get('gate_code'), a.get('gate_status'), (str(a.get('evidence_source') or 'Not recorded') + ' / ' + str(attachment_index.get(a.get('attachment_id'), {}).get('file_name') or a.get('anchor') or 'No document locator recorded')),
             ' / '.join(str(a.get(k) or 'Not recorded') for k in ('reason_code', 'anchor', 'snippet'))]
            for v in vendors for a in ([a for a in answers if a['vendor_id'] == v['vendor_id'] and a['is_gate']] or [{}])]))
        unsupported = [a for a in answers if a.get('state') == 'claimed_unsupported']
        parts.append('### Claimed unsupported')
        if unsupported:
            att = {a['attachment_id']: a for a in attachments}
            parts.append(table(['Vendor / question', 'Claim', 'Why unsupported', 'Attachment evidence', 'Resolving question'], [
                [f'{a["vendor_id"]} / {a["q_no"]}', a['answer_text'], a.get('reason_code'),
                 str({k: att.get(a.get('attachment_id'), {}).get(k) for k in ('file_name', 'entity_name', 'valid_to', 'note')}), a.get('resolving_question')]
                for a in unsupported]))
        else:
            parts.append('No claimed_unsupported items recorded.' if answers else missing('No questionnaire answers exist to assess unsupported claims.'))

        parts += ['## Assumptions in force', f'Review-line block threshold: {threshold:.2%} of the awarded value after volume pricing (strictly above blocks); source: {threshold_source}.']
        if assumptions:
            parts.append(table(['Assumption', 'Value', 'Unit', 'Date', 'Source', 'Note'], [
                [award_words.assumption_label(a['assumption_id']), a['value'] if a['value'] is not None else a['value_text'], a['unit'], a['as_of'], a['source'], a['note']] for a in assumptions]))
        else:
            parts.append(missing('No assumptions are recorded.'))
        for prefix, label in [('fx_', 'FX rate, date and source'), ('take_up_factor', 'Take-up factors')]:
            if not any(a['assumption_id'].startswith(prefix) for a in assumptions):
                parts.append(missing(label + ' are not recorded in assumptions.'))
        parts.append('### FX actually used in normalized prices')
        fx = sorted({(n.get('fx_rate'), n.get('fx_date'), n.get('fx_source')) for n in norms if n.get('fx_rate') is not None}, key=str)
        parts.append(table(['Rate', 'Date', 'Source'], [list(x) for x in fx]) if fx else 'No currency conversion recorded in normalized prices.')
        parts.append('### Freight per vendor')
        freight = []
        for v in vendors:
            vid = v['vendor_id']
            override = settings.get('freight_estimate_pct:' + vid, {})
            used = {tuple(n.get(k) for k in ('freight_status', 'freight_pct', 'freight_inr_per_piece', 'freight_source'))
                    for s in submissions if s['vendor_id'] == vid for n in norms if n['submission_id'] == s['submission_id']}
            freight.append([vid, override.get('value', 'No vendor override recorded'), '; '.join(str(x) for x in sorted(used, key=str)) or 'Not recorded'])
        if any(n.get('freight_status') == 'not_evaluated' for n in norms):
            parts.append(missing('Freight is not evaluated for some normalized prices; landed costs for those rows are unavailable.'))
        parts.append(table(['Vendor', 'Freight override (%)', 'Applied status / percent / INR per piece / source'], freight))

        parts.append('## Exceptions')
        parts.append('Scope: needs_review fields on allocated lines at or below the engine block threshold; share uses the line total after volume pricing.')
        if repriced and result and result['repriced_total']:
            selected = [r for r in result.get('needs_review_warnings', []) if not r['blocks_recommendation']]
            parts.append(table(['Vendor', 'Line', 'Field', 'Value', 'Award share', 'Reason', 'Resolving question'], [
                [r['vendor'], r['line'], r['field'], r['value'], f'{r["share_of_award"]:.2%}', r['reason_code'], r['resolving_question']]
                for r in selected]) if selected else 'No qualifying review fields on awarded lines.')
        else:
            parts.append(missing('No awarded value is available to classify review exceptions against the block threshold.'))

        parts.append('## Audit trail')
        if result and result.get('allocation'):
            by_field = {f['field_id']: f for f in fields}
            by_sub = {s['submission_id']: s for s in submissions}
            for ln, v in result['allocation'].items():
                matches = [n for n in norms if n['rfx_line_no'] == int(ln) and by_sub[n['submission_id']]['vendor_id'] == v]
                parts.append(f'### Line {ln} — {cell(v)}')
                if len(matches) != 1:
                    parts.append(missing(f'Line {ln}, {v}: source is ambiguous or absent ({len(matches)} normalization records).'))
                    continue
                n = matches[0]
                allocated = next(x for x in repriced['vendors'][v]['lines'] if x['line'] == int(ln))
                parts.append(f'Allocated quantity: {allocated["pieces"]}; unit price after volume pricing, before any order-value adjustment and freight: {money(allocated["net_inr_pc"])}/piece; line total after volume pricing: {money(allocated["total"])}.')
                f = by_field.get(n.get('field_id'), {})
                parts += [f'Normalized unit price: {money(n.get("inr_per_piece"))}/piece; landed: {money(n.get("landed_inr_per_piece"))}/piece.',
                          f'Vendor-stated price: {cell(f.get("value"))} {cell(f.get("currency"))}; basis: {cell(f.get("basis"))}.',
                          'File: ' + cell(by_sub[n['submission_id']]['file_name']) + '; anchor: ' + cell(f.get('anchor')),
                          'Verbatim snippet:', quote(f.get('snippet'))]
                if not f.get('anchor') or f.get('snippet') is None:
                    parts.append(missing(f'Line {ln}, {v}: source anchor or snippet is missing.'))
        else:
            parts.append(missing('No awarded lines exist for the audit trail.'))
        if notices:
            parts += ['## Data gaps and blockers', *['- ' + cell(n) for n in dict.fromkeys(notices)]]
        names = {v['vendor_id']: v['name'] for v in vendors}
        return award_words.named_outside_fences('\n\n'.join(parts) + '\n', names), [award_words.named(n, names) for n in dict.fromkeys(notices)]
