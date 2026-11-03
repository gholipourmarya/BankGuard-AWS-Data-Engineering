-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- Transaction volume by customer country, per currency
SELECT
    c.country,
    t.currency,
    COUNT(*)      AS transaction_count,
    SUM(t.amount) AS total_amount
FROM curated_transactions t
JOIN curated_accounts  a ON a.account_id  = t.account_id
JOIN curated_customers c ON c.customer_id = a.customer_id
GROUP BY c.country, t.currency
ORDER BY c.country, t.currency;
