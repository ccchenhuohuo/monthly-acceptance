-- 打标一致性：同一 SKU 相邻两个月都在时，标准路径变了多少（含关注↔“其他”的进出）。全部站点一次算，近 13 个月。
SELECT c.site, c.platform, c.m,
       COUNT(1) AS kept_skus,
       SUM(CASE WHEN b.p <=> c.p THEN 0 ELSE 1 END) AS changed,
       SUM(CASE WHEN b.p <=> c.p THEN 0 ELSE c.units END) AS changed_units,
       SUM(c.units) AS kept_units,
       SUM(CASE WHEN b.l1 = '其他' AND c.l1 <> '其他' THEN 1 ELSE 0 END) AS other_to_focus,
       SUM(CASE WHEN b.l1 <> '其他' AND c.l1 = '其他' THEN 1 ELSE 0 END) AS focus_to_other,
       SUM(CASE WHEN b.l1 = c.l1 AND NOT (b.p <=> c.p) THEN 1 ELSE 0 END) AS changed_within_l1
FROM (
    SELECT site, platform, product_id, sku_id, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
           MAX(COALESCE(std_category_1, '(无标签)')) AS l1,
           MAX(CONCAT_WS('|', COALESCE(std_category_1, '(无标签)'), std_category_2, std_category_3)) AS p
    FROM {{T}}
    WHERE month_dt BETWEEN '{{FROM}}-01' AND '{{END}}-01'
    GROUP BY site, platform, product_id, sku_id, YEAR(month_dt) * 12 + MONTH(month_dt)
) b
INNER JOIN (
    SELECT site, platform, product_id, sku_id, DATE_FORMAT(month_dt, '%Y-%m') AS m, YEAR(month_dt) * 12 + MONTH(month_dt) AS mi,
           MAX(COALESCE(std_category_1, '(无标签)')) AS l1,
           MAX(CONCAT_WS('|', COALESCE(std_category_1, '(无标签)'), std_category_2, std_category_3)) AS p,
           SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
    GROUP BY site, platform, product_id, sku_id, DATE_FORMAT(month_dt, '%Y-%m'), YEAR(month_dt) * 12 + MONTH(month_dt)
) c ON c.site = b.site AND c.platform = b.platform AND c.product_id = b.product_id AND c.sku_id <=> b.sku_id AND c.mi = b.mi + 1
GROUP BY c.site, c.platform, c.m
