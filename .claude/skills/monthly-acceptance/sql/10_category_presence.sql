WITH
-- PATH_EXPR 是同一观察轴的完整路径；EXPECTED_PATHS_SQL 返回 category_path, active_from, active_to。
-- 有效预期可多段，不能从已出现集合反推；MONTH_COUNT = 首尾自然月差 + 1。
expected AS ({{EXPECTED_PATHS_SQL}}),
observed AS (
  SELECT CAST({{PATH_EXPR}} AS STRING) AS category_path, month_dt,
         COUNT(*) AS source_rows, COUNT(DISTINCT NULLIF(TRIM(product_id), '')) AS spu_count,
         SUM(discount_sales) AS sales_amount
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY category_path, month_dt
), paths AS (
  SELECT category_path FROM observed UNION SELECT category_path FROM expected
), months AS (
  SELECT DATE_FORMAT(DATE_ADD(CAST('{{START}}' AS DATE), INTERVAL number MONTH), '%Y-%m-01') AS month_dt
  FROM numbers("number"="{{MONTH_COUNT}}")
), bounds AS (
  SELECT category_path, MIN(month_dt) AS first_seen, MAX(month_dt) AS last_seen FROM observed GROUP BY category_path
), grid AS (
  SELECT p.category_path, m.month_dt,
         MAX(CASE WHEN m.month_dt BETWEEN e.active_from AND e.active_to THEN 1 ELSE 0 END) AS is_expected
  FROM paths p CROSS JOIN months m LEFT JOIN expected e ON p.category_path <=> e.category_path
  GROUP BY p.category_path, m.month_dt
)
SELECT g.*, o.source_rows, o.spu_count, o.sales_amount, b.first_seen, b.last_seen,
       CASE WHEN o.source_rows IS NOT NULL AND g.month_dt = b.first_seen THEN 'first_observed'
            WHEN o.source_rows IS NOT NULL THEN 'observed'
            WHEN b.first_seen IS NULL AND g.is_expected = 1 THEN 'expected_never_observed'
            WHEN b.first_seen IS NULL THEN 'not_observed_outside_expected'
            WHEN g.month_dt < b.first_seen THEN 'before_first'
            WHEN g.month_dt > b.last_seen THEN 'terminal_missing'
            ELSE 'middle_gap' END AS presence_status,
       COUNT(*) OVER () AS total_rows
FROM grid g LEFT JOIN observed o ON g.category_path <=> o.category_path AND g.month_dt = o.month_dt
LEFT JOIN bounds b ON g.category_path <=> b.category_path
ORDER BY g.category_path, g.month_dt LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
