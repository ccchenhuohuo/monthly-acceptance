-- 品牌拆解：某国家×平台×标准一级类目×品牌，两个月之间按 SPU 拆成新增 / 留存 / 消失。
-- 增量来自新商品多为覆盖变化；同一批留存商品大涨是真实起量或估算调整，最需警惕。参数：SITE PLATFORM L1 BRAND BASE CUR
SELECT CASE WHEN b.product_id IS NULL THEN '新增' WHEN c.product_id IS NULL THEN '消失' ELSE '留存' END AS status,
       COUNT(1) AS spus, SUM(COALESCE(b.units, 0)) AS base_units, SUM(COALESCE(c.units, 0)) AS cur_units,
       SUM(COALESCE(b.amount, 0)) AS base_amount, SUM(COALESCE(c.amount, 0)) AS cur_amount
FROM (
    SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount
    FROM {{T}}
    WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{BASE}}-01'
      AND std_category_1 = '{{L1}}' AND std_brand_name = '{{BRAND}}'
    GROUP BY product_id
) b
FULL OUTER JOIN (
    SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount
    FROM {{T}}
    WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{CUR}}-01'
      AND std_category_1 = '{{L1}}' AND std_brand_name = '{{BRAND}}'
    GROUP BY product_id
) c ON c.product_id = b.product_id
GROUP BY CASE WHEN b.product_id IS NULL THEN '新增' WHEN c.product_id IS NULL THEN '消失' ELSE '留存' END
