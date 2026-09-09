WITH
-- BAND_EXPR 使用 price；跨比较期固定边界（本币/同类目）。NULL price 必须有独立桶。
-- 先 SPU 聚合再分带；列出各带商品数与金额组成。未知价格/无效 ID 不无声丢弃；未命中 BAND_EXPR 的有效价格列 unassigned_band 待修边界。
spu AS (
  SELECT month_dt, NULLIF(TRIM(product_id), '') AS spu_id,
         SUM(discount_sales) AS amount, SUM(`count`) AS units,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL OR `count` < 0 OR discount_sales < 0 THEN 1 ELSE 0 END) AS invalid_value_rows
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY month_dt, spu_id
), priced AS (
  SELECT *, CASE WHEN units > 0 AND amount > 0 AND missing_amount_rows = 0 AND invalid_value_rows = 0
                 AND spu_id IS NOT NULL THEN amount / units END AS price FROM spu
), banded AS (
  SELECT *, CASE WHEN spu_id IS NULL THEN 'invalid_product_id'
                 WHEN price IS NULL THEN 'unknown_price' ELSE COALESCE(CAST({{BAND_EXPR}} AS STRING), 'unassigned_band') END AS band FROM priced
), totals AS (
  SELECT month_dt, COUNT(spu_id) AS category_spu_count, SUM(amount) AS category_amount,
         SUM(missing_amount_rows) AS missing_amount_rows, SUM(invalid_value_rows) AS invalid_value_rows
  FROM banded GROUP BY month_dt
), bands AS (SELECT DISTINCT band FROM banded), cells AS (
  SELECT month_dt, band, COUNT(spu_id) AS spu_count, SUM(amount) AS sales_amount, COUNT(*) AS observed_groups
  FROM banded GROUP BY month_dt, band
)
SELECT t.*, b.band, COALESCE(c.spu_count, 0) AS spu_count,
       CASE WHEN c.observed_groups IS NULL THEN 0 ELSE c.sales_amount END AS sales_amount,
       CAST(COALESCE(c.spu_count, 0) AS DOUBLE) / NULLIF(t.category_spu_count, 0) AS spu_share,
       CASE WHEN c.observed_groups IS NULL THEN 0 ELSE c.sales_amount END / NULLIF(t.category_amount, 0) AS amount_share,
       COUNT(*) OVER () AS total_rows
FROM totals t CROSS JOIN bands b LEFT JOIN cells c ON t.month_dt = c.month_dt AND b.band = c.band
ORDER BY t.month_dt, b.band LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
