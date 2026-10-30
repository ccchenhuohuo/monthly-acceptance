-- 打标抽样（单月）：每个国家×平台×三级叶子，件数前 3、金额前 3（头部必核）、单价 ≥ 叶子中位数 10 倍的金额前 3（贵价），
-- 加按种子打散后的 2 个；“-其他”残差叶取件数前 10。只按件数抽会漏掉件数少、单价高的错分（整机套装进了配件叶），它们却左右叶子金额。
-- 判定依据是冻结在本次运行里的飞书类目定义与机判规则。title_cn 为服务商翻译的中文标题，title_raw 为原文。
-- price_ratio = SPU 到手均价 / 叶子内 SPU 到手均价的中位数。
SELECT site, platform, l1, l2, l3, product_id, rk_units, rk_amount, rk_rand, units, amount, price, leaf_median, price_ratio,
       sku_rows, brand, title_cn, title_raw, platform_path, attrs
FROM (
    SELECT s.*, md.leaf_median, s.price / md.leaf_median AS price_ratio,
           ROW_NUMBER() OVER (PARTITION BY s.site, s.platform, s.l1, s.l2, s.l3 ORDER BY s.units DESC, s.product_id) AS rk_units,
           ROW_NUMBER() OVER (PARTITION BY s.site, s.platform, s.l1, s.l2, s.l3 ORDER BY s.amount DESC, s.product_id) AS rk_amount,
           ROW_NUMBER() OVER (PARTITION BY s.site, s.platform, s.l1, s.l2, s.l3 ORDER BY MD5(CONCAT('{{SEED}}', s.product_id))) AS rk_rand,
           ROW_NUMBER() OVER (PARTITION BY s.site, s.platform, s.l1, s.l2, s.l3
                              ORDER BY CASE WHEN s.price >= 10 * md.leaf_median THEN 0 ELSE 1 END, s.amount DESC, s.product_id) AS rk_pricey
    FROM (
        SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, product_id,
               SUM(`count`) AS units, SUM(discount_sales) AS amount, SUM(discount_sales) / SUM(`count`) AS price, COUNT(1) AS sku_rows,
               MAX(std_brand_name) AS brand,
               MAX(NULLIF(TRIM(product_title_cn), '')) AS title_cn,
               MAX(COALESCE(NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title_raw,
               MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4, category_5)) AS platform_path,
               MAX(CONCAT_WS('; ', color_temp, compat_brand, connect_type, mount_type, drive_type, transmit_type,
                             material, wireless_charging)) AS attrs
        FROM {{T}}
        WHERE month_dt = '{{MONTH}}-01' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
        GROUP BY site, platform, std_category_1, std_category_2, std_category_3, product_id
    ) s
    JOIN (
        SELECT site, platform, l1, l2, l3, PERCENTILE_APPROX(price, 0.5) AS leaf_median
        FROM (
            SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, product_id,
                   SUM(discount_sales) / SUM(`count`) AS price
            FROM {{T}}
            WHERE month_dt = '{{MONTH}}-01' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
            GROUP BY site, platform, std_category_1, std_category_2, std_category_3, product_id
        ) p
        GROUP BY site, platform, l1, l2, l3
    ) md ON s.site = md.site AND s.platform = md.platform AND s.l1 = md.l1 AND s.l2 = md.l2 AND s.l3 = md.l3
) r
WHERE rk_units <= 3 OR rk_amount <= 3 OR rk_rand <= 2 OR (l3 LIKE '%-其他' AND rk_units <= 10)
   OR (price_ratio >= 10 AND rk_pricey <= 3)
