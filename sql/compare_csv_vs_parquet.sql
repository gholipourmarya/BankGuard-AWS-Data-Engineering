-- Select the snapshot database returned by scripts/current_snapshot.py in the Athena editor.
-- Same accepted records, exported by Glue as CSV and partitioned Parquet.
-- Never upload raw transactions to comparison/csv/.
-- Compare "Data scanned" of the two queries (Athena console, or scripts/athena_scan_compare.py).

-- 1) CSV: Athena has to read every byte of the file
SELECT currency, COUNT(*) AS transaction_count, SUM(TRY_CAST(amount AS DECIMAL(18, 2))) AS total_amount
FROM comparison_transactions_csv
WHERE transaction_date = '2026-03-04'
GROUP BY currency;

-- 2) Parquet: only the needed columns and only the matching date partition are read
SELECT currency, COUNT(*) AS transaction_count, SUM(amount) AS total_amount
FROM curated_transactions
WHERE transaction_date = '2026-03-04'
GROUP BY currency;
