-- 叶子扩抽（单月）：某国家×平台×三级叶子，按件数排名第 FROM_RANK 到 TO_RANK 的 SPU，带标题与平台路径。
-- 抽查发现错分时，按命中的叶子扩大核查范围。参数：SITE PLATFORM MONTH L1 L2 L3 FROM_RANK TO_RANK
SELECT * FROM (
    SELECT a.*, ROW_NUMBER() OVER (ORDER BY units DESC, product_id) AS rk
    FROM (
        SELECT product_id, SUM(`count`) AS units, SUM(discount_sales) AS amount, COUNT(1) AS sku_rows,
               MAX(std_brand_name) AS brand,
               MAX(NULLIF(TRIM(product_title_cn), '')) AS title_cn,
               MAX(COALESCE(NULLIF(TRIM(product_title), ''), NULLIF(TRIM(sku_title), ''))) AS title_raw,
               MAX(CONCAT_WS(' > ', category_1, category_2, category_3, category_4, category_5)) AS platform_path
        FROM {{T}}
        WHERE site = '{{SITE}}' AND platform = '{{PLATFORM}}' AND month_dt = '{{MONTH}}-01'
          AND std_category_1 = '{{L1}}' AND std_category_2 = '{{L2}}' AND std_category_3 = '{{L3}}'
        GROUP BY product_id
    ) a
) r
WHERE rk BETWEEN {{FROM_RANK}} AND {{TO_RANK}}
