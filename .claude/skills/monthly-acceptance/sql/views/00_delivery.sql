-- 交付身份：源表本身的创建/更新时间。开工记一次、收尾再记一次，变了就说明运行期间上游重建过。
SELECT TABLE_SCHEMA AS table_schema, TABLE_NAME AS table_name, CREATE_TIME AS create_time,
       UPDATE_TIME AS update_time, TABLE_ROWS AS table_rows
FROM internal.information_schema.tables
WHERE TABLE_SCHEMA = 'flywheel' AND TABLE_NAME = 'monthly_sales_wide'
