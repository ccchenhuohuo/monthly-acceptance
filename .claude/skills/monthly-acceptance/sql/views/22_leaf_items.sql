-- 小类里的单品（近三个月合计）：每个国家×平台×三级叶子，按金额前 5 的 SPU，加上占叶子金额 ≥15%、到手均价 ≥ 叶子中位数 20 倍、
-- 挂在叶子里少见的平台根节点下（该根节点不到叶子金额一成）且占叶子金额 ≥1% 的 SPU。下游按小类、按金额用数，问题也藏在这一层：
-- 一个错分的整机套装能占一个小类金额的大半，放到一级类目里就不显眼；一直错的价格从不“跳”，跨月跳价检查拦不下。
-- 叶子主体根节点 main_root = 叶子金额最多的平台一级类目，root_amount = 该 SPU 所在根节点在叶子里的金额；
-- price = SPU 到手金额 / 件；leaf_median = 叶子内 SPU 均价的中位数。
-- 分段跑（views.py 每个国家×平台一段，天猫再按一级类目分段；L1 为 % 时不分）：三个月的全量商品聚合一次跑完会超过网络路径的十分钟上限。
SELECT site, platform, l1, l2, l3, product_id, brand, raw_brand, units, amount, price, leaf_units, leaf_amount, leaf_spus,
       leaf_median, main_root, c1, root_amount, platform_path, title_cn, title_raw, rk_amount
FROM (
    SELECT s.*, t.leaf_units, t.leaf_amount, t.leaf_spus, t.leaf_median, ro.main_root, ro.root_amount,
           ROW_NUMBER() OVER (PARTITION BY s.site, s.platform, s.l1, s.l2, s.l3 ORDER BY s.amount DESC, s.product_id) AS rk_amount
    FROM (
        SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, product_id,
               MAX(std_brand_name) AS brand, MAX(brand_name) AS raw_brand,
               SUM(`count`) AS units, SUM(discount_sales) AS amount, SUM(discount_sales) / SUM(`count`) AS price,
               MAX(COALESCE(category_1, '')) AS c1,
               MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4)) AS platform_path,
               MAX(NULLIF(TRIM(product_title_cn), '')) AS title_cn,
               MAX(COALESCE(NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title_raw
        FROM {{T}}
        WHERE month_dt BETWEEN '{{Q_FROM}}-01' AND '{{MONTH}}-01' AND site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND std_category_1 LIKE '{{L1}}'
        GROUP BY site, platform, std_category_1, std_category_2, std_category_3, product_id
    ) s
    JOIN (
        SELECT site, platform, l1, l2, l3, SUM(units) AS leaf_units, SUM(amount) AS leaf_amount, COUNT(1) AS leaf_spus,
               PERCENTILE_APPROX(amount / units, 0.5) AS leaf_median
        FROM (
            SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3, product_id,
                   SUM(`count`) AS units, SUM(discount_sales) AS amount
            FROM {{T}}
            WHERE month_dt BETWEEN '{{Q_FROM}}-01' AND '{{MONTH}}-01' AND site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND std_category_1 LIKE '{{L1}}'
            GROUP BY site, platform, std_category_1, std_category_2, std_category_3, product_id
        ) p
        GROUP BY site, platform, l1, l2, l3
    ) t ON s.site = t.site AND s.platform = t.platform AND s.l1 = t.l1 AND s.l2 = t.l2 AND s.l3 = t.l3
    JOIN (
        SELECT site, platform, l1, l2, l3, c1, root_amount,
               FIRST_VALUE(c1) OVER (PARTITION BY site, platform, l1, l2, l3 ORDER BY root_amount DESC, c1) AS main_root
        FROM (
            SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
                   COALESCE(category_1, '') AS c1, SUM(discount_sales) AS root_amount
            FROM {{T}}
            WHERE month_dt BETWEEN '{{Q_FROM}}-01' AND '{{MONTH}}-01' AND site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND std_category_1 LIKE '{{L1}}'
            GROUP BY site, platform, std_category_1, std_category_2, std_category_3, COALESCE(category_1, '')
        ) a
    ) ro ON s.site = ro.site AND s.platform = ro.platform AND s.l1 = ro.l1 AND s.l2 = ro.l2 AND s.l3 = ro.l3 AND s.c1 = ro.c1
) r
WHERE rk_amount <= 5 OR amount >= 0.15 * leaf_amount OR price >= 20 * leaf_median
   OR (c1 <> main_root AND root_amount < 0.10 * leaf_amount AND amount >= 0.01 * leaf_amount)
