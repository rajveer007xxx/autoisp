ALTER TABLE admin_activity_log ALTER COLUMN created_at SET DEFAULT now();
UPDATE admin_activity_log SET created_at = COALESCE(created_at, now()) WHERE created_at IS NULL;

UPDATE olt_alerts SET created_at = to_char(now() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS.US"+05:30"') WHERE created_at IS NULL OR created_at = '';
ALTER TABLE olt_alerts ALTER COLUMN created_at SET DEFAULT to_char(now() AT TIME ZONE 'Asia/Kolkata', 'YYYY-MM-DD HH24:MI:SS.US"+05:30"');
