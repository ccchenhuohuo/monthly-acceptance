-- 本品：本品品牌在每个国家×平台×月×标准一级类目的件、额、SPU。类目总量取自 02_std_grid，用来算本品份额。
SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_brand_name AS brand,
       SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(DISTINCT product_id) AS spus
FROM {{T}}
WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
  AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
  AND std_brand_name IN ({{BRANDS}})
GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_brand_name
