-- 跳价计数：每个国家×平台×月的跳价 SKU 数、其中约 10 倍 / 约 1/10 的个数、隐含金额影响合计。
SELECT site, platform, m, COUNT(1) AS jumps,
       SUM(CASE WHEN price / prev_price BETWEEN 9.5 AND 10.5 OR price / prev_price BETWEEN 0.095 AND 0.105 THEN 1 ELSE 0 END) AS tenfold,
       SUM(CASE WHEN price / prev_price >= 50 OR price / prev_price <= 0.02 THEN 1 ELSE 0 END) AS extreme,
       SUM((price - prev_price) * units) AS impact,
       SUM(ABS((price - prev_price) * units)) AS abs_impact
FROM (
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
           discount_price AS price, `count` AS units,
           LAG(discount_price) OVER (PARTITION BY site, platform, product_id, sku_id ORDER BY month_dt) AS prev_price,
           LAG(YEAR(month_dt) * 12 + MONTH(month_dt)) OVER (PARTITION BY site, platform, product_id, sku_id ORDER BY month_dt) AS prev_mi
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
) x
WHERE prev_mi = mi - 1 AND prev_price > 0 AND (price >= 3 * prev_price OR price * 3 <= prev_price)
GROUP BY site, platform, m
