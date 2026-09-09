SELECT
-- 全历史国家/月观测；中国合并淘系。无记录月由范围日历核对，不补成零销售。
site, month_dt, COUNT(*) AS source_rows,
       COUNT(DISTINCT NULLIF(TRIM(product_id), '')) AS spu_count,
       SUM(discount_sales) AS sales_amount, SUM(`count`) AS units,
       SUM(CASE WHEN NULLIF(TRIM(product_id), '') IS NULL THEN 1 ELSE 0 END) AS invalid_id_rows,
       SUM(CASE WHEN discount_sales IS NULL THEN 1 ELSE 0 END) AS missing_amount_rows
FROM {{TABLE}}
WHERE site = '{{SITE}}' AND month_dt BETWEEN '{{START}}' AND '{{END}}'
  AND ({{FILTER}})
GROUP BY site, month_dt
ORDER BY month_dt
