-- 标准类目四项结果的底表：国家×平台×月×层级路径（lvl 0=关注口径合计，1/2/3=标准一二三级）。
-- SPU 数在每个层级各自去重：上级 SPU 数不能用子类目的数相加。
SELECT site, platform, m,
       CASE WHEN GROUPING(l1) = 1 THEN 0 WHEN GROUPING(l2) = 1 THEN 1 WHEN GROUPING(l3) = 1 THEN 2 ELSE 3 END AS lvl,
       COALESCE(l1, '') AS l1, COALESCE(l2, '') AS l2, COALESCE(l3, '') AS l3,
       COUNT(DISTINCT product_id) AS spus,
       COUNT(1) AS nrows,
       SUM(units) AS units,
       SUM(amount) AS amount,
       SUM(amount_page) AS amount_page
FROM (
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m,
           std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
           product_id, `count` AS units, discount_sales AS amount, page_sales AS amount_page
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
) x
GROUP BY GROUPING SETS ((site, platform, m), (site, platform, m, l1), (site, platform, m, l1, l2),
                        (site, platform, m, l1, l2, l3))
