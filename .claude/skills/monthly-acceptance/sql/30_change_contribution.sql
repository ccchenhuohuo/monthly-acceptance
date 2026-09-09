WITH
-- 固定同国父类目 FILTER；GROUP_EXPR 为互斥子集（细分类目/品牌/SPU），不得混合层级。
-- 分别按 ABS(amount_change) DESC 和 ABS(units_change) DESC 看两条候选线，不混成一个分数。
-- CANDIDATE_FILTER 仅在分母算完后筛选。净变化很小时不按净贡献率排序；缺观测≠真实零销售。
grouped AS (
  SELECT CAST({{GROUP_EXPR}} AS STRING) AS part, month_dt,
         SUM(discount_sales) AS amount, SUM(`count`) AS units, COUNT(*) AS source_rows,
         COUNT(DISTINCT NULLIF(TRIM(product_id), '')) AS spu_count,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL THEN 1 ELSE 0 END) AS missing_units_rows
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt IN ('{{BASE}}', '{{CURRENT}}') AND ({{FILTER}})
  GROUP BY part, month_dt
), parts AS (
  SELECT part,
         SUM(CASE WHEN month_dt = '{{BASE}}' THEN COALESCE(amount, 0) ELSE 0 END) AS amount_before,
         SUM(CASE WHEN month_dt = '{{CURRENT}}' THEN COALESCE(amount, 0) ELSE 0 END) AS amount_after,
         SUM(CASE WHEN month_dt = '{{BASE}}' THEN source_rows ELSE 0 END) AS rows_before,
         SUM(CASE WHEN month_dt = '{{CURRENT}}' THEN source_rows ELSE 0 END) AS rows_after,
         SUM(CASE WHEN month_dt = '{{BASE}}' THEN spu_count ELSE 0 END) AS spu_before,
         SUM(CASE WHEN month_dt = '{{CURRENT}}' THEN spu_count ELSE 0 END) AS spu_after,
         SUM(CASE WHEN month_dt = '{{BASE}}' THEN COALESCE(units, 0) ELSE 0 END) AS units_before,
         SUM(CASE WHEN month_dt = '{{CURRENT}}' THEN COALESCE(units, 0) ELSE 0 END) AS units_after,
         SUM(missing_amount_rows) AS missing_amount_rows, SUM(missing_units_rows) AS missing_units_rows
  FROM grouped GROUP BY part
), changes AS (
  SELECT *, amount_after - amount_before AS amount_change, units_after - units_before AS units_change FROM parts
), totals AS (
  SELECT *, SUM(amount_before) OVER () AS category_before, SUM(amount_after) OVER () AS category_after,
         SUM(amount_change) OVER () AS category_change, SUM(ABS(amount_change)) OVER () AS gross_movement,
         SUM(units_before) OVER () AS category_units_before, SUM(units_after) OVER () AS category_units_after,
         SUM(units_change) OVER () AS category_units_change, SUM(ABS(units_change)) OVER () AS gross_units_movement,
         SUM(rows_before) OVER () AS category_rows_before, SUM(rows_after) OVER () AS category_rows_after,
         SUM(missing_amount_rows) OVER () AS category_missing_amount_rows,
         SUM(missing_units_rows) OVER () AS category_missing_units_rows, COUNT(*) OVER () AS all_parts
  FROM changes
), contributions AS (
  SELECT *, amount_change / NULLIF(category_change, 0) AS net_change_contribution,
         100.0 * amount_change / NULLIF(category_before, 0) AS growth_pp,
         ABS(amount_change) / NULLIF(gross_movement, 0) AS absolute_movement_share,
         units_change / NULLIF(category_units_change, 0) AS units_net_contribution,
         100.0 * units_change / NULLIF(category_units_before, 0) AS units_growth_pp,
         ABS(units_change) / NULLIF(gross_units_movement, 0) AS units_absolute_movement_share,
         amount_before / NULLIF(category_before, 0) AS share_before,
         amount_after / NULLIF(category_after, 0) AS share_after
  FROM totals
)
SELECT *, COUNT(*) OVER () AS selected_parts FROM contributions
WHERE ({{CANDIDATE_FILTER}})
ORDER BY {{ORDER_EXPR}}, part LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
