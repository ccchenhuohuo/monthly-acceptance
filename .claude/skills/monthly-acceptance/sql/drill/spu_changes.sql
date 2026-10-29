-- 类目变化的驱动商品：某国家×平台×标准路径，两个月之间每个 SPU 的件、额、价，标出新增 / 留存 / 消失，
-- 按件数变化绝对值排序。L2/L3 传空串表示整个上级类目。用于“少数商品决定了类目涨跌”的核查（如镜头盖计入滤镜）。
-- 参数：SITE PLATFORM L1 L2 L3 BASE CUR（YYYY-MM） N
SELECT COALESCE(c.product_id, b.product_id) AS product_id,
       CASE WHEN b.product_id IS NULL THEN '新增' WHEN c.product_id IS NULL THEN '消失' ELSE '留存' END AS status,
       b.units AS base_units, c.units AS cur_units, COALESCE(c.units, 0) - COALESCE(b.units, 0) AS d_units,
       b.amount AS base_amount, c.amount AS cur_amount, COALESCE(c.amount, 0) - COALESCE(b.amount, 0) AS d_amount,
       b.amount / b.units AS base_price, c.amount / c.units AS cur_price,
       COALESCE(c.leaf, b.leaf) AS leaf, COALESCE(c.brand, b.brand) AS brand, COALESCE(c.title, b.title) AS title,
       COALESCE(c.platform_path, b.platform_path) AS platform_path
FROM (
    SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount, MAX(std_category_3) AS leaf,
           MAX(std_brand_name) AS brand,
           MAX(COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title,
           MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4)) AS platform_path
    FROM {{T}}
    WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{BASE}}-01'
      AND std_category_1 = '{{L1}}' AND ('{{L2}}' = '' OR std_category_2 = '{{L2}}') AND ('{{L3}}' = '' OR std_category_3 = '{{L3}}')
    GROUP BY product_id
) b
FULL OUTER JOIN (
    SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount, MAX(std_category_3) AS leaf,
           MAX(std_brand_name) AS brand,
           MAX(COALESCE(NULLIF(TRIM(product_title_cn), ''), NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title,
           MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4)) AS platform_path
    FROM {{T}}
    WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{CUR}}-01'
      AND std_category_1 = '{{L1}}' AND ('{{L2}}' = '' OR std_category_2 = '{{L2}}') AND ('{{L3}}' = '' OR std_category_3 = '{{L3}}')
    GROUP BY product_id
) c ON c.product_id = b.product_id
ORDER BY ABS(COALESCE(c.units, 0) - COALESCE(b.units, 0)) DESC
LIMIT {{N}}
