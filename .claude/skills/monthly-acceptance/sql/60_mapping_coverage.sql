WITH
-- 按原始完整键先聚合再连接，避免映射重复放大源层量额；同时核对量额总和及完整键内差异。FILTER 作用于原始采集范围。
-- DIM_FILTER 为 std 观察范围；不要把同父 SPU 的不同子体跨类目误判为同原始键重复。
raw_keys AS (
  SELECT month_dt, platform, site, product_id, sku_id, COUNT(*) AS raw_rows,
         SUM(discount_sales) AS raw_amount, SUM(`count`) AS raw_units,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL THEN 1 ELSE 0 END) AS missing_units_rows
  FROM internal.flywheel.monthly_sales_wide_new
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY month_dt, platform, site, product_id, sku_id
), dim_keys AS (
  SELECT month_dt, platform, site, product_id, sku_id, COUNT(*) AS dim_rows,
         SUM(discount_sales) AS dim_amount, SUM(`count`) AS dim_units,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS dim_missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL THEN 1 ELSE 0 END) AS dim_missing_units_rows
  FROM internal.flywheel.monthly_sales_with_dim
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{DIM_FILTER}})
  GROUP BY month_dt, platform, site, product_id, sku_id
)
SELECT r.month_dt, SUM(r.raw_rows) AS source_rows, COUNT(*) AS source_keys,
       COUNT(DISTINCT NULLIF(TRIM(r.product_id), '')) AS source_spus,
       SUM(r.raw_amount) AS source_amount,
       SUM(CASE WHEN d.dim_rows IS NOT NULL THEN r.raw_amount END) AS matched_source_amount,
       SUM(d.dim_amount) AS matched_dim_amount,
       SUM(r.raw_units) AS source_units,
       SUM(CASE WHEN d.dim_rows IS NOT NULL THEN r.raw_units END) AS matched_source_units,
       SUM(d.dim_units) AS matched_dim_units,
       SUM(CASE WHEN d.dim_rows IS NOT NULL AND NOT (r.raw_amount <=> d.dim_amount) THEN 1 ELSE 0 END) AS amount_unequal_keys,
       SUM(CASE WHEN d.dim_rows IS NOT NULL AND NOT (r.raw_units <=> d.dim_units) THEN 1 ELSE 0 END) AS units_unequal_keys,
       COUNT(DISTINCT CASE WHEN d.dim_rows IS NOT NULL THEN NULLIF(TRIM(r.product_id), '') END) AS matched_spus,
       SUM(CASE WHEN d.dim_rows IS NULL THEN 1 ELSE 0 END) AS unmapped_keys,
       SUM(CASE WHEN r.raw_rows > 1 THEN 1 ELSE 0 END) AS repeated_source_keys,
       SUM(CASE WHEN d.dim_rows > 1 THEN 1 ELSE 0 END) AS repeated_dim_keys,
       SUM(r.missing_amount_rows) AS missing_amount_rows,
       SUM(r.missing_units_rows) AS missing_units_rows,
       SUM(d.dim_missing_amount_rows) AS matched_dim_missing_amount_rows,
       SUM(d.dim_missing_units_rows) AS matched_dim_missing_units_rows
FROM raw_keys r LEFT JOIN dim_keys d ON r.month_dt = d.month_dt AND r.platform <=> d.platform
  AND r.site = d.site AND r.product_id <=> d.product_id AND r.sku_id <=> d.sku_id
GROUP BY r.month_dt ORDER BY r.month_dt
