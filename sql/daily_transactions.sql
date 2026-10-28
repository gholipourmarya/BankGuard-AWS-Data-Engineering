-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- Daily number and volume of valid transactions, per currency
SELECT
    transaction_date,
    currency,
    COUNT(*)    AS transaction_count,
    SUM(amount) AS total_amount
FROM curated_transactions
GROUP BY transaction_date, currency
ORDER BY transaction_date, currency;
