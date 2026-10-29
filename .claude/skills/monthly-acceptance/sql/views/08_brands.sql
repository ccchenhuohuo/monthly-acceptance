-- 品牌层：每个国家×平台×标准一级类目，近 13 个月件数前 25 的品牌，加上 OTHERS/其他 与本品，给出全部月份的件、额、行数。
-- l1_brands 是该一级类目当月的品牌总数（品牌数暴增常与幽灵商品同源）。OTHERS/其他 不是品牌，只看它的占比变化。
-- 份额须件数、金额双口径，且只在单站点内比较。品牌下的商品新增 / 留存 / 消失用 sql/drill/brand_churn.sql 拆。
SELECT site, platform, l1, brand, m, units, amount, nrows, l1_brands, rk
FROM (
    SELECT site, platform, l1, brand, m, units, amount, nrows, l1_brands,
           DENSE_RANK() OVER (PARTITION BY site, platform, l1 ORDER BY recent_units DESC, brand) AS rk
    FROM (
        SELECT site, platform, l1, brand, m, units, amount, nrows,
               COUNT(1) OVER (PARTITION BY site, platform, l1, m) AS l1_brands,
               SUM(CASE WHEN m >= '{{RECENT_FROM}}' THEN units ELSE 0 END) OVER (PARTITION BY site, platform, l1, brand) AS recent_units
        FROM (
            SELECT site, platform, std_category_1 AS l1, std_brand_name AS brand, DATE_FORMAT(month_dt, '%Y-%m') AS m,
                   SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS nrows
            FROM {{T}}
            WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
              AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
            GROUP BY site, platform, std_category_1, std_brand_name, DATE_FORMAT(month_dt, '%Y-%m')
        ) a
    ) b
) c
WHERE rk <= 25 OR brand = 'OTHERS/其他' OR brand IN ({{BRANDS}})
