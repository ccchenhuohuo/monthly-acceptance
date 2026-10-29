-- 平台类目树：平台前四级路径×月的行、件、额，以及其中关注口径、“其他”、无标签各占多少。
-- 用来查采购范围是否到齐、路径改版/断档、以及哪些平台路径的商品被打进了“其他”。
-- 分组键直接用原始列、空值在外层再补成空串：键上套 COALESCE 时单月实测 56 秒，去掉后 32 秒。
SELECT site, platform, COALESCE(category_1, '') AS c1, COALESCE(category_2, '') AS c2,
       COALESCE(category_3, '') AS c3, COALESCE(category_4, '') AS c4, m,
       nrows, units, amount, focus_units, other_units, untagged_units
FROM (
    SELECT site, platform, category_1, category_2, category_3, category_4, DATE_FORMAT(month_dt, '%Y-%m') AS m,
           COUNT(1) AS nrows, SUM(`count`) AS units, SUM(discount_sales) AS amount,
           SUM(CASE WHEN std_category_1 IS NOT NULL AND std_category_1 <> '其他' THEN `count` ELSE 0 END) AS focus_units,
           SUM(CASE WHEN std_category_1 = '其他' THEN `count` ELSE 0 END) AS other_units,
           SUM(CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN `count` ELSE 0 END) AS untagged_units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
    GROUP BY site, platform, category_1, category_2, category_3, category_4, DATE_FORMAT(month_dt, '%Y-%m')
) x
