-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- What to send back to which source system: one row per system and rejection reason.
-- A record with several reasons appears once per reason. Verified on Athena on 2026-10-28; see docs/evidence/.
SELECT
    source_system,
    reason,
    COUNT(*) AS rejected_records,
    array_join(slice(array_distinct(array_agg(transaction_id)), 1, 5), ' ') AS example_transaction_ids
FROM rejected_transactions
CROSS JOIN UNNEST(error_reasons) AS u (reason)
GROUP BY source_system, reason
ORDER BY source_system, rejected_records DESC;
