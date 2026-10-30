-- 叶子的平台类目构成（单月）：每个国家×平台×标准三级叶子，按平台类目前四级拆开的 SPU、件、额。
-- 用来估整叶的错分量额：头部样本里出现某类错分商品时，看同类商品所在的平台节点占整叶多少；残差叶看吸进了哪些平台节点。
-- 平台类目是服务商的口径，只能近似商品形态（专业商品也可能挂在大众节点下）。分组用原始列，COALESCE 放在外层。
SELECT site, platform, l1, l2, l3, COALESCE(c1, '') AS c1, COALESCE(c2, '') AS c2, COALESCE(c3, '') AS c3,
       COALESCE(c4, '') AS c4, COUNT(DISTINCT product_id) AS spus, SUM(units) AS units, SUM(amount) AS amount
FROM (
    SELECT site, platform, std_category_1 AS l1, std_category_2 AS l2, std_category_3 AS l3,
           category_1 AS c1, category_2 AS c2, category_3 AS c3, category_4 AS c4,
           product_id, `count` AS units, discount_sales AS amount
    FROM {{T}}
    WHERE month_dt = '{{MONTH}}-01' AND std_category_1 IS NOT NULL AND std_category_1 <> '其他'
) x
GROUP BY site, platform, l1, l2, l3, c1, c2, c3, c4
