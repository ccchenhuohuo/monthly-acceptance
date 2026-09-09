WITH
-- 仅在已定位 SPU 价格问题后用 FILTER 限定商品，查看相同规格连续月份。
-- 金额/跳变筛选在 LAG 之后；不跨缺失自然月比较。保留原始异常值，不将跳价单独判错。
variants AS (
  SELECT platform, site, product_id, sku_id, month_dt, COUNT(*) AS source_rows,
         SUM(discount_sales) AS amount, SUM(`count`) AS units,
         SUM(CASE WHEN discount_sales IS NULL OR `count` IS NULL OR discount_sales < 0 OR `count` < 0 THEN 1 ELSE 0 END) AS invalid_value_rows
  FROM {{TABLE}}
  WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}' AND ({{FILTER}})
  GROUP BY platform, site, product_id, sku_id, month_dt
), priced AS (
  SELECT *, CASE WHEN units > 0 AND amount > 0 AND invalid_value_rows = 0 THEN amount / units END AS price FROM variants
), previous AS (
  SELECT *, LAG(month_dt) OVER (PARTITION BY platform, site, product_id, sku_id ORDER BY month_dt) AS previous_month, LAG(price) OVER (PARTITION BY platform, site, product_id, sku_id ORDER BY month_dt) AS previous_price,
         LAG(amount) OVER (PARTITION BY platform, site, product_id, sku_id ORDER BY month_dt) AS previous_amount
  FROM priced
)
SELECT *, price / NULLIF(previous_price, 0) AS price_ratio, COUNT(*) OVER () AS total_rows
FROM previous
WHERE DATEDIFF(CAST(month_dt AS DATE), CAST(previous_month AS DATE)) BETWEEN 28 AND 31
  AND (amount >= {{MIN_AMOUNT}} OR previous_amount >= {{MIN_AMOUNT}})
  AND (price / NULLIF(previous_price, 0) >= {{RATIO}} OR previous_price / NULLIF(price, 0) >= {{RATIO}})
ORDER BY GREATEST(amount, previous_amount) DESC, platform, product_id, sku_id, month_dt
LIMIT {{PAGE_SIZE}} OFFSET {{OFFSET}}
