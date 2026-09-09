WITH
-- 类目内按 SPU 销售额排序；两期均取，结合 30 的变化贡献选择主要集合。无固定 Top N。
-- 累计份额只有在金额完整且非负时可直接解释；负值/缺失先核查。CANDIDATE_FILTER 在总额与排名之后。
spu AS (
  SELECT month_dt, NULLIF(TRIM(product_id), '') AS spu_id,
         SUM(discount_sales) AS sales_amount, SUM(`count`) AS units, COUNT(*) AS source_rows,
         SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows,
         SUM(CASE WHEN `count` IS NULL OR `count` < 0 OR discount_sales < 0 THEN 1 ELSE 0 END) AS invalid_value_rows
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY month_dt, spu_id
), ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY month_dt ORDER BY sales_amount DESC, spu_id) AS amount_rank,
         SUM(sales_amount) OVER (PARTITION BY month_dt) AS category_amount,
         COUNT(spu_id) OVER (PARTITION BY month_dt) AS category_spu_count,
         SUM(sales_amount) OVER (PARTITION BY month_dt ORDER BY sales_amount DESC, spu_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_amount
  FROM spu
), shares AS (
  SELECT *, sales_amount / NULLIF(category_amount, 0) AS amount_share,
         cumulative_amount / NULLIF(category_amount, 0) AS cumulative_share,
         CASE WHEN units > 0 AND sales_amount > 0 AND missing_amount_rows = 0 AND invalid_value_rows = 0
                   AND spu_id IS NOT NULL THEN sales_amount / units END AS price
  FROM ranked
)
SELECT *, COUNT(*) OVER () AS selected_rows FROM shares WHERE ({{CANDIDATE_FILTER}})
ORDER BY month_dt, amount_rank LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
