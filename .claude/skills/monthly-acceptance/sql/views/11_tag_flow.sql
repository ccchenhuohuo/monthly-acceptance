-- 打标流向（单月）：平台前三级路径 × 标准一级类目（含“其他”与无标签）的件、额、SPU。
-- 同一平台路径的商品若分流到关注类目和“其他”，“其他”那部分是“本该关注却被打出口径”的候选。
SELECT site, platform, COALESCE(category_1, '') AS c1, COALESCE(category_2, '') AS c2, COALESCE(category_3, '') AS c3,
       CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN '(无标签)' ELSE std_category_1 END AS bucket,
       COUNT(1) AS nrows, COUNT(DISTINCT product_id) AS spus, SUM(`count`) AS units, SUM(discount_sales) AS amount
FROM {{T}}
WHERE month_dt = '{{MONTH}}-01'
GROUP BY site, platform, COALESCE(category_1, ''), COALESCE(category_2, ''), COALESCE(category_3, ''),
         CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN '(无标签)' ELSE std_category_1 END
