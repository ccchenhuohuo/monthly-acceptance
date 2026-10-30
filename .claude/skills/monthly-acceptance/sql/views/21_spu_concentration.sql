-- 单品集中度（上月与最新月）：每个国家×平台×标准一级类目，两期金额前 5、本月件数前 5、金额或件数变化最大的 5 个 SPU 及其两期件额。
-- 抓“一条商品撑起或扭转整个一级类目”：身份错联、估算失真、价格变化不到 3 倍的错价，跳价检查拦不下，在这里现形。
-- 一级类目两期合计取 02_std_grid；SPU 按一级类目分别汇总，子体跨一级类目时各占一行。
SELECT site, platform, l1, product_id, units_prev, units_cur, amount_prev, amount_cur,
       rk_amount_cur, rk_amount_prev, rk_units_cur, rk_damount, rk_dunits, brand, title_cn, title_raw, platform_path
FROM (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1 ORDER BY amount_cur DESC, product_id) AS rk_amount_cur,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1 ORDER BY amount_prev DESC, product_id) AS rk_amount_prev,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1 ORDER BY units_cur DESC, product_id) AS rk_units_cur,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1 ORDER BY ABS(amount_cur - amount_prev) DESC, product_id) AS rk_damount,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1 ORDER BY ABS(units_cur - units_prev) DESC, product_id) AS rk_dunits
    FROM (
        SELECT site, platform, std_category_1 AS l1, product_id,
               SUM(CASE WHEN month_dt = '{{PREV}}-01' THEN `count` ELSE 0 END) AS units_prev,
               SUM(CASE WHEN month_dt = '{{MONTH}}-01' THEN `count` ELSE 0 END) AS units_cur,
               SUM(CASE WHEN month_dt = '{{PREV}}-01' THEN discount_sales ELSE 0 END) AS amount_prev,
               SUM(CASE WHEN month_dt = '{{MONTH}}-01' THEN discount_sales ELSE 0 END) AS amount_cur,
               MAX(std_brand_name) AS brand,
               MAX(NULLIF(TRIM(product_title_cn), '')) AS title_cn,
               MAX(COALESCE(NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title_raw,
               MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4)) AS platform_path
        FROM {{T}}
        WHERE month_dt IN ('{{PREV}}-01', '{{MONTH}}-01') AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
        GROUP BY site, platform, std_category_1, product_id
    ) s
) r
WHERE rk_amount_cur <= 5 OR rk_amount_prev <= 5 OR rk_units_cur <= 5 OR rk_damount <= 5 OR rk_dunits <= 5
