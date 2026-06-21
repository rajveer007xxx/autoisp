-- Phase 19.2 — Smart OLT Provision schema (idempotent)
BEGIN;

-- 1. LAN columns on onus
ALTER TABLE onus ADD COLUMN IF NOT EXISTS lan_ip TEXT;
ALTER TABLE onus ADD COLUMN IF NOT EXISTS lan_netmask TEXT;
ALTER TABLE onus ADD COLUMN IF NOT EXISTS dhcp_enabled SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onus ADD COLUMN IF NOT EXISTS dhcp_start TEXT;
ALTER TABLE onus ADD COLUMN IF NOT EXISTS dhcp_end TEXT;
ALTER TABLE onus ADD COLUMN IF NOT EXISTS factory_reset_on_push SMALLINT NOT NULL DEFAULT 0;

-- 2. Extend onu_service_profiles for full Smart OLT defaults
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_ssid_5g_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_pw_5g_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_channel_24 INTEGER;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_channel_5  INTEGER;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_bw_24 TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_bw_5  TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_auto_24 SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_auto_5  SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_radio_24 SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS wifi_radio_5  SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS lan_ip_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS lan_netmask_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS dhcp_enabled SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS dhcp_start_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS dhcp_end_tpl TEXT;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS factory_reset_on_push SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE onu_service_profiles ADD COLUMN IF NOT EXISTS is_default SMALLINT NOT NULL DEFAULT 0;

-- 3. Constraint: inform interval max 60s
ALTER TABLE onu_service_profiles
  DROP CONSTRAINT IF EXISTS onu_service_profiles_inform_interval_chk;
ALTER TABLE onu_service_profiles
  ADD CONSTRAINT onu_service_profiles_inform_interval_chk
  CHECK (acs_inform_int BETWEEN 5 AND 60);

UPDATE onu_service_profiles
   SET acs_inform_int = LEAST(acs_inform_int, 60)
 WHERE acs_inform_int > 60;

-- 4. Seed a tenant-wide "Residential Standard" profile per tenant if none default
INSERT INTO onu_service_profiles
  (company_id, name, connection_type, vlan,
   wifi_ssid_tpl, wifi_pw_tpl, wifi_band_split, acs_inform_int,
   wifi_ssid_5g_tpl, wifi_pw_5g_tpl,
   wifi_channel_24, wifi_channel_5,
   wifi_bw_24, wifi_bw_5,
   lan_ip_tpl, lan_netmask_tpl,
   dhcp_enabled, dhcp_start_tpl, dhcp_end_tpl,
   factory_reset_on_push, is_default)
SELECT c.company_id, 'Residential Standard', 'pppoe', NULL,
       'FIBERNET-{mobile_last4}-2G', '{name_first4}{mobile_last4}', 1, 60,
       'FIBERNET-{mobile_last4}-5G', '{name_first4}{mobile_last4}',
       NULL, NULL,
       'Auto', 'Auto',
       '192.168.1.1', '255.255.255.0',
       1, '192.168.1.2', '192.168.1.254',
       0, 1
FROM companies c
WHERE c.company_id IS NOT NULL
  AND NOT EXISTS (
  SELECT 1 FROM onu_service_profiles p
  WHERE p.company_id = c.company_id AND p.is_default = 1
)
ON CONFLICT (company_id, name) DO NOTHING;

COMMIT;

\d onus
