-- 平台路径明细（单月）：某平台路径前缀下按件数排序的 SPU，带标准路径。用于看“本该关注却被打成其他”、无标签、
-- 采购范围内路径被整片打出口径等问题。C2/C3/C4 传空串表示不限。参数：SITE PLATFORM MONTH C1 C2 C3 C4 N
SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS sku_rows,
       MAX(CONCAT_WS(' > ', COALESCE(std_category_1, '(无标签)'), std_category_2, std_category_3)) AS std_path,
       MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4, category_5)) AS platform_path,
       MAX(std_brand_name) AS brand,
       MAX(COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title
FROM {{T}}
WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{MONTH}}-01'
  AND category_1 = '{{C1}}' AND ('{{C2}}' = '' OR category_2 = '{{C2}}')
  AND ('{{C3}}' = '' OR category_3 = '{{C3}}') AND ('{{C4}}' = '' OR category_4 = '{{C4}}')
GROUP BY product_id
ORDER BY units DESC
LIMIT {{N}}
