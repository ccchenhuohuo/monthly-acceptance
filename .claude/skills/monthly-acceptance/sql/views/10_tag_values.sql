-- 属性标签取值：近 13 个月、关注口径内，每个叶子×维度×取值的行数与件数（只列非空值）。
-- 与治理层属性策略对照：取值是否在受控集内、标签是否只出现在适用叶子、适用叶子上的填充率是否突变。
SELECT * FROM (
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'color_temp' AS dim, TRIM(color_temp) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(color_temp), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(color_temp)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'compat_brand' AS dim, TRIM(compat_brand) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(compat_brand), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(compat_brand)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'connect_type' AS dim, TRIM(connect_type) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(connect_type), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(connect_type)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'mount_type' AS dim, TRIM(mount_type) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(mount_type), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(mount_type)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'drive_type' AS dim, TRIM(drive_type) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(drive_type), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(drive_type)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'transmit_type' AS dim, TRIM(transmit_type) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(transmit_type), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(transmit_type)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'material' AS dim, TRIM(material) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(material), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(material)
    UNION ALL
    SELECT site, platform, DATE_FORMAT(month_dt, '%Y-%m') AS m, std_category_1 AS l1, std_category_2 AS l2,
           std_category_3 AS l3, 'wireless_charging' AS dim, TRIM(wireless_charging) AS val, COUNT(1) AS nrows, SUM(`count`) AS units
    FROM {{T}}
    WHERE month_dt BETWEEN '{{RECENT_FROM}}-01' AND '{{END}}-01'
      AND std_category_1 IS NOT NULL AND std_category_1 <> '其他' AND NULLIF(TRIM(wireless_charging), '') IS NOT NULL
    GROUP BY site, platform, DATE_FORMAT(month_dt, '%Y-%m'), std_category_1, std_category_2, std_category_3, TRIM(wireless_charging)
) t
