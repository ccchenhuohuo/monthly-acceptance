-- 同款跳价：同一 SKU 相邻两个月到手价涨到 3 倍以上或跌到 1/3 以下，按“价差 × 当月件数”排前 {{N}} 条。
-- 先取前一个月的价格再过滤（不能先按金额筛掉再 LAG，否则大跳价会漏掉）。恰好 10 倍优先怀疑位数错误。
SELECT site, platform, product_id, sku_id, m, prev_m, l1, l2, l3, brand, title,
       prev_price, price, units, price / prev_price AS ratio, (price - prev_price) * units AS impact
FROM (
    SELECT w.*, ROW_NUMBER() OVER (PARTITION BY site, platform ORDER BY ABS((price - prev_price) * units) DESC) AS rk
    FROM (
        SELECT site, platform, product_id, sku_id, DATE_FORMAT(month_dt, '%Y-%m') AS m,
               YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
               std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, std_brand_name AS brand,
               COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), '')) AS title,
               discount_price AS price, `count` AS units,
               LAG(discount_price) OVER (PARTITION BY site, platform, product_id, sku_id ORDER BY month_dt) AS prev_price,
               LAG(YEAR(month_dt) * 12 + MONTH(month_dt)) OVER (PARTITION BY site, platform, product_id, sku_id ORDER BY month_dt) AS prev_mi,
               LAG(DATE_FORMAT(month_dt, '%Y-%m')) OVER (PARTITION BY site, platform, product_id, sku_id ORDER BY month_dt) AS prev_m
        FROM {{T}}
        WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
          AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
    ) w
    WHERE prev_mi = mi - 1 AND prev_price > 0 AND (price >= 3 * prev_price OR price * 3 <= prev_price)
) r
WHERE rk <= {{N}}
