-- 价格带：每个 SPU 先在所属层级内汇总，价格 = 到手金额 / 件数，再落入治理层价格带（按站点币种）。
-- lvl 1 = 标准一级类目，lvl 3 = 三级叶子。unbanded = 价格落在所有分带之外，先查分带定义再解读。
-- 只算最新月、上月、去年同月三个月：摘要只比较这三个月，全历史版本实测要 26 分钟。
SELECT s.site, s.platform, s.m, s.lvl, s.l1, s.l2, s.l3,
       COALESCE(pb.band_id, 'unbanded') AS band, MIN(pb.band_sort) AS band_sort,
       COUNT(1) AS spus, SUM(s.units) AS units, SUM(s.amount) AS amount
FROM (
    SELECT site, platform, m, CASE WHEN GROUPING(l3) = 1 THEN 1 ELSE 3 END AS lvl,
           COALESCE(l1, '') AS l1, COALESCE(l2, '') AS l2, COALESCE(l3, '') AS l3, product_id,
           SUM(units) AS units, SUM(amount) AS amount, SUM(amount) / SUM(units) AS price
    FROM (
        SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1,
               std_category_2 AS l2, std_category_3 AS l3, product_id, `count` AS units, discount_sales AS amount
        FROM {{T}}
        WHERE month_dt IN ('{{YOY}}-01', '{{PREV}}-01', '{{END}}-01')
          AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
    ) x
    GROUP BY GROUPING SETS ((site, platform, m, l1, product_id), (site, platform, m, l1, l2, l3, product_id))
) s
LEFT JOIN (
    SELECT site, platform, currency_code
    FROM internal.flywheel_gov.dim_site_currency WHERE release_id = '{{RELEASE}}'
) cur ON cur.site = s.site AND cur.platform = s.platform
LEFT JOIN (
    SELECT profile_currency, band_id, band_sort, lower_inclusive, upper_exclusive
    FROM internal.flywheel_gov.dim_price_band WHERE release_id = '{{RELEASE}}'
) pb ON pb.profile_currency = cur.currency_code AND s.price >= pb.lower_inclusive
    AND (pb.upper_exclusive IS NULL OR s.price < pb.upper_exclusive)
GROUP BY s.site, s.platform, s.m, s.lvl, s.l1, s.l2, s.l3, COALESCE(pb.band_id, 'unbanded')
