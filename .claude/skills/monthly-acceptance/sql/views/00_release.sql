-- 治理层发布代次：现役 = status='published' 中 built_at 最新的一条。
SELECT release_id, status, built_at, published_at, max_complete_month, scope_version,
       pipeline_version, source_wide_update_time
FROM internal.flywheel_gov.gov_release
ORDER BY built_at DESC
LIMIT 10
