-- 现役发布冻结的属性标签策略：哪个叶子适用哪个维度、受控取值是什么（斜杠分隔）。
SELECT std_l1, std_l2, std_l3, dimension, tag_values, tag_policy_version
FROM internal.flywheel_gov.dim_tag_applicability
WHERE release_id = '{{RELEASE}}'
