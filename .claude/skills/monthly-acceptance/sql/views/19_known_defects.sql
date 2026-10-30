-- 已知缺陷复验：已知缺陷台账与服务商沟通台账登记的商品在全部月份的标准路径、平台路径、件、额。第 6 步对照历史时才读。
-- 登记的编号可能是父体（product_id），也可能是子体（Amazon 页面和群聊里的 ASIN 多是子体），两种都匹配；match_id 是命中的编号。
-- 父体与子体都登记时，同一行在两个 match_id 下各出现一次，各自的追踪互不遮挡。
SELECT site, platform, product_id, match_id, m, l1, l2, l3,
       MAX(platform_path) AS platform_path, SUM(units) AS units, SUM(amount) AS amount, COUNT(1) AS sku_rows,
       MAX(title) AS title
FROM (
    SELECT site, platform, product_id, product_id AS match_id,
           DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
           CONCAT_WS(' > ', category_1, category_2, category_3, category_4) AS platform_path,
           `count` AS units, discount_sales AS amount,
           COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), '')) AS title
    FROM {{T}}
    WHERE product_id IN ({{PIDS}})
    UNION ALL
    SELECT site, platform, product_id, sku_id AS match_id,
           DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
           CONCAT_WS(' > ', category_1, category_2, category_3, category_4) AS platform_path,
           `count` AS units, discount_sales AS amount,
           COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), '')) AS title
    FROM {{T}}
    WHERE sku_id IN ({{PIDS}}) AND sku_id <> product_id
) x
GROUP BY site, platform, product_id, match_id, m, l1, l2, l3
