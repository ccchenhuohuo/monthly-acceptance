-- 打标抽样（单月）：每个国家×平台×三级叶子，件数前 3 的 SPU（头部必核）+ 按种子打散后的 2 个；“-其他”残差叶取前 10。
-- 判定依据是冻结在本次运行里的飞书类目定义与机判规则。title_cn 为服务商翻译的中文标题，title_raw 为原文。
SELECT site, platform, l1, l2, l3, product_id, rk_units, rk_rand, units, amount, amount / units AS price,
       sku_rows, brand, title_cn, title_raw, platform_path, attrs
FROM (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1, l2, l3 ORDER BY units DESC, product_id) AS rk_units,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1, l2, l3 ORDER BY MD5(CONCAT('{{SEED}}', product_id))) AS rk_rand
    FROM (
        SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, product_id,
               SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS sku_rows,
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
) r
WHERE rk_units <= 3 OR rk_rand <= 2 OR (l3 LIKE '%-其他' AND rk_units <= 10)
