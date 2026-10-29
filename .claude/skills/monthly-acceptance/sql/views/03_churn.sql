-- 新增 / 留存 / 消失拆解（全部站点一次算；lag=1 环比，lag=12 同比）。
-- lvl 0 = 关注口径合计，lvl 1 = 标准一级类目。区分“覆盖变了”（增量来自新商品）与“估算变了”（留存商品同步涨跌）。
-- 窗口首月没有基期，base_spus 为 0 的月份不能解读。
SELECT site, platform, lvl, l1,
       CONCAT(CAST(FLOOR((mi - 1) / 12) AS STRING), '-', LPAD(CAST(MOD(mi - 1, 12) + 1 AS STRING), 2, '0')) AS m,
       SUM(hb) AS base_spus, SUM(hc) AS cur_spus,
       SUM(CASE WHEN hb = 0 THEN 1 ELSE 0 END) AS entered,
       SUM(CASE WHEN hc = 0 THEN 1 ELSE 0 END) AS exited,
       SUM(CASE WHEN hb = 1 AND hc = 1 THEN 1 ELSE 0 END) AS kept,
       SUM(bu) AS base_units, SUM(cu) AS cur_units,
       SUM(CASE WHEN hb = 0 THEN cu ELSE 0 END) AS entered_units,
       SUM(CASE WHEN hc = 0 THEN bu ELSE 0 END) AS exited_units,
       SUM(CASE WHEN hb = 1 AND hc = 1 THEN bu ELSE 0 END) AS kept_base_units,
       SUM(CASE WHEN hb = 1 AND hc = 1 THEN cu ELSE 0 END) AS kept_cur_units,
       SUM(ba) AS base_amount, SUM(ca) AS cur_amount,
       SUM(CASE WHEN hb = 0 THEN ca ELSE 0 END) AS entered_amount,
       SUM(CASE WHEN hc = 0 THEN ba ELSE 0 END) AS exited_amount,
       SUM(CASE WHEN hb = 1 AND hc = 1 THEN ba ELSE 0 END) AS kept_base_amount,
       SUM(CASE WHEN hb = 1 AND hc = 1 THEN ca ELSE 0 END) AS kept_cur_amount
FROM (
    SELECT COALESCE(c.site, b.site) AS site, COALESCE(c.platform, b.platform) AS platform,
           COALESCE(c.lvl, b.lvl) AS lvl, COALESCE(c.l1, b.l1) AS l1,
           COALESCE(c.mi, b.mi + {{LAG}}) AS mi,
           CASE WHEN b.product_id IS NULL THEN 0 ELSE 1 END AS hb,
           CASE WHEN c.product_id IS NULL THEN 0 ELSE 1 END AS hc,
           COALESCE(b.units, 0) AS bu, COALESCE(c.units, 0) AS cu,
           COALESCE(b.amount, 0) AS ba, COALESCE(c.amount, 0) AS ca
    FROM (
        SELECT site, platform, CASE WHEN GROUPING(l1) = 1 THEN 0 ELSE 1 END AS lvl, COALESCE(l1, '') AS l1,
               mi, product_id, SUM(units) AS units, SUM(amount) AS amount
        FROM (
            SELECT site, platform, std_category_1 AS l1, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
                   product_id, `count` AS units, discount_sales AS amount
            FROM {{T}}
            WHERE month_dt BETWEEN '{{FROM}}-01' AND '{{END}}-01'
              AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
        ) x
        GROUP BY GROUPING SETS ((site, platform, mi, product_id), (site, platform, l1, mi, product_id))
    ) b
    FULL OUTER JOIN (
        SELECT site, platform, CASE WHEN GROUPING(l1) = 1 THEN 0 ELSE 1 END AS lvl, COALESCE(l1, '') AS l1,
               mi, product_id, SUM(units) AS units, SUM(amount) AS amount
        FROM (
            SELECT site, platform, std_category_1 AS l1, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
                   product_id, `count` AS units, discount_sales AS amount
            FROM {{T}}
            WHERE month_dt BETWEEN '{{FROM}}-01' AND '{{END}}-01'
              AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
        ) x
        GROUP BY GROUPING SETS ((site, platform, mi, product_id), (site, platform, l1, mi, product_id))
    ) c
      ON c.site = b.site AND c.platform = b.platform AND c.lvl = b.lvl AND c.l1 = b.l1
     AND c.product_id = b.product_id AND c.mi = b.mi + {{LAG}}
) j
WHERE mi BETWEEN {{START_MI}} AND {{END_MI}}
GROUP BY site, platform, lvl, l1, mi
