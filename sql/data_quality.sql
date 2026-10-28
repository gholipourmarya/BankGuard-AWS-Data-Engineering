-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- Rejected transactions by reason (one record can have several reasons)
SELECT
    reason,
    COUNT(*) AS rejected_records
FROM rejected_transactions
CROSS JOIN UNNEST(error_reasons) AS u (reason)
GROUP BY reason
ORDER BY rejected_records DESC;

-- Rejected transactions by originating system
SELECT
    source_system,
    COUNT(*) AS rejected_records
FROM rejected_transactions
GROUP BY source_system
ORDER BY rejected_records DESC;
