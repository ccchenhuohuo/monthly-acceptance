-- 现役发布冻结的标准类目树（映射版本）。源表关注行的路径都应在其中。
SELECT std_l1, std_l2, std_l3, mapping_version
FROM internal.flywheel_gov.dim_std_category_mapping
WHERE release_id = '{{RELEASE}}'
