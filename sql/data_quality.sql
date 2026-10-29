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
-- Match Python str.strip(), including Unicode whitespace, and missing labels.
WITH normalized AS (
    SELECT COALESCE(NULLIF(regexp_replace(source_system,
        '^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+$', ''), ''),
        '(missing)') AS source_label
    FROM rejected_transactions
)
SELECT
    source_label AS source_system,
    COUNT(*) AS rejected_records
FROM normalized
GROUP BY source_label
ORDER BY rejected_records DESC;
