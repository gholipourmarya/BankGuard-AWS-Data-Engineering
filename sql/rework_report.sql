-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- What to send back to which source system: one row per system and rejection reason.
-- A record with several reasons appears once per reason. Verified on Athena on 2026-10-28; see docs/evidence/.
-- Match Python str.strip(), including Unicode whitespace, and missing labels.
WITH normalized AS (
    SELECT *, COALESCE(NULLIF(regexp_replace(source_system,
        '^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+$', ''), ''),
        '(missing)') AS source_label
    FROM rejected_transactions
)
SELECT
    source_label AS source_system,
    reason,
    COUNT(*) AS rejected_records,
    array_join(slice(array_distinct(array_agg(transaction_id)), 1, 5), ' ') AS example_transaction_ids
FROM normalized
CROSS JOIN UNNEST(error_reasons) AS u (reason)
GROUP BY source_label, reason
ORDER BY source_label, rejected_records DESC;
