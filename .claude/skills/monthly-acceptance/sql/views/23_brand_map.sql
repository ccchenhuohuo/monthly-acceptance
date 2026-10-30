-- 品牌映射（近三个月合计）：每个国家×平台，“标准品牌 ← 原始品牌”的配对及其 SPU、件、额，按金额排前 3000 对。
-- 标准品牌由服务商清洗；这里核对清洗本身：一个标准名吞进不相干的原始品牌（疑似按子串归并）、同一品牌拆成几个标准名、
-- 同一品牌跨站写法不同、把“中性”“无”这类非品牌词当品牌。原始品牌为空的行记作空串。
-- 分段跑（views.py 每个国家×平台一段）。
SELECT site, platform, brand, raw_brand, spus, units, amount, rk
FROM (
    SELECT a.*, ROW_NUMBER() OVER (PARTITION BY site, platform ORDER BY amount DESC, brand, raw_brand) AS rk
    FROM (
        SELECT site, platform, brand, raw_brand, COUNT(1) AS spus, SUM(units) AS units, SUM(amount) AS amount
        FROM (
            SELECT site, platform, COALESCE(std_brand_name, '') AS brand, COALESCE(TRIM(brand_name), '') AS raw_brand, product_id,
                   SUM(`count`) AS units, SUM(discount_sales) AS amount
            FROM {{T}}
            WHERE month_dt BETWEEN '{{Q_FROM}}-01' AND '{{MONTH}}-01' AND site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
            GROUP BY site, platform, COALESCE(std_brand_name, ''), COALESCE(TRIM(brand_name), ''), product_id
        ) p
        GROUP BY site, platform, brand, raw_brand
    ) a
) b
WHERE rk <= 3000
