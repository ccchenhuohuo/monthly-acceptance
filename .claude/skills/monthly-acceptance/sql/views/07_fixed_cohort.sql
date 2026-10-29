-- 固定头部集合：基期当月件数 TOP1000 的同一批 SPU，到下个月的件数。判读要扣除排名回落（同源相邻月实测约 -5%~-9%），
-- 并对照上一年同一月份对的变化（季节基线）。显著偏离二者，才是估算口径变化的信号。
SELECT b.site, b.platform, b.scope, b.m AS base_m,
       COUNT(1) AS cohort_spus, SUM(b.units) AS base_units, SUM(COALESCE(c.units, 0)) AS next_units,
       SUM(CASE WHEN c.product_id IS NULL THEN 1 ELSE 0 END) AS vanished,
       SUM(CASE WHEN b.rk <= 100 THEN b.units ELSE 0 END) AS top100_base_units,
       SUM(CASE WHEN b.rk <= 100 THEN COALESCE(c.units, 0) ELSE 0 END) AS top100_next_units
FROM (
    SELECT * FROM (
        SELECT site, platform, scope, m, mi, product_id, units,
               ROW_NUMBER() OVER (PARTITION BY site, platform, scope, mi ORDER BY units DESC, product_id) AS rk
        FROM (
            SELECT site, platform, 'all' AS scope, DATE_FORMAT(month_dt, '%Y-%m') AS m,
                   YEAR(month_dt) * 12 + MONTH(month_dt) AS mi, product_id, SUM(`count`) AS units
            FROM {{T}}
            WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
            GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), YEAR(month_dt) * 12 + MONTH(month_dt), product_id
            UNION ALL
            SELECT site, platform, 'focus' AS scope, DATE_FORMAT(month_dt, '%Y-%m') AS m,
                   YEAR(month_dt) * 12 + MONTH(month_dt) AS mi, product_id, SUM(`count`) AS units
            FROM {{T}}
            WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
              AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
            GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), YEAR(month_dt) * 12 + MONTH(month_dt), product_id
        ) a
    ) ranked
    WHERE rk <= 1000
) b
LEFT JOIN (
    SELECT site, platform, 'all' AS scope, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi, product_id, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
    GROUP BY site, platform, YEAR(month_dt) * 12 + MONTH(month_dt), product_id
    UNION ALL
    SELECT site, platform, 'focus' AS scope, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi, product_id, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
    GROUP BY site, platform, YEAR(month_dt) * 12 + MONTH(month_dt), product_id
) c ON c.site = b.site AND c.platform = b.platform AND c.scope = b.scope AND c.product_id = b.product_id AND c.mi = b.mi + 1
GROUP BY b.site, b.platform, b.scope, b.m
