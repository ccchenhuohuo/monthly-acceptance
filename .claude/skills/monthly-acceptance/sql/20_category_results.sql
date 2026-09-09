WITH
-- 同国、同一类目层级：完整历史结果。先汇总 SPU，再算分位数；缺失价格不丢商品/金额。
spu AS (
  SELECT month_dt, CAST({{PATH_EXPR}} AS STRING) AS category_path,
         NULLIF(TRIM(product_id), '') AS spu_id, SUM(discount_sales) AS amount, SUM(`count`) AS units,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL OR `count` < 0 OR discount_sales < 0 THEN 1 ELSE 0 END) AS invalid_value_rows,
         COUNT(*) AS source_rows
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY month_dt, category_path, spu_id
), priced AS (
  SELECT *, CASE WHEN units > 0 AND amount > 0 AND missing_amount_rows = 0 AND invalid_value_rows = 0
                AND spu_id IS NOT NULL THEN amount / units END AS price FROM spu
), results AS (
  SELECT month_dt, category_path, COUNT(spu_id) AS spu_count, SUM(amount) AS sales_amount, SUM(units) AS units,
         COUNT(price) AS priced_spu_count, PERCENTILE_APPROX(price, 0.1) AS price_p10,
         PERCENTILE_APPROX(price, 0.5) AS price_median, PERCENTILE_APPROX(price, 0.9) AS price_p90,
         SUM(missing_amount_rows) AS missing_amount_rows, SUM(invalid_value_rows) AS invalid_value_rows,
         SUM(CASE WHEN spu_id IS NULL THEN source_rows ELSE 0 END) AS invalid_id_rows
  FROM priced GROUP BY month_dt, category_path
), previous AS (
  SELECT *, LAG(month_dt) OVER (PARTITION BY category_path ORDER BY month_dt) AS previous_month,
         LAG(sales_amount) OVER (PARTITION BY category_path ORDER BY month_dt) AS previous_amount,
         LAG(units) OVER (PARTITION BY category_path ORDER BY month_dt) AS previous_units,
         LAG(spu_count) OVER (PARTITION BY category_path ORDER BY month_dt) AS previous_spu_count
  FROM results
)
SELECT *, CASE WHEN DATEDIFF(CAST(month_dt AS DATE), CAST(previous_month AS DATE)) BETWEEN 28 AND 31
                    THEN sales_amount - previous_amount END AS mom_amount_change,
       CASE WHEN DATEDIFF(CAST(month_dt AS DATE), CAST(previous_month AS DATE)) BETWEEN 28 AND 31
                    THEN spu_count - previous_spu_count END AS mom_spu_change,
       CASE WHEN DATEDIFF(CAST(month_dt AS DATE), CAST(previous_month AS DATE)) BETWEEN 28 AND 31
                    THEN units - previous_units END AS mom_units_change,
       COUNT(*) OVER () AS total_rows
FROM previous ORDER BY category_path, month_dt LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
