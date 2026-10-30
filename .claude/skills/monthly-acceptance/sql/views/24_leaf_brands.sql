-- 小类的品牌构成：每个国家×平台×三级叶子，近三个月（cur）与上年同期三个月（yoy）按金额前 5 的品牌，外加 OTHERS/其他，附叶子合计。
-- 两个用途：看每个小类的头部品牌属不属于这个小类（摄影监视器里排进电脑显示器品牌）；看 OTHERS 在小类、二级类目层面的占比和同比，
-- 站点级的 OTHERS 占比会把一个小类的品牌缺失平均掉。
-- 分段跑（views.py 每个国家×平台一段，天猫再按一级类目分段；L1 为 % 时不分）。
SELECT site, platform, l1, l2, l3, period, brand, spus, units, amount, leaf_units, leaf_amount, rk
FROM (
    SELECT a.*,
           SUM(units) OVER (PARTITION BY site, platform, l1, l2, l3, period) AS leaf_units,
           SUM(amount) OVER (PARTITION BY site, platform, l1, l2, l3, period) AS leaf_amount,
           ROW_NUMBER() OVER (PARTITION BY site, platform, l1, l2, l3, period ORDER BY amount DESC, brand) AS rk
    FROM (
        SELECT site, platform, l1, l2, l3, period, brand, COUNT(1) AS spus, SUM(units) AS units, SUM(amount) AS amount
        FROM (
            SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
                   CASE WHEN month_dt >= '{{Q_FROM}}-01' THEN 'cur' ELSE 'yoy' END AS period,
                   COALESCE(std_brand_name, '') AS brand, product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount
            FROM {{T}}
            WHERE (month_dt BETWEEN '{{Q_FROM}}-01' AND '{{MONTH}}-01' OR month_dt BETWEEN '{{YOY_Q_FROM}}-01' AND '{{YOY}}-01')
              AND site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND std_category_1 LIKE '{{L1}}'
            GROUP BY site, platform, std_category_1, std_category_2, std_category_3,
                     CASE WHEN month_dt >= '{{Q_FROM}}-01' THEN 'cur' ELSE 'yoy' END, COALESCE(std_brand_name, ''), product_id
        ) p
        GROUP BY site, platform, l1, l2, l3, period, brand
    ) a
) b
WHERE rk <= 5 OR brand = 'OTHERS/其他'
