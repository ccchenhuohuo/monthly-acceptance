-- 已知缺陷复验：台账里登记的商品在全部月份的标准路径、平台路径、件、额。第 6 步对照历史时才读。
SELECT site, platform, product_id, DATE_FORMAT(month_dt, '%Y-%m') AS m,
       std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
       MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4)) AS platform_path,
       SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS sku_rows,
       MAX(COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title
FROM {{T}}
WHERE product_id IN ({{PIDS}})
GROUP BY site, platform, product_id, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3
