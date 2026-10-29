-- 治理层自带的覆盖率指标（每个国家×平台×月 13 项）：品牌缺失率、OTHERS 件数占比、未分类剔除占比、空值率、缺价行等。
SELECT site, platform, DATE_FORMAT(stat_month, '%Y-%m') AS m, indicator_id, value, numerator, denominator
FROM internal.flywheel_gov.gov_coverage_month
WHERE release_id = '{{RELEASE}}'
