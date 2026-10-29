-- 治理承接：源表关注口径与现役发布的 dws_market_month_agg，各自按 国家×平台×月×标准路径 汇总后上下拼接（side = src / gov）。
-- 逐格对账在摘要里做：任何一边缺格、或件 / 额 / 行数不等，都说明对外发布的数与源表不一致。
-- 不在库里做 FULL OUTER JOIN：那个写法在本集群上两次报 INTERNAL_ERROR。
SELECT 'src' AS side, site, platform, m, l1, l2, l3, units, amount, amount_page, nrows
FROM (
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, SUM(`count`) AS units, SUM(discount_sales) AS amount, SUM(page_sales) AS amount_page,
           COUNT(1) AS nrows
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3
) s
UNION ALL
SELECT 'gov' AS side, site, platform, m, l1, l2, l3, units, amount, amount_page, nrows
FROM (
    SELECT site, platform, DATE_FORMAT(stat_month, '%Y-%m') AS m, std_l1 AS l1, std_l2 AS l2, std_l3 AS l3,
           SUM(units_est) AS units, SUM(amt_discount_local) AS amount, SUM(amt_page_local) AS amount_page,
           SUM(src_row_cnt) AS nrows
    FROM internal.flywheel_gov.dws_market_month_agg
    WHERE release_id = '{{RELEASE}}' AND stat_month BETWEEN '{{START}}-01' AND '{{END}}-01'
    GROUP BY site, platform, DATE_FORMAT(stat_month, '%Y-%m'), std_l1, std_l2, std_l3
) g
