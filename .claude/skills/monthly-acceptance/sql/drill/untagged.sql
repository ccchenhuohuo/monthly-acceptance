-- 无标签商品（单月）：标准一级类目为空的 SPU，按件数排序。参数：MONTH N
SELECT site, platform, product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS sku_rows,
       MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4, category_5)) AS platform_path,
       MAX(std_brand_name) AS brand,
       MAX(COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title
FROM {{T}}
WHERE month_dt = '{{MONTH}}-01' AND (std_category_1 IS NULL OR TRIM(std_category_1) = '')
GROUP BY site, platform, product_id
ORDER BY units DESC
LIMIT {{N}}
