-- 商品轨迹：指定商品全部月份每个 SKU 的标准路径、平台路径、件、到手价、页面价、标题。
-- 用于核对身份连续性、跳价、跨月换叶。参数：SITE PLATFORM，列表 PIDS（product_id）
SELECT DATE_FORMAT(month_dt, '%Y-%m') AS m, product_id, sku_id,
       CONCAT_WS(' > ', std_category_1, std_category_2, std_category_3) AS std_path,
       CONCAT_WS(' > ', category_1, category_2, category_3, category_4, category_5) AS platform_path,
       `count` AS units, discount_price, page_price, discount_sales AS amount, std_brand_name AS brand,
       COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), '')) AS title,
       product_listing_time
FROM {{T}}
WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND product_id IN ({{PIDS}})
ORDER BY product_id, sku_id, m
