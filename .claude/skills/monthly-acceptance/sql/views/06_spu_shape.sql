-- 头部与长尾：每个国家×平台×月，SPU 按件数排名后的 TOP100/TOP1000 件数，以及 ≤5 件、6–20 件档的 SPU 数与件数、件数中位数。
-- scope=all 是全部采集（看服务商覆盖），scope=focus 是关注口径（看我们用的数）。
SELECT site, platform, scope, m,
       COUNT(1) AS spus, SUM(units) AS units, SUM(amount) AS amount,
       SUM(CASE WHEN rk <= 100 THEN units ELSE 0 END) AS top100_units,
       SUM(CASE WHEN rk <= 1000 THEN units ELSE 0 END) AS top1000_units,
       SUM(CASE WHEN rk <= 1000 THEN amount ELSE 0 END) AS top1000_amount,
       SUM(CASE WHEN units <= 5 THEN 1 ELSE 0 END) AS le5_spus,
       SUM(CASE WHEN units <= 5 THEN units ELSE 0 END) AS le5_units,
       SUM(CASE WHEN units BETWEEN 6 AND 20 THEN 1 ELSE 0 END) AS le20_spus,
       PERCENTILE_APPROX(units, 0.5) AS p50_units,
       PERCENTILE_APPROX(units, 0.9) AS p90_units
FROM (
    SELECT site, platform, scope, m, product_id, units, amount,
           ROW_NUMBER() OVER (PARTITION BY site, platform, scope, m ORDER BY units DESC, product_id) AS rk
    FROM (
        SELECT site, platform, 'all' AS scope, DATE_FORMAT(month_dt, '%Y-%m') AS m, product_id,
               SUM(`count`) AS units, SUM(discount_sales) AS amount
        FROM {{T}}
        WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
        GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), product_id
        UNION ALL
        SELECT site, platform, 'focus' AS scope, DATE_FORMAT(month_dt, '%Y-%m') AS m, product_id,
               SUM(`count`) AS units, SUM(discount_sales) AS amount
        FROM {{T}}
        WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
          AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
        GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), product_id
    ) a
) r
GROUP BY site, platform, scope, m
