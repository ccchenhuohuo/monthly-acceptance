-- 到货与水位：国家×平台×月的行、SPU、件、额，以及关注口径（标准一级类目非“其他”）与无标签行。
-- 金额为到手金额 discount_sales（= 到手价 × 件数），本币。
SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m,
       COUNT(1) AS nrows,
       COUNT(DISTINCT product_id) AS spus,
       SUM(CASE WHEN sku_id IS NULL OR TRIM(sku_id) IN ('', '0') THEN 1 ELSE 0 END) AS placeholder_rows,
       SUM(`count`) AS units,
       SUM(discount_sales) AS amount,
       SUM(page_sales) AS amount_page,
       SUM(CASE WHEN `count` IS NULL OR `count` <= 0 OR discount_price IS NULL OR discount_price <= 0
                  OR discount_sales IS NULL OR discount_sales < 0 THEN 1 ELSE 0 END) AS invalid_rows,
       SUM(CASE WHEN std_category_1 IS NOT NULL AND std_category_1 <> '其他' THEN 1 ELSE 0 END) AS focus_rows,
       COUNT(DISTINCT CASE WHEN std_category_1 IS NOT NULL AND std_category_1 <> '其他' THEN product_id END) AS focus_spus,
       SUM(CASE WHEN std_category_1 IS NOT NULL AND std_category_1 <> '其他' THEN `count` ELSE 0 END) AS focus_units,
       SUM(CASE WHEN std_category_1 IS NOT NULL AND std_category_1 <> '其他' THEN discount_sales ELSE 0 END) AS focus_amount,
       SUM(CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN 1 ELSE 0 END) AS untagged_rows,
       SUM(CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN `count` ELSE 0 END) AS untagged_units,
       SUM(CASE WHEN std_category_1 IS NULL OR TRIM(std_category_1) = '' THEN discount_sales ELSE 0 END) AS untagged_amount
FROM {{T}}
WHERE month_dt BETWEEN '{{START}}-01' AND '{{END}}-01'
GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m')
ORDER BY site, platform, m
