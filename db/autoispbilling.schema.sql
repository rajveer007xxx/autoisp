--
-- PostgreSQL database dump
--

\restrict NDz18aRWxDGcwv2VXxHusSuxS4RIJBMyc2X5se27kF5dPlMESBtqKxDPcOLKUnS

-- Dumped from database version 16.14 (Ubuntu 16.14-1.pgdg24.04+1)
-- Dumped by pg_dump version 16.14 (Ubuntu 16.14-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: ispbilling
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO ispbilling;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: ispbilling
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: __dummy_for_schema; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.__dummy_for_schema (
    x text
);


ALTER TABLE public.__dummy_for_schema OWNER TO ispbilling;

--
-- Name: _backup_s56t_start_date_repair; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public._backup_s56t_start_date_repair (
    id bigint,
    customer_id text,
    customer_name text,
    old_start text,
    old_end text,
    period bigint,
    repaired_at text
);


ALTER TABLE public._backup_s56t_start_date_repair OWNER TO ispbilling;

--
-- Name: access_request_logs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.access_request_logs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    username text NOT NULL,
    nas_ip text,
    request_type text,
    status text,
    reason text,
    ip_address text,
    created_at timestamp with time zone
);


ALTER TABLE public.access_request_logs OWNER TO ispbilling;

--
-- Name: access_request_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.access_request_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.access_request_logs_id_seq OWNER TO ispbilling;

--
-- Name: access_request_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.access_request_logs_id_seq OWNED BY public.access_request_logs.id;


--
-- Name: account_deletion_requests; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.account_deletion_requests (
    id bigint NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    phone text NOT NULL,
    customer_id text,
    isp text,
    scope text,
    notes text,
    status text DEFAULT 'pending'::text NOT NULL,
    ip text,
    user_agent text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    processed_at text
);


ALTER TABLE public.account_deletion_requests OWNER TO ispbilling;

--
-- Name: account_deletion_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.account_deletion_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.account_deletion_requests_id_seq OWNER TO ispbilling;

--
-- Name: account_deletion_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.account_deletion_requests_id_seq OWNED BY public.account_deletion_requests.id;


--
-- Name: acs_device_mapping; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.acs_device_mapping (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text,
    onu_serial text,
    genieacs_device_id text NOT NULL,
    manufacturer text,
    oui text,
    product_class text,
    model text,
    firmware text,
    mac_address text,
    last_inform timestamp with time zone,
    last_bootstrap timestamp with time zone,
    status text DEFAULT 'PENDING_MATCH'::text NOT NULL,
    last_push_at timestamp with time zone,
    last_push_result text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.acs_device_mapping OWNER TO ispbilling;

--
-- Name: acs_device_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.acs_device_mapping_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.acs_device_mapping_id_seq OWNER TO ispbilling;

--
-- Name: acs_device_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.acs_device_mapping_id_seq OWNED BY public.acs_device_mapping.id;


--
-- Name: acs_device_parameter_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.acs_device_parameter_profiles (
    id bigint NOT NULL,
    vendor text,
    model text,
    product_class text,
    firmware text,
    wifi_ssid_path text,
    wifi_password_path text,
    wifi_encryption_path text,
    wifi_5g_ssid_path text,
    wifi_5g_password_path text,
    wan_username_path text,
    wan_password_path text,
    wan_vlan_path text,
    wan_enable_path text,
    wan_connection_status_path text,
    wan_ipaddr_path text,
    lan_dhcp_path text,
    reboot_path text,
    factory_reset_path text,
    set_strategy text,
    notes text,
    priority integer DEFAULT 100 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.acs_device_parameter_profiles OWNER TO ispbilling;

--
-- Name: acs_device_parameter_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.acs_device_parameter_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.acs_device_parameter_profiles_id_seq OWNER TO ispbilling;

--
-- Name: acs_device_parameter_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.acs_device_parameter_profiles_id_seq OWNED BY public.acs_device_parameter_profiles.id;


--
-- Name: acs_push_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.acs_push_log (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint,
    onu_serial text,
    customer_id text,
    reason text,
    ok bigint NOT NULL,
    skip text,
    error text,
    params_json text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    olt_id bigint,
    actor text,
    action text,
    message text
);


ALTER TABLE public.acs_push_log OWNER TO ispbilling;

--
-- Name: acs_push_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.acs_push_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.acs_push_log_id_seq OWNER TO ispbilling;

--
-- Name: acs_push_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.acs_push_log_id_seq OWNED BY public.acs_push_log.id;


--
-- Name: admin_activity_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.admin_activity_log (
    id bigint NOT NULL,
    company_id text NOT NULL,
    actor_id text,
    actor_name text,
    actor_type text,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id text,
    summary text,
    payload_json text,
    ip_address text,
    created_at timestamp with time zone
);


ALTER TABLE public.admin_activity_log OWNER TO ispbilling;

--
-- Name: admin_activity_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.admin_activity_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admin_activity_log_id_seq OWNER TO ispbilling;

--
-- Name: admin_activity_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.admin_activity_log_id_seq OWNED BY public.admin_activity_log.id;


--
-- Name: admin_notification_state; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.admin_notification_state (
    company_id text NOT NULL,
    admin_id text NOT NULL,
    last_seen_at timestamp with time zone NOT NULL
);


ALTER TABLE public.admin_notification_state OWNER TO ispbilling;

--
-- Name: admins; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.admins (
    id bigint NOT NULL,
    admin_id text,
    password_hash text,
    admin_name text,
    admin_email text,
    admin_mobile text,
    company_id text,
    profile_image_path text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    deleted_at timestamp with time zone,
    deleted_by text,
    totp_secret text DEFAULT ''::text,
    totp_enabled bigint DEFAULT 0,
    totp_recovery_codes text DEFAULT ''::text,
    mfa_deadline text DEFAULT ''::text,
    seal_signature_path text,
    language_pref text DEFAULT 'en'::text
);


ALTER TABLE public.admins OWNER TO ispbilling;

--
-- Name: admins_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.admins_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admins_id_seq OWNER TO ispbilling;

--
-- Name: admins_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.admins_id_seq OWNED BY public.admins.id;


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.api_keys (
    id bigint NOT NULL,
    company_id text NOT NULL,
    label text,
    key_hash text NOT NULL,
    scopes text DEFAULT 'read'::text,
    enabled bigint DEFAULT 1,
    created_by text,
    created_at text,
    last_used text
);


ALTER TABLE public.api_keys OWNER TO ispbilling;

--
-- Name: api_keys_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.api_keys_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.api_keys_id_seq OWNER TO ispbilling;

--
-- Name: api_keys_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.api_keys_id_seq OWNED BY public.api_keys.id;


--
-- Name: bulk_push_jobs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.bulk_push_jobs (
    id text,
    company_id text,
    olt_id bigint,
    actor text,
    status text,
    started text,
    updated text,
    total bigint DEFAULT 0,
    pushed bigint DEFAULT 0,
    failed bigint DEFAULT 0,
    results text,
    error text
);


ALTER TABLE public.bulk_push_jobs OWNER TO ispbilling;

--
-- Name: captive_portal_settings; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.captive_portal_settings (
    id bigint NOT NULL,
    company_id text NOT NULL,
    title text,
    welcome_text text,
    terms_text text,
    primary_color text DEFAULT '#7c3aed'::text,
    accent_color text DEFAULT '#06b6d4'::text,
    logo_path text,
    background_path text,
    login_mode text DEFAULT 'voucher'::text,
    whatsapp_otp_enabled bigint DEFAULT 0,
    footer_text text,
    updated_at timestamp with time zone,
    post_login_redirect_url text DEFAULT ''::text,
    hotspot_portal_url text DEFAULT ''::text,
    walled_garden_hosts text DEFAULT ''::text,
    voucher_webhook_url text DEFAULT ''::text
);


ALTER TABLE public.captive_portal_settings OWNER TO ispbilling;

--
-- Name: captive_portal_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.captive_portal_settings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.captive_portal_settings_id_seq OWNER TO ispbilling;

--
-- Name: captive_portal_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.captive_portal_settings_id_seq OWNED BY public.captive_portal_settings.id;


--
-- Name: cms_mirror_config; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.cms_mirror_config (
    id bigint NOT NULL,
    company_id text NOT NULL,
    cms_endpoint text,
    cms_key text,
    mirror_nas_id bigint,
    enabled bigint DEFAULT 0,
    last_test_at timestamp with time zone,
    last_test_ok bigint DEFAULT 0,
    last_test_msg text,
    updated_at timestamp with time zone,
    updated_by text
);


ALTER TABLE public.cms_mirror_config OWNER TO ispbilling;

--
-- Name: cms_mirror_config_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.cms_mirror_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cms_mirror_config_id_seq OWNER TO ispbilling;

--
-- Name: cms_mirror_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.cms_mirror_config_id_seq OWNED BY public.cms_mirror_config.id;


--
-- Name: companies; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.companies (
    id bigint NOT NULL,
    company_id text,
    company_name text,
    company_email text,
    company_phone text,
    company_address text,
    country text,
    state text,
    city text,
    pincode text,
    gst_number text,
    bank_name text,
    account_number text,
    branch_code text,
    branch_location text,
    branch_ifsc text,
    upi_id text,
    logo_path text,
    declaration text,
    terms_conditions text,
    smtp_server text,
    smtp_port bigint,
    smtp_username text,
    smtp_password text,
    bank_qr_code text,
    package text,
    admin_type text,
    period_months bigint,
    start_date timestamp with time zone,
    end_date timestamp with time zone,
    status text,
    balance_amount double precision,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    gst_invoice_needed bigint DEFAULT 1,
    auto_renew_enabled bigint DEFAULT 1,
    last_renewal_date timestamp with time zone,
    deleted_at timestamp with time zone,
    deleted_by text,
    walled_garden_enabled bigint DEFAULT 1,
    enable_online_payment bigint DEFAULT 0,
    enable_whatsapp_api bigint DEFAULT 0,
    mfa_required_for_admins bigint DEFAULT 0,
    mfa_grace_period_days bigint DEFAULT 7,
    auto_email_invoices bigint DEFAULT 1,
    auto_email_receipts bigint DEFAULT 0,
    payment_gateway_enabled bigint DEFAULT 0,
    account_holder_name text,
    telegram_bot_token character varying(200),
    telegram_admin_chat_id character varying(80)
);


ALTER TABLE public.companies OWNER TO ispbilling;

--
-- Name: companies_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.companies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.companies_id_seq OWNER TO ispbilling;

--
-- Name: companies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.companies_id_seq OWNED BY public.companies.id;


--
-- Name: company_feature_flags; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.company_feature_flags (
    id bigint NOT NULL,
    company_id text,
    sms_enabled bigint DEFAULT 0,
    whatsapp_enabled bigint DEFAULT 1,
    dns_filter_enabled bigint DEFAULT 1,
    outage_detector_enabled bigint DEFAULT 1,
    self_upgrade_enabled bigint DEFAULT 1,
    referrals_enabled bigint DEFAULT 1,
    lead_crm_enabled bigint DEFAULT 1,
    multilang_enabled bigint DEFAULT 1,
    updated_at timestamp with time zone,
    updated_by text
);


ALTER TABLE public.company_feature_flags OWNER TO ispbilling;

--
-- Name: company_feature_flags_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.company_feature_flags_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.company_feature_flags_id_seq OWNER TO ispbilling;

--
-- Name: company_feature_flags_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.company_feature_flags_id_seq OWNED BY public.company_feature_flags.id;


--
-- Name: complaint_comments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.complaint_comments (
    id bigint NOT NULL,
    complaint_id bigint NOT NULL,
    company_id text NOT NULL,
    author text,
    text text NOT NULL,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL
);


ALTER TABLE public.complaint_comments OWNER TO ispbilling;

--
-- Name: complaint_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.complaint_comments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.complaint_comments_id_seq OWNER TO ispbilling;

--
-- Name: complaint_comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.complaint_comments_id_seq OWNED BY public.complaint_comments.id;


--
-- Name: complaint_responses; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.complaint_responses (
    id bigint NOT NULL,
    complaint_id bigint NOT NULL,
    responder_role text NOT NULL,
    responder_id text NOT NULL,
    message text NOT NULL,
    created_at timestamp with time zone
);


ALTER TABLE public.complaint_responses OWNER TO ispbilling;

--
-- Name: complaint_responses_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.complaint_responses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.complaint_responses_id_seq OWNER TO ispbilling;

--
-- Name: complaint_responses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.complaint_responses_id_seq OWNED BY public.complaint_responses.id;


--
-- Name: complaints; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.complaints (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text,
    ticket_no text NOT NULL,
    complaint_type text NOT NULL,
    priority text,
    subject text,
    description text NOT NULL,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    resolved_at timestamp with time zone,
    resolved_by text,
    kind text DEFAULT 'Complaint'::text,
    source text DEFAULT 'admin'::text,
    target_role text DEFAULT 'admin'::text,
    sla_minutes bigint DEFAULT 240,
    escalation_level bigint DEFAULT 0,
    escalated_at timestamp with time zone,
    sla_breached bigint DEFAULT 0,
    assigned_to_kind text,
    assigned_to_id text,
    assigned_to_name text
);


ALTER TABLE public.complaints OWNER TO ispbilling;

--
-- Name: complaints_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.complaints_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.complaints_id_seq OWNER TO ispbilling;

--
-- Name: complaints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.complaints_id_seq OWNED BY public.complaints.id;


--
-- Name: compliance_ingest_tokens; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.compliance_ingest_tokens (
    id bigint NOT NULL,
    company_id text NOT NULL,
    kind text NOT NULL,
    token_hash text NOT NULL,
    label text,
    created_at timestamp with time zone,
    last_used_at timestamp with time zone,
    revoked bigint DEFAULT 0
);


ALTER TABLE public.compliance_ingest_tokens OWNER TO ispbilling;

--
-- Name: compliance_ingest_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.compliance_ingest_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.compliance_ingest_tokens_id_seq OWNER TO ispbilling;

--
-- Name: compliance_ingest_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.compliance_ingest_tokens_id_seq OWNED BY public.compliance_ingest_tokens.id;


--
-- Name: connection_requests; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.connection_requests (
    id bigint NOT NULL,
    company_id text,
    full_name text,
    phone text,
    email text,
    address text,
    locality text,
    preferred_plan_id bigint,
    source text,
    notes text,
    pipeline_stage text DEFAULT 'new'::text,
    assigned_to text,
    assigned_role text,
    customer_id text,
    approved_at timestamp with time zone,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.connection_requests OWNER TO ispbilling;

--
-- Name: connection_requests_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.connection_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.connection_requests_id_seq OWNER TO ispbilling;

--
-- Name: connection_requests_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.connection_requests_id_seq OWNED BY public.connection_requests.id;


--
-- Name: customer_status_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.customer_status_log (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    username text,
    old_status text,
    new_status text,
    actor_type text,
    actor_id text,
    actor_name text,
    reason text,
    balance_at_change double precision,
    created_at timestamp with time zone
);


ALTER TABLE public.customer_status_log OWNER TO ispbilling;

--
-- Name: customer_status_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.customer_status_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.customer_status_log_id_seq OWNER TO ispbilling;

--
-- Name: customer_status_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.customer_status_log_id_seq OWNED BY public.customer_status_log.id;


--
-- Name: customers; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.customers (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    password_hash text,
    registration_type text,
    service_type text,
    username text,
    customer_name text,
    nickname text,
    customer_email text,
    customer_phone text,
    alt_mobile text,
    gst_invoice_needed text,
    customer_gst_no text,
    id_proof text,
    id_proof_no text,
    installation_date text,
    address text,
    locality text,
    city text,
    state text,
    pincode text,
    plan_id bigint,
    monthly_amount double precision,
    auto_renew text,
    customer_type text,
    caf_no text,
    mac_address text,
    ip_address text,
    vendor text,
    modem_no text,
    start_date text,
    period bigint,
    end_date text,
    bill_amount double precision,
    cgst_tax double precision,
    sgst_tax double precision,
    igst_tax double precision,
    total_bill_amount double precision,
    payment_mode text,
    received_amount double precision,
    security_deposit double precision,
    installation_charges double precision,
    router_charges double precision,
    transaction_id text,
    payment_notes text,
    discount_credit double precision,
    status text,
    created_at timestamp with time zone,
    caf_pdf bytea,
    updated_at timestamp with time zone,
    location_id bigint,
    billing_address text,
    billing_locality text,
    billing_city text,
    billing_state text,
    billing_pincode text,
    photograph_path text,
    address_proof_path text,
    id_proof_doc_path text,
    provisioned_nas_id bigint,
    pppoe_password text DEFAULT ''::text,
    auth_type text DEFAULT 'pppoe'::text,
    static_ip_address text DEFAULT ''::text,
    static_netmask text DEFAULT '255.255.255.0'::text,
    hotspot_session_timeout bigint DEFAULT 0,
    hotspot_idle_timeout bigint DEFAULT 600,
    zone text DEFAULT ''::text,
    node text DEFAULT ''::text,
    distance_from_node text DEFAULT ''::text,
    fix_ip_address text DEFAULT 'No'::text,
    bind_mac_address text DEFAULT 'No'::text,
    allow_devices bigint DEFAULT 1,
    reset_mac_flag text DEFAULT 'No'::text,
    leased_line_subscriber text DEFAULT 'No'::text,
    do_not_send_sms text DEFAULT 'No'::text,
    do_not_send_whatsapp text DEFAULT 'No'::text,
    do_not_send_email text DEFAULT 'No'::text,
    custom_rate_limit text,
    sub_lco_id bigint,
    latitude double precision,
    longitude double precision,
    created_by_employee_id bigint,
    dns_profile_id bigint,
    language_pref text DEFAULT 'en'::text,
    lat double precision,
    lng double precision,
    location_source text DEFAULT 'address'::text,
    geocoded_at timestamp with time zone,
    geocode_confidence double precision,
    created_by text,
    created_by_role text,
    created_by_sub_lco_id bigint,
    show_data_usage bigint DEFAULT 0 NOT NULL,
    vlan_enabled bigint DEFAULT 0 NOT NULL,
    vlan_id bigint
);


ALTER TABLE public.customers OWNER TO ispbilling;

--
-- Name: customers_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.customers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.customers_id_seq OWNER TO ispbilling;

--
-- Name: customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;


--
-- Name: data_mgmt_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.data_mgmt_log (
    id bigint NOT NULL,
    company_id text,
    action text,
    kind text,
    records bigint DEFAULT 0,
    status text,
    filename text,
    message text,
    created_by text,
    created_at timestamp with time zone
);


ALTER TABLE public.data_mgmt_log OWNER TO ispbilling;

--
-- Name: data_mgmt_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.data_mgmt_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.data_mgmt_log_id_seq OWNER TO ispbilling;

--
-- Name: data_mgmt_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.data_mgmt_log_id_seq OWNED BY public.data_mgmt_log.id;


--
-- Name: db_backups; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.db_backups (
    id bigint NOT NULL,
    filename text,
    size_bytes bigint,
    sha256 text,
    created_at timestamp with time zone,
    created_by text,
    trigger text DEFAULT 'auto'::text
);


ALTER TABLE public.db_backups OWNER TO ispbilling;

--
-- Name: db_backups_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.db_backups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.db_backups_id_seq OWNER TO ispbilling;

--
-- Name: db_backups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.db_backups_id_seq OWNED BY public.db_backups.id;


--
-- Name: dns_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.dns_profiles (
    id bigint NOT NULL,
    company_id text,
    name text,
    description text,
    upstream_v4 text,
    upstream_v6 text,
    block_categories text,
    is_default bigint DEFAULT 0,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.dns_profiles OWNER TO ispbilling;

--
-- Name: dns_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.dns_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dns_profiles_id_seq OWNER TO ispbilling;

--
-- Name: dns_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.dns_profiles_id_seq OWNED BY public.dns_profiles.id;


--
-- Name: dot_blocklist; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.dot_blocklist (
    id bigint NOT NULL,
    company_id text NOT NULL,
    target text NOT NULL,
    dot_ref text,
    notes text,
    added_by text,
    added_at timestamp with time zone
);


ALTER TABLE public.dot_blocklist OWNER TO ispbilling;

--
-- Name: dot_blocklist_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.dot_blocklist_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dot_blocklist_id_seq OWNER TO ispbilling;

--
-- Name: dot_blocklist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.dot_blocklist_id_seq OWNED BY public.dot_blocklist.id;


--
-- Name: employee_locality_assignments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.employee_locality_assignments (
    id bigint NOT NULL,
    company_id text NOT NULL,
    employee_id bigint NOT NULL,
    location_id bigint NOT NULL,
    connection_type text NOT NULL,
    active smallint,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    created_by text,
    updated_by text
);


ALTER TABLE public.employee_locality_assignments OWNER TO ispbilling;

--
-- Name: employee_locality_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.employee_locality_assignments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employee_locality_assignments_id_seq OWNER TO ispbilling;

--
-- Name: employee_locality_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.employee_locality_assignments_id_seq OWNED BY public.employee_locality_assignments.id;


--
-- Name: employee_location_history; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.employee_location_history (
    id bigint NOT NULL,
    employee_id bigint NOT NULL,
    company_id text NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    accuracy double precision,
    recorded_at text NOT NULL
);


ALTER TABLE public.employee_location_history OWNER TO ispbilling;

--
-- Name: employee_location_history_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.employee_location_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employee_location_history_id_seq OWNER TO ispbilling;

--
-- Name: employee_location_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.employee_location_history_id_seq OWNED BY public.employee_location_history.id;


--
-- Name: employee_permissions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.employee_permissions (
    id bigint NOT NULL,
    employee_id bigint NOT NULL,
    permission_id bigint NOT NULL,
    granted_at timestamp with time zone,
    granted_by text
);


ALTER TABLE public.employee_permissions OWNER TO ispbilling;

--
-- Name: employee_permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.employee_permissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employee_permissions_id_seq OWNER TO ispbilling;

--
-- Name: employee_permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.employee_permissions_id_seq OWNED BY public.employee_permissions.id;


--
-- Name: employee_sequences; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.employee_sequences (
    id bigint NOT NULL,
    company_id text,
    next_number bigint,
    updated_at timestamp with time zone
);


ALTER TABLE public.employee_sequences OWNER TO ispbilling;

--
-- Name: employee_sequences_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.employee_sequences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employee_sequences_id_seq OWNER TO ispbilling;

--
-- Name: employee_sequences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.employee_sequences_id_seq OWNED BY public.employee_sequences.id;


--
-- Name: employees; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.employees (
    id bigint NOT NULL,
    company_id text NOT NULL,
    employee_code text NOT NULL,
    employee_name text NOT NULL,
    password_hash text NOT NULL,
    mobile text NOT NULL,
    email text,
    address text,
    profile_image_path text,
    status text,
    is_deleted boolean DEFAULT false,
    last_latitude double precision,
    last_longitude double precision,
    last_seen_at timestamp with time zone,
    created_by text,
    updated_by text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    terms_conditions text,
    declaration text,
    inv_company_name text,
    inv_company_gst text,
    inv_company_phone text,
    inv_company_email text,
    inv_company_address text,
    inv_bank_name text,
    inv_account_number text,
    inv_branch_ifsc text,
    inv_branch_location text,
    inv_upi_id text,
    sub_lco_id bigint
);


ALTER TABLE public.employees OWNER TO ispbilling;

--
-- Name: employees_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.employees_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employees_id_seq OWNER TO ispbilling;

--
-- Name: employees_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.employees_id_seq OWNED BY public.employees.id;


--
-- Name: expenses; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.expenses (
    id bigint NOT NULL,
    company_id text NOT NULL,
    expense_date text NOT NULL,
    category text NOT NULL,
    sub_category text,
    amount double precision DEFAULT 0 NOT NULL,
    payment_mode text,
    vendor text,
    paid_to text,
    description text,
    attachment text,
    created_by text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    deleted_at timestamp with time zone,
    sub_lco_id bigint,
    employee_id text
);


ALTER TABLE public.expenses OWNER TO ispbilling;

--
-- Name: expenses_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.expenses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.expenses_id_seq OWNER TO ispbilling;

--
-- Name: expenses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.expenses_id_seq OWNED BY public.expenses.id;


--
-- Name: fiber_cut_history; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.fiber_cut_history (
    id bigint NOT NULL,
    company_id text NOT NULL,
    node_hw_id bigint NOT NULL,
    original_fiber_id bigint NOT NULL,
    first_half_id bigint NOT NULL,
    second_half_id bigint NOT NULL,
    cut_lat double precision,
    cut_lng double precision,
    cut_by text,
    cut_at text,
    notes text
);


ALTER TABLE public.fiber_cut_history OWNER TO ispbilling;

--
-- Name: fiber_cut_history_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.fiber_cut_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.fiber_cut_history_id_seq OWNER TO ispbilling;

--
-- Name: fiber_cut_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.fiber_cut_history_id_seq OWNED BY public.fiber_cut_history.id;


--
-- Name: fiber_splice; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.fiber_splice (
    id bigint NOT NULL,
    company_id text NOT NULL,
    node_hw_id bigint NOT NULL,
    src_fiber_id bigint,
    src_core bigint,
    dst_fiber_id bigint,
    dst_core bigint,
    mode text DEFAULT 'thru'::text,
    loss_db double precision,
    notes text,
    created_by text,
    created_at text
);


ALTER TABLE public.fiber_splice OWNER TO ispbilling;

--
-- Name: fiber_splice_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.fiber_splice_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.fiber_splice_id_seq OWNER TO ispbilling;

--
-- Name: fiber_splice_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.fiber_splice_id_seq OWNED BY public.fiber_splice.id;


--
-- Name: geofence_events; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.geofence_events (
    id bigint NOT NULL,
    company_id text NOT NULL,
    geofence_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    event_type text NOT NULL,
    latitude double precision,
    longitude double precision,
    recorded_at timestamp with time zone
);


ALTER TABLE public.geofence_events OWNER TO ispbilling;

--
-- Name: geofence_events_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.geofence_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.geofence_events_id_seq OWNER TO ispbilling;

--
-- Name: geofence_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.geofence_events_id_seq OWNED BY public.geofence_events.id;


--
-- Name: geofences; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.geofences (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    radius_m double precision DEFAULT 200 NOT NULL,
    color text DEFAULT '#0ea5e9'::text,
    is_active bigint DEFAULT 1,
    notify_on_enter bigint DEFAULT 1,
    notify_on_exit bigint DEFAULT 1,
    created_at timestamp with time zone,
    deleted_at timestamp with time zone
);


ALTER TABLE public.geofences OWNER TO ispbilling;

--
-- Name: geofences_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.geofences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.geofences_id_seq OWNER TO ispbilling;

--
-- Name: geofences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.geofences_id_seq OWNED BY public.geofences.id;


--
-- Name: hotspot_vouchers; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.hotspot_vouchers (
    id bigint NOT NULL,
    company_id text NOT NULL,
    batch_id text NOT NULL,
    code text NOT NULL,
    plan_id bigint,
    plan_name text,
    duration_minutes bigint DEFAULT 0,
    data_cap_mb bigint DEFAULT 0,
    status text DEFAULT 'unused'::text,
    used_by text,
    used_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone,
    created_by text
);


ALTER TABLE public.hotspot_vouchers OWNER TO ispbilling;

--
-- Name: hotspot_vouchers_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.hotspot_vouchers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.hotspot_vouchers_id_seq OWNER TO ispbilling;

--
-- Name: hotspot_vouchers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.hotspot_vouchers_id_seq OWNED BY public.hotspot_vouchers.id;


--
-- Name: invoice_reminder_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.invoice_reminder_log (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text NOT NULL,
    invoice_no text NOT NULL,
    stage bigint NOT NULL,
    sent_at timestamp with time zone NOT NULL,
    email text,
    dry_run bigint DEFAULT 0
);


ALTER TABLE public.invoice_reminder_log OWNER TO ispbilling;

--
-- Name: invoice_reminder_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.invoice_reminder_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoice_reminder_log_id_seq OWNER TO ispbilling;

--
-- Name: invoice_reminder_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.invoice_reminder_log_id_seq OWNED BY public.invoice_reminder_log.id;


--
-- Name: invoice_sequences; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.invoice_sequences (
    id bigint NOT NULL,
    company_id text,
    next_number bigint,
    updated_at timestamp with time zone
);


ALTER TABLE public.invoice_sequences OWNER TO ispbilling;

--
-- Name: invoice_sequences_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.invoice_sequences_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoice_sequences_id_seq OWNER TO ispbilling;

--
-- Name: invoice_sequences_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.invoice_sequences_id_seq OWNED BY public.invoice_sequences.id;


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.invoices (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    invoice_no text,
    issue_date text,
    due_date text,
    start_date text,
    end_date text,
    period_months bigint,
    plan_id bigint,
    plan_name text,
    base_amount double precision,
    cgst_tax double precision,
    sgst_tax double precision,
    igst_tax double precision,
    total_amount double precision,
    pdf_path text,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    has_gst_no bigint,
    due_at_issue double precision,
    manual_customer_name text,
    manual_customer_email text,
    manual_customer_phone text
);


ALTER TABLE public.invoices OWNER TO ispbilling;

--
-- Name: invoices_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.invoices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.invoices_id_seq OWNER TO ispbilling;

--
-- Name: invoices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.invoices_id_seq OWNED BY public.invoices.id;


--
-- Name: ip_pools; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ip_pools (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    network text NOT NULL,
    gateway text,
    start_ip text,
    end_ip text,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    role text DEFAULT 'PPPoE'::text,
    dns_servers text DEFAULT '8.8.8.8,1.1.1.1'::text,
    comment text DEFAULT ''::text,
    next_pool_id bigint
);


ALTER TABLE public.ip_pools OWNER TO ispbilling;

--
-- Name: ip_pools_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ip_pools_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ip_pools_id_seq OWNER TO ispbilling;

--
-- Name: ip_pools_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ip_pools_id_seq OWNED BY public.ip_pools.id;


--
-- Name: ipdr_records; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ipdr_records (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text,
    user_ip text,
    src_ip text,
    src_port bigint,
    dst_ip text,
    dst_port bigint,
    protocol text,
    start_ts timestamp with time zone,
    stop_ts timestamp with time zone,
    bytes_in bigint DEFAULT 0,
    bytes_out bigint DEFAULT 0,
    nas_ip text,
    created_at timestamp with time zone
);


ALTER TABLE public.ipdr_records OWNER TO ispbilling;

--
-- Name: ipdr_records_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ipdr_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ipdr_records_id_seq OWNER TO ispbilling;

--
-- Name: ipdr_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ipdr_records_id_seq OWNED BY public.ipdr_records.id;


--
-- Name: iptv_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.iptv_profiles (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    mcast_vlan bigint NOT NULL,
    igmp_version bigint DEFAULT 2,
    mcast_group text,
    description text,
    created_at text
);


ALTER TABLE public.iptv_profiles OWNER TO ispbilling;

--
-- Name: iptv_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.iptv_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.iptv_profiles_id_seq OWNER TO ispbilling;

--
-- Name: iptv_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.iptv_profiles_id_seq OWNED BY public.iptv_profiles.id;


--
-- Name: li_orders; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.li_orders (
    id bigint NOT NULL,
    company_id text NOT NULL,
    order_no text NOT NULL,
    agency text NOT NULL,
    target_username text NOT NULL,
    ordered_on text,
    ends_on text,
    destroy_at text,
    status text DEFAULT 'active'::text NOT NULL,
    notes text,
    created_by text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL
);


ALTER TABLE public.li_orders OWNER TO ispbilling;

--
-- Name: li_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.li_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.li_orders_id_seq OWNER TO ispbilling;

--
-- Name: li_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.li_orders_id_seq OWNED BY public.li_orders.id;


--
-- Name: load_balancing_configs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.load_balancing_configs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint NOT NULL,
    wan1_iface text NOT NULL,
    wan1_ip text DEFAULT ''::text,
    wan1_gateway text NOT NULL,
    wan2_iface text NOT NULL,
    wan2_ip text NOT NULL,
    wan2_gateway text NOT NULL,
    lan_iface text NOT NULL,
    strategy text DEFAULT 'pcc_balanced'::text NOT NULL,
    weight1 bigint DEFAULT 50,
    weight2 bigint DEFAULT 50,
    dns text DEFAULT ''::text,
    status text DEFAULT 'Active'::text,
    last_backup text DEFAULT ''::text,
    last_applied text DEFAULT ''::text,
    last_error text DEFAULT ''::text,
    created_at text
);


ALTER TABLE public.load_balancing_configs OWNER TO ispbilling;

--
-- Name: load_balancing_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.load_balancing_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.load_balancing_configs_id_seq OWNER TO ispbilling;

--
-- Name: load_balancing_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.load_balancing_configs_id_seq OWNED BY public.load_balancing_configs.id;


--
-- Name: locations; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.locations (
    id bigint NOT NULL,
    company_id text,
    name text,
    city text,
    state text,
    pincode text,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    sub_lco_id bigint
);


ALTER TABLE public.locations OWNER TO ispbilling;

--
-- Name: locations_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.locations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.locations_id_seq OWNER TO ispbilling;

--
-- Name: locations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.locations_id_seq OWNED BY public.locations.id;


--
-- Name: lock_secrets; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.lock_secrets (
    id bigint NOT NULL,
    company_id text NOT NULL,
    password_hash text NOT NULL,
    algo text DEFAULT 'sha256'::text,
    set_at timestamp with time zone,
    set_by text
);


ALTER TABLE public.lock_secrets OWNER TO ispbilling;

--
-- Name: lock_secrets_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.lock_secrets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.lock_secrets_id_seq OWNER TO ispbilling;

--
-- Name: lock_secrets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.lock_secrets_id_seq OWNED BY public.lock_secrets.id;


--
-- Name: login_events; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.login_events (
    id bigint NOT NULL,
    actor_type text NOT NULL,
    actor_id text NOT NULL,
    company_id text,
    actor_name text,
    ip_address text,
    user_agent text,
    login_at timestamp with time zone NOT NULL
);


ALTER TABLE public.login_events OWNER TO ispbilling;

--
-- Name: login_events_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.login_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.login_events_id_seq OWNER TO ispbilling;

--
-- Name: login_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.login_events_id_seq OWNED BY public.login_events.id;


--
-- Name: mdu_buildings; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mdu_buildings (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    address text,
    lat double precision,
    lng double precision,
    owner_type text DEFAULT 'residential'::text,
    floors_count bigint DEFAULT 1,
    units_per_floor bigint DEFAULT 1,
    ref_hw_id bigint,
    notes text,
    created_by text,
    created_at text
);


ALTER TABLE public.mdu_buildings OWNER TO ispbilling;

--
-- Name: mdu_buildings_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.mdu_buildings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.mdu_buildings_id_seq OWNER TO ispbilling;

--
-- Name: mdu_buildings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.mdu_buildings_id_seq OWNED BY public.mdu_buildings.id;


--
-- Name: mdu_floors; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mdu_floors (
    id bigint NOT NULL,
    company_id text NOT NULL,
    building_id bigint NOT NULL,
    floor_number bigint NOT NULL,
    label text
);


ALTER TABLE public.mdu_floors OWNER TO ispbilling;

--
-- Name: mdu_floors_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.mdu_floors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.mdu_floors_id_seq OWNER TO ispbilling;

--
-- Name: mdu_floors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.mdu_floors_id_seq OWNED BY public.mdu_floors.id;


--
-- Name: mdu_unit_customers; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mdu_unit_customers (
    id bigint NOT NULL,
    company_id text NOT NULL,
    unit_id bigint NOT NULL,
    customer_id text NOT NULL,
    active bigint DEFAULT 1,
    linked_at text,
    ended_at text
);


ALTER TABLE public.mdu_unit_customers OWNER TO ispbilling;

--
-- Name: mdu_unit_customers_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.mdu_unit_customers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.mdu_unit_customers_id_seq OWNER TO ispbilling;

--
-- Name: mdu_unit_customers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.mdu_unit_customers_id_seq OWNED BY public.mdu_unit_customers.id;


--
-- Name: mdu_units; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mdu_units (
    id bigint NOT NULL,
    company_id text NOT NULL,
    floor_id bigint NOT NULL,
    unit_label text NOT NULL,
    unit_type text DEFAULT 'apartment'::text,
    ref_onu_id bigint,
    ref_hw_id bigint,
    notes text
);


ALTER TABLE public.mdu_units OWNER TO ispbilling;

--
-- Name: mdu_units_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.mdu_units_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.mdu_units_id_seq OWNER TO ispbilling;

--
-- Name: mdu_units_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.mdu_units_id_seq OWNED BY public.mdu_units.id;


--
-- Name: mobile_login_banners; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mobile_login_banners (
    id bigint NOT NULL,
    title text NOT NULL,
    message text,
    image_url text,
    link_url text,
    is_popup bigint DEFAULT 0 NOT NULL,
    start_date text,
    end_date text,
    active bigint DEFAULT 1 NOT NULL,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    created_by text
);


ALTER TABLE public.mobile_login_banners OWNER TO ispbilling;

--
-- Name: mobile_login_banners_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.mobile_login_banners_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.mobile_login_banners_id_seq OWNER TO ispbilling;

--
-- Name: mobile_login_banners_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.mobile_login_banners_id_seq OWNED BY public.mobile_login_banners.id;


--
-- Name: mobile_sessions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mobile_sessions (
    token text,
    user_type text NOT NULL,
    company_id text,
    user_pk text,
    username text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    last_seen text,
    expires_at text,
    device_info text
);


ALTER TABLE public.mobile_sessions OWNER TO ispbilling;

--
-- Name: mobile_sso_tokens; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.mobile_sso_tokens (
    token text,
    user_type text NOT NULL,
    company_id text NOT NULL,
    user_pk text NOT NULL,
    username text NOT NULL,
    target text,
    consumed bigint DEFAULT 0 NOT NULL,
    expires_at text NOT NULL,
    created_at text DEFAULT now() NOT NULL
);


ALTER TABLE public.mobile_sso_tokens OWNER TO ispbilling;

--
-- Name: nas_compliance_deployments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.nas_compliance_deployments (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint NOT NULL,
    kind text NOT NULL,
    token_id bigint,
    endpoint text,
    generated_at timestamp with time zone,
    generated_by text
);


ALTER TABLE public.nas_compliance_deployments OWNER TO ispbilling;

--
-- Name: nas_compliance_deployments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.nas_compliance_deployments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.nas_compliance_deployments_id_seq OWNER TO ispbilling;

--
-- Name: nas_compliance_deployments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.nas_compliance_deployments_id_seq OWNED BY public.nas_compliance_deployments.id;


--
-- Name: nas_devices; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.nas_devices (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    ip_address text NOT NULL,
    secret text,
    type text,
    location text,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    api_username text DEFAULT 'admin'::text,
    api_password text DEFAULT ''::text,
    port bigint DEFAULT 8728,
    use_tls bigint DEFAULT 0,
    use_ssh bigint DEFAULT 0,
    ssh_port bigint DEFAULT 22,
    provisioned_at timestamp with time zone,
    last_status text DEFAULT 'unknown'::text,
    last_status_msg text DEFAULT ''::text,
    pppoe_pool_name text DEFAULT 'pppoe-pool'::text,
    auth_modes text DEFAULT 'pppoe'::text,
    pppoe_service_name text DEFAULT ''::text,
    pppoe_interface text DEFAULT 'ether2'::text,
    pppoe_vlan_id bigint DEFAULT 0,
    hotspot_interface text DEFAULT 'ether3'::text,
    hotspot_dns_name text DEFAULT ''::text,
    hotspot_pool_name text DEFAULT 'hotspot-pool'::text,
    walled_garden_hosts text DEFAULT ''::text,
    pinned_wan_iface text DEFAULT ''::text,
    wg_tunnel_ip text,
    wg_peer_pubkey text,
    wg_peer_privkey text,
    wg_provisioned_at text
);


ALTER TABLE public.nas_devices OWNER TO ispbilling;

--
-- Name: nas_devices_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.nas_devices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.nas_devices_id_seq OWNER TO ispbilling;

--
-- Name: nas_devices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.nas_devices_id_seq OWNED BY public.nas_devices.id;


--
-- Name: nat_configs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.nat_configs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint NOT NULL,
    name text DEFAULT ''::text,
    nat_pool_id bigint DEFAULT 0,
    nat_network text NOT NULL,
    nat_range text DEFAULT ''::text,
    interface text NOT NULL,
    action text DEFAULT 'masquerade'::text NOT NULL,
    source_pool_id bigint DEFAULT 0,
    source_address text DEFAULT ''::text,
    bind_address bigint DEFAULT 1,
    status text DEFAULT 'Active'::text,
    last_applied text DEFAULT ''::text,
    last_error text DEFAULT ''::text,
    pushed_summary text DEFAULT ''::text,
    created_at text,
    place_at_top bigint DEFAULT 1,
    auto_disable_masq bigint DEFAULT 0,
    pcc_enabled bigint DEFAULT 0
);


ALTER TABLE public.nat_configs OWNER TO ispbilling;

--
-- Name: nat_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.nat_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.nat_configs_id_seq OWNER TO ispbilling;

--
-- Name: nat_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.nat_configs_id_seq OWNED BY public.nat_configs.id;


--
-- Name: nat_one_to_one_pairs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.nat_one_to_one_pairs (
    id bigint NOT NULL,
    nat_config_id bigint NOT NULL,
    company_id text NOT NULL,
    customer_pk bigint DEFAULT 0,
    customer_username text DEFAULT ''::text,
    customer_name text DEFAULT ''::text,
    private_ip text NOT NULL,
    public_ip text NOT NULL,
    status text DEFAULT 'Active'::text,
    last_error text DEFAULT ''::text,
    created_at text
);


ALTER TABLE public.nat_one_to_one_pairs OWNER TO ispbilling;

--
-- Name: nat_one_to_one_pairs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.nat_one_to_one_pairs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.nat_one_to_one_pairs_id_seq OWNER TO ispbilling;

--
-- Name: nat_one_to_one_pairs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.nat_one_to_one_pairs_id_seq OWNED BY public.nat_one_to_one_pairs.id;


--
-- Name: network_fiber; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.network_fiber (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text,
    color text DEFAULT 'blue'::text,
    core_count bigint DEFAULT 12,
    src_hw_id bigint,
    dst_hw_id bigint,
    polyline_json text,
    length_m double precision,
    created_by text,
    created_at text,
    props_json text
);


ALTER TABLE public.network_fiber OWNER TO ispbilling;

--
-- Name: network_fiber_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.network_fiber_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.network_fiber_id_seq OWNER TO ispbilling;

--
-- Name: network_fiber_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.network_fiber_id_seq OWNED BY public.network_fiber.id;


--
-- Name: network_hardware; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.network_hardware (
    id bigint NOT NULL,
    company_id text NOT NULL,
    kind text NOT NULL,
    name text,
    lat double precision NOT NULL,
    lng double precision NOT NULL,
    ref_olt_id bigint,
    ref_onu_id bigint,
    parent_id bigint,
    props_json text,
    created_by text,
    created_at text
);


ALTER TABLE public.network_hardware OWNER TO ispbilling;

--
-- Name: network_hardware_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.network_hardware_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.network_hardware_id_seq OWNER TO ispbilling;

--
-- Name: network_hardware_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.network_hardware_id_seq OWNED BY public.network_hardware.id;


--
-- Name: network_seed_markers; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.network_seed_markers (
    id bigint NOT NULL,
    company_id text NOT NULL,
    module text NOT NULL,
    seeded_at timestamp with time zone
);


ALTER TABLE public.network_seed_markers OWNER TO ispbilling;

--
-- Name: network_seed_markers_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.network_seed_markers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.network_seed_markers_id_seq OWNER TO ispbilling;

--
-- Name: network_seed_markers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.network_seed_markers_id_seq OWNED BY public.network_seed_markers.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.notifications (
    id bigint NOT NULL,
    title text,
    message text,
    notification_type text,
    related_entity_type text,
    related_entity_id text,
    is_read smallint,
    created_at timestamp with time zone,
    read_at timestamp with time zone
);


ALTER TABLE public.notifications OWNER TO ispbilling;

--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.notifications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.notifications_id_seq OWNER TO ispbilling;

--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: olt_alerts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.olt_alerts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    olt_id bigint,
    onu_id bigint,
    kind text NOT NULL,
    level text DEFAULT 'warn'::text NOT NULL,
    title text NOT NULL,
    message text,
    meta_json text,
    acked bigint DEFAULT 0 NOT NULL,
    acked_by text,
    acked_at text,
    created_at text
);


ALTER TABLE public.olt_alerts OWNER TO ispbilling;

--
-- Name: olt_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.olt_alerts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.olt_alerts_id_seq OWNER TO ispbilling;

--
-- Name: olt_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.olt_alerts_id_seq OWNED BY public.olt_alerts.id;


--
-- Name: olt_polls; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.olt_polls (
    id bigint NOT NULL,
    olt_id bigint NOT NULL,
    ts text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text),
    ok bigint DEFAULT 1,
    total_onus bigint DEFAULT 0,
    online_onus bigint DEFAULT 0,
    cpu_pct double precision DEFAULT 0,
    avg_rx double precision,
    error text
);


ALTER TABLE public.olt_polls OWNER TO ispbilling;

--
-- Name: olt_polls_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.olt_polls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.olt_polls_id_seq OWNER TO ispbilling;

--
-- Name: olt_polls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.olt_polls_id_seq OWNED BY public.olt_polls.id;


--
-- Name: olt_settings; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.olt_settings (
    company_id text,
    rx_warn_dbm double precision DEFAULT '-25'::integer,
    rx_crit_dbm double precision DEFAULT '-28'::integer,
    fiber_cut_pct double precision DEFAULT 50,
    fiber_cut_min bigint DEFAULT 5,
    poll_interval bigint DEFAULT 60,
    wa_enabled bigint DEFAULT 1,
    wa_target text,
    email_enabled bigint DEFAULT 0,
    email_target text,
    updated_at text,
    genieacs_url text,
    genieacs_username text,
    genieacs_password text,
    genieacs_auto_provision bigint DEFAULT 1,
    tr069_acs_url text,
    signal_drop_threshold_db numeric(5,2) DEFAULT 3.0 NOT NULL,
    signal_critical_threshold_dbm numeric(5,2) DEFAULT '-27.0'::numeric NOT NULL
);


ALTER TABLE public.olt_settings OWNER TO ispbilling;

--
-- Name: olts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.olts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    vendor text DEFAULT 'mock'::text NOT NULL,
    model text,
    host text NOT NULL,
    snmp_port bigint DEFAULT 161,
    snmp_community text DEFAULT 'public'::text,
    snmp_version text DEFAULT 'v2c'::text,
    cli_port bigint DEFAULT 23,
    cli_username text,
    cli_password text,
    location text,
    poll_interval bigint DEFAULT 60,
    enabled bigint DEFAULT 1 NOT NULL,
    status text DEFAULT 'unknown'::text,
    uptime_sec bigint DEFAULT 0,
    cpu_pct double precision DEFAULT 0,
    mem_pct double precision DEFAULT 0,
    temp_c double precision DEFAULT 0,
    total_onus bigint DEFAULT 0,
    online_onus bigint DEFAULT 0,
    last_polled text,
    last_seen_up text,
    created_at text,
    created_by text,
    pon_type text DEFAULT 'GPON'::text,
    latitude double precision,
    longitude double precision,
    connection_mode text DEFAULT 'public'::text NOT NULL,
    vpn_address text,
    vpn_peer_pubkey text,
    vpn_peer_privkey text,
    vpn_psk text,
    telnet_port bigint DEFAULT 23,
    ssh_port bigint DEFAULT 22,
    web_port bigint DEFAULT 80,
    pon_port_count bigint DEFAULT 16,
    uplink_port_count bigint DEFAULT 16,
    olt_tech text DEFAULT 'GPON'::text,
    scan_profile text DEFAULT 'Generic'::text,
    alert_unit_offline bigint DEFAULT 1,
    alert_signal_critical bigint DEFAULT 1,
    alert_signal_warning bigint DEFAULT 1,
    alert_high_power bigint DEFAULT 1,
    alert_uplink_down bigint DEFAULT 1,
    telegram_bot_token text DEFAULT ''::text,
    telegram_chat_id text DEFAULT ''::text,
    whatsapp_instance_id text DEFAULT ''::text,
    whatsapp_api_key_enc text DEFAULT ''::text,
    vpn_type text DEFAULT 'none'::text,
    vpn_username text DEFAULT ''::text,
    vpn_password_enc text DEFAULT ''::text,
    vpn_endpoint text DEFAULT ''::text,
    vpn_config_enc text DEFAULT ''::text,
    last_telnet_at text,
    telnet_interval_sec bigint DEFAULT 300,
    parent_nas_id bigint
);


ALTER TABLE public.olts OWNER TO ispbilling;

--
-- Name: olts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.olts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.olts_id_seq OWNER TO ispbilling;

--
-- Name: olts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.olts_id_seq OWNED BY public.olts.id;


--
-- Name: online_users; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.online_users (
    id bigint NOT NULL,
    company_id text NOT NULL,
    username text NOT NULL,
    ip_address text,
    nas_ip text,
    session_id text,
    framed_protocol text,
    started_at timestamp with time zone,
    uptime_seconds bigint,
    bytes_in double precision,
    bytes_out double precision,
    status text,
    updated_at timestamp with time zone,
    mac_address text,
    nas_port_id text,
    customer_name text,
    live_vlan_id bigint,
    live_vlan_iface text
);


ALTER TABLE public.online_users OWNER TO ispbilling;

--
-- Name: online_users_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.online_users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.online_users_id_seq OWNER TO ispbilling;

--
-- Name: online_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.online_users_id_seq OWNED BY public.online_users.id;


--
-- Name: onu_config_snapshots; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_config_snapshots (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    serial text,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    pushed_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.onu_config_snapshots OWNER TO ispbilling;

--
-- Name: onu_config_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_config_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_config_snapshots_id_seq OWNER TO ispbilling;

--
-- Name: onu_config_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_config_snapshots_id_seq OWNED BY public.onu_config_snapshots.id;


--
-- Name: onu_iptv_assignments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_iptv_assignments (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    profile_id bigint NOT NULL,
    attached_at text
);


ALTER TABLE public.onu_iptv_assignments OWNER TO ispbilling;

--
-- Name: onu_iptv_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_iptv_assignments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_iptv_assignments_id_seq OWNER TO ispbilling;

--
-- Name: onu_iptv_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_iptv_assignments_id_seq OWNED BY public.onu_iptv_assignments.id;


--
-- Name: onu_mac_table; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_mac_table (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    port text,
    mac text NOT NULL,
    vlan bigint,
    ip text,
    last_seen text NOT NULL
);


ALTER TABLE public.onu_mac_table OWNER TO ispbilling;

--
-- Name: onu_mac_table_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_mac_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_mac_table_id_seq OWNER TO ispbilling;

--
-- Name: onu_mac_table_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_mac_table_id_seq OWNED BY public.onu_mac_table.id;


--
-- Name: onu_service_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_service_profiles (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    connection_type text DEFAULT 'pppoe'::text NOT NULL,
    vlan integer,
    qos_dl_kbps bigint,
    qos_ul_kbps bigint,
    wifi_ssid_tpl text,
    wifi_pw_tpl text,
    wifi_band_split smallint DEFAULT 0 NOT NULL,
    acs_inform_int integer DEFAULT 300 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.onu_service_profiles OWNER TO ispbilling;

--
-- Name: onu_service_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_service_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_service_profiles_id_seq OWNER TO ispbilling;

--
-- Name: onu_service_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_service_profiles_id_seq OWNED BY public.onu_service_profiles.id;


--
-- Name: onu_signal_alerts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_signal_alerts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    kind text NOT NULL,
    severity text DEFAULT 'warn'::text,
    details text,
    opened_at text NOT NULL,
    closed_at text,
    ack_by text
);


ALTER TABLE public.onu_signal_alerts OWNER TO ispbilling;

--
-- Name: onu_signal_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_signal_alerts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_signal_alerts_id_seq OWNER TO ispbilling;

--
-- Name: onu_signal_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_signal_alerts_id_seq OWNED BY public.onu_signal_alerts.id;


--
-- Name: onu_signal_samples; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_signal_samples (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    ts text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    rx_dbm double precision,
    tx_dbm double precision,
    olt_rx_dbm double precision,
    temp_c double precision,
    voltage_v double precision,
    bias_ma double precision,
    distance_m bigint
);


ALTER TABLE public.onu_signal_samples OWNER TO ispbilling;

--
-- Name: onu_signal_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_signal_samples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_signal_samples_id_seq OWNER TO ispbilling;

--
-- Name: onu_signal_samples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_signal_samples_id_seq OWNED BY public.onu_signal_samples.id;


--
-- Name: onu_traffic_samples; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_traffic_samples (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    ts text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL,
    rx_mbps double precision DEFAULT 0,
    tx_mbps double precision DEFAULT 0,
    rx_packets bigint DEFAULT 0,
    tx_packets bigint DEFAULT 0,
    rx_errors bigint DEFAULT 0,
    tx_errors bigint DEFAULT 0
);


ALTER TABLE public.onu_traffic_samples OWNER TO ispbilling;

--
-- Name: onu_traffic_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_traffic_samples_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_traffic_samples_id_seq OWNER TO ispbilling;

--
-- Name: onu_traffic_samples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_traffic_samples_id_seq OWNED BY public.onu_traffic_samples.id;


--
-- Name: onu_voip_assignments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onu_voip_assignments (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    profile_id bigint NOT NULL,
    sip_username text,
    sip_password text,
    attached_at text
);


ALTER TABLE public.onu_voip_assignments OWNER TO ispbilling;

--
-- Name: onu_voip_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onu_voip_assignments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onu_voip_assignments_id_seq OWNER TO ispbilling;

--
-- Name: onu_voip_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onu_voip_assignments_id_seq OWNED BY public.onu_voip_assignments.id;


--
-- Name: onus; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.onus (
    id bigint NOT NULL,
    company_id text NOT NULL,
    olt_id bigint NOT NULL,
    pon_port_index bigint,
    onu_index bigint,
    serial text,
    mac text,
    vendor text,
    model text,
    name text,
    customer_id text,
    status text DEFAULT 'unknown'::text,
    rx_power double precision,
    tx_power double precision,
    distance_m bigint,
    uptime_sec bigint,
    last_seen text,
    last_offline text,
    offline_reason text,
    wifi_ssid text,
    wifi_password text,
    wan_ip text,
    wan_status text,
    notes text,
    created_at text,
    wan_mode text,
    wan_username text,
    wan_password text,
    wan_static_ip text,
    wan_netmask text,
    wan_gateway text,
    wan_dns text,
    wan_vlan bigint,
    manual_pin bigint DEFAULT 0,
    wan_service_name text,
    wifi_band_split bigint DEFAULT 0,
    wifi_ssid_5g text,
    wifi_password_5g text,
    wifi_radio_24_enabled bigint DEFAULT 1,
    wifi_radio_5_enabled bigint DEFAULT 1,
    wifi_auto_24 bigint DEFAULT 1,
    wifi_auto_5 bigint DEFAULT 1,
    wifi_channel_24 bigint,
    wifi_channel_5 bigint,
    wifi_bw_24 text,
    wifi_bw_5 text,
    lat double precision,
    lng double precision,
    location_accuracy_m double precision,
    location_source text DEFAULT 'address'::text,
    location_set_at timestamp with time zone,
    location_set_by text,
    temperature_c double precision,
    voltage_v double precision,
    bias_current_ma double precision,
    last_register_time text,
    offline_streak bigint DEFAULT 0,
    last_deregister_time text,
    olt_register_raw text,
    olt_deregister_raw text,
    olt_reason_raw text,
    firmware_version text,
    installation_date timestamp with time zone,
    warranty_until date,
    last_acs_inform timestamp with time zone,
    profile_id bigint,
    last_provisioned_at timestamp with time zone,
    factory_reset_seen timestamp with time zone,
    auto_recovery_enabled smallint DEFAULT 1,
    service_profile_id bigint
);


ALTER TABLE public.onus OWNER TO ispbilling;

--
-- Name: onus_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.onus_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.onus_id_seq OWNER TO ispbilling;

--
-- Name: onus_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.onus_id_seq OWNED BY public.onus.id;


--
-- Name: outage_event_onus_v2; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.outage_event_onus_v2 (
    id bigint NOT NULL,
    event_id bigint NOT NULL,
    onu_id bigint NOT NULL
);


ALTER TABLE public.outage_event_onus_v2 OWNER TO ispbilling;

--
-- Name: outage_event_onus_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.outage_event_onus_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.outage_event_onus_v2_id_seq OWNER TO ispbilling;

--
-- Name: outage_event_onus_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.outage_event_onus_v2_id_seq OWNED BY public.outage_event_onus_v2.id;


--
-- Name: outage_events; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.outage_events (
    id bigint NOT NULL,
    company_id text,
    olt_id bigint,
    pon_port bigint,
    onu_count bigint,
    affected_ids text,
    status text DEFAULT 'open'::text,
    started_at timestamp with time zone,
    resolved_at timestamp with time zone,
    complaint_id bigint,
    notified bigint DEFAULT 0
);


ALTER TABLE public.outage_events OWNER TO ispbilling;

--
-- Name: outage_events_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.outage_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.outage_events_id_seq OWNER TO ispbilling;

--
-- Name: outage_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.outage_events_id_seq OWNED BY public.outage_events.id;


--
-- Name: outage_events_v2; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.outage_events_v2 (
    id bigint NOT NULL,
    company_id text NOT NULL,
    kind text NOT NULL,
    severity text DEFAULT 'crit'::text,
    scope_kind text,
    scope_id text,
    details text,
    opened_at text NOT NULL,
    closed_at text,
    ack_by text
);


ALTER TABLE public.outage_events_v2 OWNER TO ispbilling;

--
-- Name: outage_events_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.outage_events_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.outage_events_v2_id_seq OWNER TO ispbilling;

--
-- Name: outage_events_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.outage_events_v2_id_seq OWNED BY public.outage_events_v2.id;


--
-- Name: outage_notifications_v2; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.outage_notifications_v2 (
    id bigint NOT NULL,
    event_id bigint NOT NULL,
    company_id text NOT NULL,
    channel text NOT NULL,
    customer_id text,
    text text NOT NULL,
    ai_used bigint DEFAULT 0,
    sent bigint DEFAULT 0,
    sent_at text,
    created_at text
);


ALTER TABLE public.outage_notifications_v2 OWNER TO ispbilling;

--
-- Name: outage_notifications_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.outage_notifications_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.outage_notifications_v2_id_seq OWNER TO ispbilling;

--
-- Name: outage_notifications_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.outage_notifications_v2_id_seq OWNED BY public.outage_notifications_v2.id;


--
-- Name: password_reset_captchas; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.password_reset_captchas (
    id bigint NOT NULL,
    code text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    is_used smallint DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.password_reset_captchas OWNER TO ispbilling;

--
-- Name: password_reset_captchas_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.password_reset_captchas_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.password_reset_captchas_id_seq OWNER TO ispbilling;

--
-- Name: password_reset_captchas_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.password_reset_captchas_id_seq OWNED BY public.password_reset_captchas.id;


--
-- Name: password_resets; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.password_resets (
    id bigint NOT NULL,
    email text NOT NULL,
    user_type text NOT NULL,
    otp_code text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    is_used smallint,
    created_at timestamp with time zone,
    company_id text DEFAULT ''::text,
    subject_id text DEFAULT ''::text
);


ALTER TABLE public.password_resets OWNER TO ispbilling;

--
-- Name: password_resets_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.password_resets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.password_resets_id_seq OWNER TO ispbilling;

--
-- Name: password_resets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.password_resets_id_seq OWNED BY public.password_resets.id;


--
-- Name: payment_gateways; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.payment_gateways (
    id bigint NOT NULL,
    company_id text NOT NULL,
    gateway_name text NOT NULL,
    merchant_id text,
    key_id_enc text,
    key_secret_enc text,
    webhook_secret_enc text,
    extra_config_enc text,
    is_default bigint DEFAULT 0 NOT NULL,
    status text DEFAULT 'Active'::text NOT NULL,
    last_tested_at timestamp with time zone,
    last_test_result text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.payment_gateways OWNER TO ispbilling;

--
-- Name: payment_gateways_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.payment_gateways_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payment_gateways_id_seq OWNER TO ispbilling;

--
-- Name: payment_gateways_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.payment_gateways_id_seq OWNED BY public.payment_gateways.id;


--
-- Name: payment_reminders; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.payment_reminders (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    last_reminder_sent timestamp with time zone,
    reminder_count bigint
);


ALTER TABLE public.payment_reminders OWNER TO ispbilling;

--
-- Name: payment_reminders_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.payment_reminders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payment_reminders_id_seq OWNER TO ispbilling;

--
-- Name: payment_reminders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.payment_reminders_id_seq OWNED BY public.payment_reminders.id;


--
-- Name: payments; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.payments (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    employee_id text,
    amount double precision,
    discount double precision,
    payment_mode text,
    transaction_no text,
    paid_at timestamp with time zone,
    remarks text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.payments OWNER TO ispbilling;

--
-- Name: payments_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.payments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payments_id_seq OWNER TO ispbilling;

--
-- Name: payments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.payments_id_seq OWNED BY public.payments.id;


--
-- Name: pending_emails; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.pending_emails (
    id bigint NOT NULL,
    company_id text,
    to_email text NOT NULL,
    cc_email text,
    bcc_email text,
    subject text NOT NULL,
    body_text text,
    body_html text,
    attachment_path text,
    priority bigint DEFAULT 5,
    status text DEFAULT 'pending'::text,
    attempts bigint DEFAULT 0,
    max_attempts bigint DEFAULT 3,
    last_error text,
    created_at timestamp with time zone,
    locked_by text,
    locked_until timestamp with time zone,
    sent_at timestamp with time zone,
    context text
);


ALTER TABLE public.pending_emails OWNER TO ispbilling;

--
-- Name: pending_emails_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.pending_emails_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pending_emails_id_seq OWNER TO ispbilling;

--
-- Name: pending_emails_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.pending_emails_id_seq OWNED BY public.pending_emails.id;


--
-- Name: permissions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.permissions (
    id bigint NOT NULL,
    key text NOT NULL,
    label text NOT NULL,
    category text NOT NULL,
    description text,
    created_at timestamp with time zone
);


ALTER TABLE public.permissions OWNER TO ispbilling;

--
-- Name: permissions_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.permissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.permissions_id_seq OWNER TO ispbilling;

--
-- Name: permissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.permissions_id_seq OWNED BY public.permissions.id;


--
-- Name: plan_change_orders; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.plan_change_orders (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    current_plan_id bigint,
    target_plan_id bigint,
    amount_due double precision,
    status text DEFAULT 'pending'::text,
    rzp_order_id text,
    rzp_payment_id text,
    applied_at timestamp with time zone,
    created_at timestamp with time zone
);


ALTER TABLE public.plan_change_orders OWNER TO ispbilling;

--
-- Name: plan_change_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.plan_change_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.plan_change_orders_id_seq OWNER TO ispbilling;

--
-- Name: plan_change_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.plan_change_orders_id_seq OWNED BY public.plan_change_orders.id;


--
-- Name: plans; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.plans (
    id bigint NOT NULL,
    company_id text,
    service text,
    plan_name text,
    speed text,
    validity bigint,
    base_amount double precision,
    cgst_tax double precision,
    sgst_tax double precision,
    igst_tax double precision,
    after_tax_amount double precision,
    description text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    download_speed text,
    upload_speed text,
    validity_unit text DEFAULT 'days'::text,
    fup_enabled bigint DEFAULT 0,
    fup_limit_gb double precision DEFAULT 0.0,
    fup_post_download text,
    fup_post_upload text,
    night_boost_enabled bigint DEFAULT 0,
    night_boost_start text,
    night_boost_end text,
    night_boost_download text,
    night_boost_upload text,
    burst_enabled bigint DEFAULT 0,
    burst_limit_down text,
    burst_limit_up text,
    burst_threshold_down text,
    burst_threshold_up text,
    burst_time_down bigint DEFAULT 0,
    burst_time_up bigint DEFAULT 0,
    priority bigint DEFAULT 8,
    queue_type text DEFAULT 'default'::text,
    dns_profile_id bigint
);


ALTER TABLE public.plans OWNER TO ispbilling;

--
-- Name: plans_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.plans_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.plans_id_seq OWNER TO ispbilling;

--
-- Name: plans_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.plans_id_seq OWNED BY public.plans.id;


--
-- Name: pon_ports; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.pon_ports (
    id bigint NOT NULL,
    olt_id bigint NOT NULL,
    port_index bigint NOT NULL,
    name text,
    tx_power double precision,
    admin_up bigint DEFAULT 1,
    oper_up bigint DEFAULT 1,
    total_onus bigint DEFAULT 0,
    online_onus bigint DEFAULT 0,
    temperature_c double precision,
    voltage_v double precision,
    bias_current_ma double precision
);


ALTER TABLE public.pon_ports OWNER TO ispbilling;

--
-- Name: pon_ports_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.pon_ports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pon_ports_id_seq OWNER TO ispbilling;

--
-- Name: pon_ports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.pon_ports_id_seq OWNED BY public.pon_ports.id;


--
-- Name: profanity_violations; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.profanity_violations (
    id bigint NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    actor_type text,
    actor_id text,
    actor_name text,
    company_id text,
    ip_address text,
    user_agent text,
    method text,
    request_path text,
    referer text,
    offending_word text NOT NULL,
    snippet text
);


ALTER TABLE public.profanity_violations OWNER TO ispbilling;

--
-- Name: profanity_violations_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.profanity_violations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.profanity_violations_id_seq OWNER TO ispbilling;

--
-- Name: profanity_violations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.profanity_violations_id_seq OWNED BY public.profanity_violations.id;


--
-- Name: public_ip_addresses; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.public_ip_addresses (
    id bigint NOT NULL,
    company_id text NOT NULL,
    ip_address text NOT NULL,
    label text,
    status text,
    assigned_to text,
    assigned_nas_id bigint,
    notes text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    pool_name text DEFAULT 'Default'::text
);


ALTER TABLE public.public_ip_addresses OWNER TO ispbilling;

--
-- Name: public_ip_addresses_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.public_ip_addresses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.public_ip_addresses_id_seq OWNER TO ispbilling;

--
-- Name: public_ip_addresses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.public_ip_addresses_id_seq OWNED BY public.public_ip_addresses.id;


--
-- Name: push_tokens; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.push_tokens (
    id bigint NOT NULL,
    company_id text NOT NULL,
    user_type text NOT NULL,
    user_pk bigint NOT NULL,
    expo_token text NOT NULL,
    device_id text,
    platform text,
    app_version text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.push_tokens OWNER TO ispbilling;

--
-- Name: push_tokens_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.push_tokens_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.push_tokens_id_seq OWNER TO ispbilling;

--
-- Name: push_tokens_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.push_tokens_id_seq OWNED BY public.push_tokens.id;


--
-- Name: received_tracker; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.received_tracker (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    received_since_reset double precision,
    last_reset_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.received_tracker OWNER TO ispbilling;

--
-- Name: received_tracker_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.received_tracker_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.received_tracker_id_seq OWNER TO ispbilling;

--
-- Name: received_tracker_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.received_tracker_id_seq OWNED BY public.received_tracker.id;


--
-- Name: referral_codes; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.referral_codes (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    code text,
    uses bigint DEFAULT 0,
    reward_per_signup double precision DEFAULT 100,
    reward_balance double precision DEFAULT 0,
    created_at timestamp with time zone
);


ALTER TABLE public.referral_codes OWNER TO ispbilling;

--
-- Name: referral_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.referral_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.referral_codes_id_seq OWNER TO ispbilling;

--
-- Name: referral_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.referral_codes_id_seq OWNED BY public.referral_codes.id;


--
-- Name: referrals; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.referrals (
    id bigint NOT NULL,
    company_id text,
    referrer_customer_id text,
    referee_customer_id text,
    code text,
    status text DEFAULT 'pending'::text,
    reward_amount double precision DEFAULT 0,
    rewarded_at timestamp with time zone,
    created_at timestamp with time zone
);


ALTER TABLE public.referrals OWNER TO ispbilling;

--
-- Name: referrals_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.referrals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.referrals_id_seq OWNER TO ispbilling;

--
-- Name: referrals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.referrals_id_seq OWNED BY public.referrals.id;


--
-- Name: renewal_logs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.renewal_logs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    months bigint NOT NULL,
    invoice_id bigint,
    amount double precision NOT NULL,
    method text NOT NULL,
    period_start timestamp with time zone NOT NULL,
    period_end timestamp with time zone NOT NULL,
    created_at timestamp with time zone
);


ALTER TABLE public.renewal_logs OWNER TO ispbilling;

--
-- Name: renewal_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.renewal_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.renewal_logs_id_seq OWNER TO ispbilling;

--
-- Name: renewal_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.renewal_logs_id_seq OWNED BY public.renewal_logs.id;


--
-- Name: retention_runs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.retention_runs (
    id bigint NOT NULL,
    ran_at timestamp with time zone,
    target_table text,
    cutoff_ts timestamp with time zone,
    rows_archived bigint DEFAULT 0,
    rows_deleted bigint DEFAULT 0,
    archive_path text,
    ok bigint DEFAULT 1,
    message text,
    company_id text
);


ALTER TABLE public.retention_runs OWNER TO ispbilling;

--
-- Name: retention_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.retention_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.retention_runs_id_seq OWNER TO ispbilling;

--
-- Name: retention_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.retention_runs_id_seq OWNED BY public.retention_runs.id;


--
-- Name: session_activity; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.session_activity (
    actor_type text NOT NULL,
    actor_id text NOT NULL,
    company_id text,
    last_seen_at timestamp with time zone NOT NULL
);


ALTER TABLE public.session_activity OWNER TO ispbilling;

--
-- Name: signal_degradation_events_v2; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.signal_degradation_events_v2 (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_id bigint NOT NULL,
    slope_db_per_day double precision,
    predicted_fail_in_days double precision,
    opened_at text NOT NULL,
    closed_at text
);


ALTER TABLE public.signal_degradation_events_v2 OWNER TO ispbilling;

--
-- Name: signal_degradation_events_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.signal_degradation_events_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.signal_degradation_events_v2_id_seq OWNER TO ispbilling;

--
-- Name: signal_degradation_events_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.signal_degradation_events_v2_id_seq OWNED BY public.signal_degradation_events_v2.id;


--
-- Name: smartnet_alerts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_alerts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    device_id bigint,
    port_id bigint,
    severity text NOT NULL,
    message text NOT NULL,
    status text DEFAULT 'active'::text,
    created_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone
);


ALTER TABLE public.smartnet_alerts OWNER TO ispbilling;

--
-- Name: smartnet_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_alerts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_alerts_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_alerts_id_seq OWNED BY public.smartnet_alerts.id;


--
-- Name: smartnet_audit; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_audit (
    id bigint NOT NULL,
    company_id text NOT NULL,
    actor text,
    action text,
    entity text,
    entity_id bigint,
    payload jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.smartnet_audit OWNER TO ispbilling;

--
-- Name: smartnet_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_audit_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_audit_id_seq OWNED BY public.smartnet_audit.id;


--
-- Name: smartnet_bandwidth; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_bandwidth (
    id bigint NOT NULL,
    company_id text NOT NULL,
    device_id bigint,
    port_id bigint,
    ts timestamp with time zone DEFAULT now(),
    in_mbps double precision DEFAULT 0,
    out_mbps double precision DEFAULT 0
);


ALTER TABLE public.smartnet_bandwidth OWNER TO ispbilling;

--
-- Name: smartnet_bandwidth_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_bandwidth_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_bandwidth_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_bandwidth_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_bandwidth_id_seq OWNED BY public.smartnet_bandwidth.id;


--
-- Name: smartnet_catalog; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_catalog (
    id bigint NOT NULL,
    brand text NOT NULL,
    model text NOT NULL,
    type text NOT NULL,
    image_url text,
    default_ports jsonb DEFAULT '[]'::jsonb,
    is_popular boolean DEFAULT false
);


ALTER TABLE public.smartnet_catalog OWNER TO ispbilling;

--
-- Name: smartnet_catalog_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_catalog_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_catalog_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_catalog_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_catalog_id_seq OWNED BY public.smartnet_catalog.id;


--
-- Name: smartnet_devices; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_devices (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    vendor text,
    model text,
    ip_address text,
    mac_address text,
    location text,
    latitude double precision,
    longitude double precision,
    x double precision DEFAULT 0,
    y double precision DEFAULT 0,
    image_url text,
    status text DEFAULT 'unknown'::text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.smartnet_devices OWNER TO ispbilling;

--
-- Name: smartnet_devices_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_devices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_devices_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_devices_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_devices_id_seq OWNED BY public.smartnet_devices.id;


--
-- Name: smartnet_layouts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_layouts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    layout jsonb NOT NULL,
    is_default boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT now(),
    created_by text
);


ALTER TABLE public.smartnet_layouts OWNER TO ispbilling;

--
-- Name: smartnet_layouts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_layouts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_layouts_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_layouts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_layouts_id_seq OWNED BY public.smartnet_layouts.id;


--
-- Name: smartnet_links; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_links (
    id bigint NOT NULL,
    company_id text NOT NULL,
    src_device_id bigint NOT NULL,
    dst_device_id bigint NOT NULL,
    src_port text,
    dst_port text,
    link_type text DEFAULT 'ethernet'::text,
    bandwidth_mbps bigint DEFAULT 1000,
    status text DEFAULT 'up'::text,
    label text,
    meta jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.smartnet_links OWNER TO ispbilling;

--
-- Name: smartnet_links_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_links_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_links_id_seq OWNED BY public.smartnet_links.id;


--
-- Name: smartnet_notif_channels; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_notif_channels (
    id bigint NOT NULL,
    company_id text NOT NULL,
    channel text NOT NULL,
    enabled boolean DEFAULT false,
    config jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now()
);


ALTER TABLE public.smartnet_notif_channels OWNER TO ispbilling;

--
-- Name: smartnet_notif_channels_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_notif_channels_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_notif_channels_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_notif_channels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_notif_channels_id_seq OWNED BY public.smartnet_notif_channels.id;


--
-- Name: smartnet_ports; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.smartnet_ports (
    id bigint NOT NULL,
    company_id text NOT NULL,
    device_id bigint NOT NULL,
    port_name text NOT NULL,
    port_type text DEFAULT 'ethernet'::text,
    status text DEFAULT 'down'::text,
    speed_mbps bigint DEFAULT 0,
    duplex text DEFAULT 'full'::text,
    sfp_module text,
    sfp_vendor text,
    sfp_partno text,
    wavelength_nm integer,
    distance_km double precision,
    tx_power_dbm double precision,
    rx_power_dbm double precision,
    temp_c double precision,
    voltage_v double precision,
    bw_in_mbps double precision DEFAULT 0,
    bw_out_mbps double precision DEFAULT 0,
    errors bigint DEFAULT 0,
    discards bigint DEFAULT 0,
    last_change timestamp with time zone DEFAULT now(),
    meta jsonb DEFAULT '{}'::jsonb
);


ALTER TABLE public.smartnet_ports OWNER TO ispbilling;

--
-- Name: smartnet_ports_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.smartnet_ports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.smartnet_ports_id_seq OWNER TO ispbilling;

--
-- Name: smartnet_ports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.smartnet_ports_id_seq OWNED BY public.smartnet_ports.id;


--
-- Name: sms_campaigns; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sms_campaigns (
    id bigint NOT NULL,
    company_id text,
    name text,
    body text,
    target_type text,
    target_ids text,
    status text DEFAULT 'draft'::text,
    total_recipients bigint DEFAULT 0,
    sent_count bigint DEFAULT 0,
    failed_count bigint DEFAULT 0,
    created_at timestamp with time zone,
    created_by text,
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone
);


ALTER TABLE public.sms_campaigns OWNER TO ispbilling;

--
-- Name: sms_campaigns_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sms_campaigns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sms_campaigns_id_seq OWNER TO ispbilling;

--
-- Name: sms_campaigns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sms_campaigns_id_seq OWNED BY public.sms_campaigns.id;


--
-- Name: sms_logs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sms_logs (
    id bigint NOT NULL,
    company_id text,
    actor_type text,
    actor_id text,
    to_phone text,
    to_name text,
    customer_id text,
    body text,
    status text,
    sid text,
    error text,
    campaign_id bigint,
    created_at timestamp with time zone
);


ALTER TABLE public.sms_logs OWNER TO ispbilling;

--
-- Name: sms_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sms_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sms_logs_id_seq OWNER TO ispbilling;

--
-- Name: sms_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sms_logs_id_seq OWNED BY public.sms_logs.id;


--
-- Name: sub_lco_commissions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sub_lco_commissions (
    id bigint NOT NULL,
    company_id text NOT NULL,
    sub_lco_id bigint NOT NULL,
    customer_id text NOT NULL,
    payment_id bigint,
    base_amount double precision DEFAULT 0 NOT NULL,
    commission_percent double precision DEFAULT 0 NOT NULL,
    commission_amount double precision DEFAULT 0 NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    payout_status text DEFAULT 'Pending'::text,
    payout_id bigint,
    settled_at timestamp with time zone
);


ALTER TABLE public.sub_lco_commissions OWNER TO ispbilling;

--
-- Name: sub_lco_commissions_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sub_lco_commissions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sub_lco_commissions_id_seq OWNER TO ispbilling;

--
-- Name: sub_lco_commissions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sub_lco_commissions_id_seq OWNED BY public.sub_lco_commissions.id;


--
-- Name: sub_lco_locations; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sub_lco_locations (
    id bigint NOT NULL,
    sub_lco_id bigint NOT NULL,
    location_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.sub_lco_locations OWNER TO ispbilling;

--
-- Name: sub_lco_locations_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sub_lco_locations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sub_lco_locations_id_seq OWNER TO ispbilling;

--
-- Name: sub_lco_locations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sub_lco_locations_id_seq OWNED BY public.sub_lco_locations.id;


--
-- Name: sub_lco_locks; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sub_lco_locks (
    id bigint NOT NULL,
    company_id text NOT NULL,
    sub_lco_id bigint NOT NULL,
    locked_at timestamp with time zone,
    locked_by text
);


ALTER TABLE public.sub_lco_locks OWNER TO ispbilling;

--
-- Name: sub_lco_locks_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sub_lco_locks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sub_lco_locks_id_seq OWNER TO ispbilling;

--
-- Name: sub_lco_locks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sub_lco_locks_id_seq OWNED BY public.sub_lco_locks.id;


--
-- Name: sub_lco_payouts; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sub_lco_payouts (
    id bigint NOT NULL,
    company_id text NOT NULL,
    sub_lco_id bigint NOT NULL,
    amount double precision DEFAULT 0 NOT NULL,
    reference text,
    notes text,
    paid_at timestamp with time zone NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.sub_lco_payouts OWNER TO ispbilling;

--
-- Name: sub_lco_payouts_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sub_lco_payouts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sub_lco_payouts_id_seq OWNER TO ispbilling;

--
-- Name: sub_lco_payouts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sub_lco_payouts_id_seq OWNED BY public.sub_lco_payouts.id;


--
-- Name: sub_lcos; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.sub_lcos (
    id bigint NOT NULL,
    company_id text NOT NULL,
    sub_lco_code text NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    name text NOT NULL,
    email text,
    mobile text,
    address text,
    commission_percent double precision DEFAULT 0 NOT NULL,
    status text DEFAULT 'Active'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    terms_conditions text,
    declaration text,
    inv_company_name text,
    inv_company_gst text,
    inv_company_phone text,
    inv_company_email text,
    inv_company_address text,
    inv_bank_name text,
    inv_account_number text,
    inv_branch_ifsc text,
    inv_branch_location text,
    inv_upi_id text,
    profile_image text
);


ALTER TABLE public.sub_lcos OWNER TO ispbilling;

--
-- Name: sub_lcos_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.sub_lcos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sub_lcos_id_seq OWNER TO ispbilling;

--
-- Name: sub_lcos_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.sub_lcos_id_seq OWNED BY public.sub_lcos.id;


--
-- Name: superadmin_packages; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.superadmin_packages (
    id bigint NOT NULL,
    package_name text,
    user_count bigint,
    package_type text,
    package_price double precision,
    description text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    cgst_rate double precision DEFAULT 9.0,
    sgst_rate double precision DEFAULT 9.0,
    igst_rate double precision DEFAULT 18.0
);


ALTER TABLE public.superadmin_packages OWNER TO ispbilling;

--
-- Name: superadmin_packages_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.superadmin_packages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.superadmin_packages_id_seq OWNER TO ispbilling;

--
-- Name: superadmin_packages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.superadmin_packages_id_seq OWNED BY public.superadmin_packages.id;


--
-- Name: superadmin_settings; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.superadmin_settings (
    id bigint NOT NULL,
    contact_number text,
    contact_email text,
    address text,
    state text,
    gst_number text,
    bank_name text,
    branch_code text,
    account_no text,
    branch_location text,
    ifsc_code text,
    upi_id text,
    qr_code_path text,
    declaration text,
    terms_conditions text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.superadmin_settings OWNER TO ispbilling;

--
-- Name: superadmin_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.superadmin_settings_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.superadmin_settings_id_seq OWNER TO ispbilling;

--
-- Name: superadmin_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.superadmin_settings_id_seq OWNED BY public.superadmin_settings.id;


--
-- Name: superadmins; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.superadmins (
    id bigint NOT NULL,
    superadmin_id text,
    password_hash text,
    superadmin_name text,
    email text,
    mobile text,
    profile_image_path text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    logo_path text,
    smtp_server text,
    smtp_port bigint,
    smtp_username text,
    smtp_password text,
    contact_email text
);


ALTER TABLE public.superadmins OWNER TO ispbilling;

--
-- Name: superadmins_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.superadmins_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.superadmins_id_seq OWNER TO ispbilling;

--
-- Name: superadmins_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.superadmins_id_seq OWNED BY public.superadmins.id;


--
-- Name: support_responses; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.support_responses (
    id bigint NOT NULL,
    ticket_id bigint NOT NULL,
    responder_role text NOT NULL,
    responder_id text NOT NULL,
    message text NOT NULL,
    created_at timestamp with time zone
);


ALTER TABLE public.support_responses OWNER TO ispbilling;

--
-- Name: support_responses_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.support_responses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.support_responses_id_seq OWNER TO ispbilling;

--
-- Name: support_responses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.support_responses_id_seq OWNED BY public.support_responses.id;


--
-- Name: support_tickets; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.support_tickets (
    id bigint NOT NULL,
    company_id text NOT NULL,
    admin_id text,
    ticket_no text NOT NULL,
    category text NOT NULL,
    priority text,
    subject text NOT NULL,
    description text NOT NULL,
    status text,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    resolved_at timestamp with time zone,
    resolved_by text,
    last_response_at timestamp with time zone
);


ALTER TABLE public.support_tickets OWNER TO ispbilling;

--
-- Name: support_tickets_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.support_tickets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.support_tickets_id_seq OWNER TO ispbilling;

--
-- Name: support_tickets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.support_tickets_id_seq OWNED BY public.support_tickets.id;


--
-- Name: transactions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.transactions (
    id bigint NOT NULL,
    company_id text,
    customer_id text,
    transaction_type text,
    amount double precision,
    invoice_id bigint,
    start_date text,
    end_date text,
    period_months bigint,
    remarks text,
    created_at timestamp with time zone,
    payment_method text,
    reference_no text,
    note text,
    transaction_date timestamp with time zone
);


ALTER TABLE public.transactions OWNER TO ispbilling;

--
-- Name: transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.transactions_id_seq OWNER TO ispbilling;

--
-- Name: transactions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.transactions_id_seq OWNED BY public.transactions.id;


--
-- Name: url_logs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.url_logs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text,
    user_ip text,
    ts timestamp with time zone,
    method text,
    host text,
    url text,
    dst_port bigint,
    status_code bigint,
    bytes_xfer bigint DEFAULT 0,
    user_agent text,
    nas_ip text,
    created_at timestamp with time zone
);


ALTER TABLE public.url_logs OWNER TO ispbilling;

--
-- Name: url_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.url_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.url_logs_id_seq OWNER TO ispbilling;

--
-- Name: url_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.url_logs_id_seq OWNED BY public.url_logs.id;


--
-- Name: vlan_pool; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.vlan_pool (
    id bigint NOT NULL,
    company_id text NOT NULL,
    vlan_id bigint NOT NULL,
    label text,
    state text DEFAULT 'free'::text NOT NULL,
    customer_id bigint,
    assigned_at text,
    assigned_by text,
    notes text,
    created_at text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL
);


ALTER TABLE public.vlan_pool OWNER TO ispbilling;

--
-- Name: vlan_pool_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.vlan_pool_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vlan_pool_id_seq OWNER TO ispbilling;

--
-- Name: vlan_pool_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.vlan_pool_id_seq OWNED BY public.vlan_pool.id;


--
-- Name: vlan_setup_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.vlan_setup_log (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint,
    actor_user text,
    actor_role text,
    action text NOT NULL,
    detail text,
    ok bigint DEFAULT 1,
    ts text DEFAULT to_char((now() AT TIME ZONE 'UTC'::text), 'YYYY-MM-DD HH24:MI:SS'::text) NOT NULL
);


ALTER TABLE public.vlan_setup_log OWNER TO ispbilling;

--
-- Name: vlan_setup_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.vlan_setup_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vlan_setup_log_id_seq OWNER TO ispbilling;

--
-- Name: vlan_setup_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.vlan_setup_log_id_seq OWNED BY public.vlan_setup_log.id;


--
-- Name: voip_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.voip_profiles (
    id bigint NOT NULL,
    company_id text NOT NULL,
    name text NOT NULL,
    sip_server text NOT NULL,
    sip_port bigint DEFAULT 5060,
    sip_proxy text,
    transport text DEFAULT 'UDP'::text,
    codec text DEFAULT 'G.711a'::text,
    description text,
    created_at text
);


ALTER TABLE public.voip_profiles OWNER TO ispbilling;

--
-- Name: voip_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.voip_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.voip_profiles_id_seq OWNER TO ispbilling;

--
-- Name: voip_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.voip_profiles_id_seq OWNED BY public.voip_profiles.id;


--
-- Name: voucher_redemptions; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.voucher_redemptions (
    id bigint NOT NULL,
    company_id text NOT NULL,
    voucher_id bigint,
    batch_id text,
    code text NOT NULL,
    used_by text,
    mac_address text,
    ip_address text,
    user_agent text,
    duration_minutes bigint DEFAULT 0,
    data_cap_mb bigint DEFAULT 0,
    plan_name text,
    created_at text
);


ALTER TABLE public.voucher_redemptions OWNER TO ispbilling;

--
-- Name: voucher_redemptions_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.voucher_redemptions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.voucher_redemptions_id_seq OWNER TO ispbilling;

--
-- Name: voucher_redemptions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.voucher_redemptions_id_seq OWNED BY public.voucher_redemptions.id;


--
-- Name: webhook_log; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.webhook_log (
    id bigint NOT NULL,
    company_id text,
    gateway_name text,
    event_id text,
    event_type text,
    signature text,
    signature_valid bigint,
    payload_json text,
    http_status bigint,
    processed_payment_id text,
    received_at timestamp with time zone NOT NULL
);


ALTER TABLE public.webhook_log OWNER TO ispbilling;

--
-- Name: webhook_log_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.webhook_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.webhook_log_id_seq OWNER TO ispbilling;

--
-- Name: webhook_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.webhook_log_id_seq OWNED BY public.webhook_log.id;


--
-- Name: website_block_targets; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.website_block_targets (
    id bigint NOT NULL,
    block_id bigint NOT NULL,
    company_id text NOT NULL,
    customer_pk bigint DEFAULT 0,
    customer_username text DEFAULT ''::text,
    customer_name text DEFAULT ''::text,
    snapshot_ip text DEFAULT ''::text,
    status text DEFAULT 'Active'::text,
    created_at text
);


ALTER TABLE public.website_block_targets OWNER TO ispbilling;

--
-- Name: website_block_targets_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.website_block_targets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_block_targets_id_seq OWNER TO ispbilling;

--
-- Name: website_block_targets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.website_block_targets_id_seq OWNED BY public.website_block_targets.id;


--
-- Name: website_blocks; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.website_blocks (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint NOT NULL,
    name text DEFAULT ''::text,
    domains text DEFAULT ''::text,
    status text DEFAULT 'Active'::text,
    last_applied text DEFAULT ''::text,
    last_error text DEFAULT ''::text,
    pushed_summary text DEFAULT ''::text,
    created_at text
);


ALTER TABLE public.website_blocks OWNER TO ispbilling;

--
-- Name: website_blocks_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.website_blocks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.website_blocks_id_seq OWNER TO ispbilling;

--
-- Name: website_blocks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.website_blocks_id_seq OWNED BY public.website_blocks.id;


--
-- Name: wg_tenant_slices; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.wg_tenant_slices (
    company_id text,
    slice_cidr text NOT NULL,
    allocated_at text NOT NULL
);


ALTER TABLE public.wg_tenant_slices OWNER TO ispbilling;

--
-- Name: whatsapp_campaigns; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.whatsapp_campaigns (
    id bigint NOT NULL,
    name text,
    template_id bigint,
    target_type text,
    target_ids text,
    status text,
    scheduled_at timestamp with time zone,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    total_recipients bigint,
    sent_count bigint,
    failed_count bigint,
    created_at timestamp with time zone,
    created_by text,
    company_id text,
    body text,
    actor_type text
);


ALTER TABLE public.whatsapp_campaigns OWNER TO ispbilling;

--
-- Name: whatsapp_campaigns_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.whatsapp_campaigns_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.whatsapp_campaigns_id_seq OWNER TO ispbilling;

--
-- Name: whatsapp_campaigns_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.whatsapp_campaigns_id_seq OWNED BY public.whatsapp_campaigns.id;


--
-- Name: whatsapp_config; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.whatsapp_config (
    id bigint NOT NULL,
    provider text,
    business_account_id text,
    phone_number_id text,
    sender_phone text,
    access_token text,
    webhook_verify_token text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone,
    updated_at timestamp with time zone
);


ALTER TABLE public.whatsapp_config OWNER TO ispbilling;

--
-- Name: whatsapp_config_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.whatsapp_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.whatsapp_config_id_seq OWNER TO ispbilling;

--
-- Name: whatsapp_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.whatsapp_config_id_seq OWNED BY public.whatsapp_config.id;


--
-- Name: whatsapp_message_logs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.whatsapp_message_logs (
    id bigint NOT NULL,
    campaign_id bigint,
    recipient_type text,
    recipient_id text,
    recipient_phone text,
    template_name text,
    message_content text,
    status text,
    error_message text,
    sent_at timestamp with time zone,
    delivered_at timestamp with time zone,
    read_at timestamp with time zone,
    provider text DEFAULT 'msg91'::text,
    provider_message_id text,
    company_id text,
    linked_invoice_id bigint,
    linked_payment_id bigint,
    failed_at timestamp with time zone,
    webhook_payload text
);


ALTER TABLE public.whatsapp_message_logs OWNER TO ispbilling;

--
-- Name: whatsapp_message_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.whatsapp_message_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.whatsapp_message_logs_id_seq OWNER TO ispbilling;

--
-- Name: whatsapp_message_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.whatsapp_message_logs_id_seq OWNED BY public.whatsapp_message_logs.id;


--
-- Name: whatsapp_templates; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.whatsapp_templates (
    id bigint NOT NULL,
    name text,
    language text,
    category text,
    header_type text,
    header_text text,
    body_text text,
    footer_text text,
    buttons_json text,
    variables_json text,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone,
    updated_at timestamp with time zone,
    created_by text,
    company_id text
);


ALTER TABLE public.whatsapp_templates OWNER TO ispbilling;

--
-- Name: whatsapp_templates_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.whatsapp_templates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.whatsapp_templates_id_seq OWNER TO ispbilling;

--
-- Name: whatsapp_templates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.whatsapp_templates_id_seq OWNED BY public.whatsapp_templates.id;


--
-- Name: wifi_recover_tokens; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.wifi_recover_tokens (
    token text,
    customer_id text,
    company_id text,
    issued_at bigint,
    expires_at bigint,
    used_at bigint,
    ip text
);


ALTER TABLE public.wifi_recover_tokens OWNER TO ispbilling;

--
-- Name: ztp_dhcp_option43_configs; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ztp_dhcp_option43_configs (
    id bigint NOT NULL,
    company_id text NOT NULL,
    nas_id bigint NOT NULL,
    port_name text,
    vlan_id integer NOT NULL,
    acs_url text NOT NULL,
    acs_username text,
    acs_password text,
    additional_opts text,
    enabled integer DEFAULT 1 NOT NULL,
    last_generated_script text,
    last_generated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ztp_dhcp_option43_configs OWNER TO ispbilling;

--
-- Name: ztp_dhcp_option43_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ztp_dhcp_option43_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ztp_dhcp_option43_configs_id_seq OWNER TO ispbilling;

--
-- Name: ztp_dhcp_option43_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ztp_dhcp_option43_configs_id_seq OWNED BY public.ztp_dhcp_option43_configs.id;


--
-- Name: ztp_discovered_onus; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ztp_discovered_onus (
    id bigint NOT NULL,
    company_id text NOT NULL,
    olt_id bigint NOT NULL,
    pon_port integer NOT NULL,
    onu_serial text NOT NULL,
    onu_vendor text,
    onu_model text,
    firmware text,
    mac_address text,
    loid text,
    rx_power_dbm numeric(6,2),
    tx_power_dbm numeric(6,2),
    distance_m integer,
    status text DEFAULT 'DISCOVERED'::text NOT NULL,
    confidence integer DEFAULT 100 NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ztp_discovered_onus OWNER TO ispbilling;

--
-- Name: ztp_discovered_onus_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ztp_discovered_onus_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ztp_discovered_onus_id_seq OWNER TO ispbilling;

--
-- Name: ztp_discovered_onus_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ztp_discovered_onus_id_seq OWNED BY public.ztp_discovered_onus.id;


--
-- Name: ztp_onu_customer_mapping; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ztp_onu_customer_mapping (
    id bigint NOT NULL,
    company_id text NOT NULL,
    customer_id text,
    olt_id bigint,
    pon_port integer,
    onu_index integer,
    onu_serial text NOT NULL,
    onu_profile_id bigint,
    internet_plan_id bigint,
    pppoe_username text,
    pppoe_password text,
    vlan_id integer,
    service_vlan integer,
    client_vlan integer,
    status text DEFAULT 'MAPPED'::text NOT NULL,
    last_state_change timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ztp_onu_customer_mapping OWNER TO ispbilling;

--
-- Name: ztp_onu_customer_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ztp_onu_customer_mapping_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ztp_onu_customer_mapping_id_seq OWNER TO ispbilling;

--
-- Name: ztp_onu_customer_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ztp_onu_customer_mapping_id_seq OWNED BY public.ztp_onu_customer_mapping.id;


--
-- Name: ztp_onu_profiles; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ztp_onu_profiles (
    id bigint NOT NULL,
    company_id text NOT NULL,
    profile_name text NOT NULL,
    description text,
    internet_plan_id bigint,
    wan_mode text,
    wan_vlan integer,
    service_vlan integer,
    client_vlan integer,
    management_vlan integer,
    acs_url text,
    acs_username text,
    acs_password text,
    acs_inform_interval integer DEFAULT 300,
    wifi_ssid_template text,
    wifi_password_template text,
    wifi_band_split integer DEFAULT 0,
    lan_dhcp_enabled integer DEFAULT 1,
    lan_subnet text,
    iptv_enabled integer DEFAULT 0,
    iptv_vlan integer,
    voip_enabled integer DEFAULT 0,
    voip_vlan integer,
    speed_profile_id bigint,
    olt_service_template text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ztp_onu_profiles OWNER TO ispbilling;

--
-- Name: ztp_onu_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ztp_onu_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ztp_onu_profiles_id_seq OWNER TO ispbilling;

--
-- Name: ztp_onu_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ztp_onu_profiles_id_seq OWNED BY public.ztp_onu_profiles.id;


--
-- Name: ztp_state_audit; Type: TABLE; Schema: public; Owner: ispbilling
--

CREATE TABLE public.ztp_state_audit (
    id bigint NOT NULL,
    company_id text NOT NULL,
    onu_serial text,
    customer_id text,
    olt_id bigint,
    from_state text,
    to_state text NOT NULL,
    reason text,
    actor text,
    payload_json text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ztp_state_audit OWNER TO ispbilling;

--
-- Name: ztp_state_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: ispbilling
--

CREATE SEQUENCE public.ztp_state_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ztp_state_audit_id_seq OWNER TO ispbilling;

--
-- Name: ztp_state_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: ispbilling
--

ALTER SEQUENCE public.ztp_state_audit_id_seq OWNED BY public.ztp_state_audit.id;


--
-- Name: access_request_logs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.access_request_logs ALTER COLUMN id SET DEFAULT nextval('public.access_request_logs_id_seq'::regclass);


--
-- Name: account_deletion_requests id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.account_deletion_requests ALTER COLUMN id SET DEFAULT nextval('public.account_deletion_requests_id_seq'::regclass);


--
-- Name: acs_device_mapping id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_device_mapping ALTER COLUMN id SET DEFAULT nextval('public.acs_device_mapping_id_seq'::regclass);


--
-- Name: acs_device_parameter_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_device_parameter_profiles ALTER COLUMN id SET DEFAULT nextval('public.acs_device_parameter_profiles_id_seq'::regclass);


--
-- Name: acs_push_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_push_log ALTER COLUMN id SET DEFAULT nextval('public.acs_push_log_id_seq'::regclass);


--
-- Name: admin_activity_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.admin_activity_log ALTER COLUMN id SET DEFAULT nextval('public.admin_activity_log_id_seq'::regclass);


--
-- Name: admins id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.admins ALTER COLUMN id SET DEFAULT nextval('public.admins_id_seq'::regclass);


--
-- Name: api_keys id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.api_keys ALTER COLUMN id SET DEFAULT nextval('public.api_keys_id_seq'::regclass);


--
-- Name: captive_portal_settings id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.captive_portal_settings ALTER COLUMN id SET DEFAULT nextval('public.captive_portal_settings_id_seq'::regclass);


--
-- Name: cms_mirror_config id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.cms_mirror_config ALTER COLUMN id SET DEFAULT nextval('public.cms_mirror_config_id_seq'::regclass);


--
-- Name: companies id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.companies ALTER COLUMN id SET DEFAULT nextval('public.companies_id_seq'::regclass);


--
-- Name: company_feature_flags id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.company_feature_flags ALTER COLUMN id SET DEFAULT nextval('public.company_feature_flags_id_seq'::regclass);


--
-- Name: complaint_comments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaint_comments ALTER COLUMN id SET DEFAULT nextval('public.complaint_comments_id_seq'::regclass);


--
-- Name: complaint_responses id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaint_responses ALTER COLUMN id SET DEFAULT nextval('public.complaint_responses_id_seq'::regclass);


--
-- Name: complaints id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaints ALTER COLUMN id SET DEFAULT nextval('public.complaints_id_seq'::regclass);


--
-- Name: compliance_ingest_tokens id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.compliance_ingest_tokens ALTER COLUMN id SET DEFAULT nextval('public.compliance_ingest_tokens_id_seq'::regclass);


--
-- Name: connection_requests id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.connection_requests ALTER COLUMN id SET DEFAULT nextval('public.connection_requests_id_seq'::regclass);


--
-- Name: customer_status_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.customer_status_log ALTER COLUMN id SET DEFAULT nextval('public.customer_status_log_id_seq'::regclass);


--
-- Name: customers id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);


--
-- Name: data_mgmt_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.data_mgmt_log ALTER COLUMN id SET DEFAULT nextval('public.data_mgmt_log_id_seq'::regclass);


--
-- Name: db_backups id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.db_backups ALTER COLUMN id SET DEFAULT nextval('public.db_backups_id_seq'::regclass);


--
-- Name: dns_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.dns_profiles ALTER COLUMN id SET DEFAULT nextval('public.dns_profiles_id_seq'::regclass);


--
-- Name: dot_blocklist id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.dot_blocklist ALTER COLUMN id SET DEFAULT nextval('public.dot_blocklist_id_seq'::regclass);


--
-- Name: employee_locality_assignments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_locality_assignments ALTER COLUMN id SET DEFAULT nextval('public.employee_locality_assignments_id_seq'::regclass);


--
-- Name: employee_location_history id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_location_history ALTER COLUMN id SET DEFAULT nextval('public.employee_location_history_id_seq'::regclass);


--
-- Name: employee_permissions id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_permissions ALTER COLUMN id SET DEFAULT nextval('public.employee_permissions_id_seq'::regclass);


--
-- Name: employee_sequences id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_sequences ALTER COLUMN id SET DEFAULT nextval('public.employee_sequences_id_seq'::regclass);


--
-- Name: employees id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employees ALTER COLUMN id SET DEFAULT nextval('public.employees_id_seq'::regclass);


--
-- Name: expenses id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.expenses ALTER COLUMN id SET DEFAULT nextval('public.expenses_id_seq'::regclass);


--
-- Name: fiber_cut_history id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.fiber_cut_history ALTER COLUMN id SET DEFAULT nextval('public.fiber_cut_history_id_seq'::regclass);


--
-- Name: fiber_splice id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.fiber_splice ALTER COLUMN id SET DEFAULT nextval('public.fiber_splice_id_seq'::regclass);


--
-- Name: geofence_events id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.geofence_events ALTER COLUMN id SET DEFAULT nextval('public.geofence_events_id_seq'::regclass);


--
-- Name: geofences id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.geofences ALTER COLUMN id SET DEFAULT nextval('public.geofences_id_seq'::regclass);


--
-- Name: hotspot_vouchers id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.hotspot_vouchers ALTER COLUMN id SET DEFAULT nextval('public.hotspot_vouchers_id_seq'::regclass);


--
-- Name: invoice_reminder_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoice_reminder_log ALTER COLUMN id SET DEFAULT nextval('public.invoice_reminder_log_id_seq'::regclass);


--
-- Name: invoice_sequences id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoice_sequences ALTER COLUMN id SET DEFAULT nextval('public.invoice_sequences_id_seq'::regclass);


--
-- Name: invoices id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoices ALTER COLUMN id SET DEFAULT nextval('public.invoices_id_seq'::regclass);


--
-- Name: ip_pools id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ip_pools ALTER COLUMN id SET DEFAULT nextval('public.ip_pools_id_seq'::regclass);


--
-- Name: ipdr_records id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ipdr_records ALTER COLUMN id SET DEFAULT nextval('public.ipdr_records_id_seq'::regclass);


--
-- Name: iptv_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.iptv_profiles ALTER COLUMN id SET DEFAULT nextval('public.iptv_profiles_id_seq'::regclass);


--
-- Name: li_orders id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.li_orders ALTER COLUMN id SET DEFAULT nextval('public.li_orders_id_seq'::regclass);


--
-- Name: load_balancing_configs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.load_balancing_configs ALTER COLUMN id SET DEFAULT nextval('public.load_balancing_configs_id_seq'::regclass);


--
-- Name: locations id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.locations ALTER COLUMN id SET DEFAULT nextval('public.locations_id_seq'::regclass);


--
-- Name: lock_secrets id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.lock_secrets ALTER COLUMN id SET DEFAULT nextval('public.lock_secrets_id_seq'::regclass);


--
-- Name: login_events id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.login_events ALTER COLUMN id SET DEFAULT nextval('public.login_events_id_seq'::regclass);


--
-- Name: mdu_buildings id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_buildings ALTER COLUMN id SET DEFAULT nextval('public.mdu_buildings_id_seq'::regclass);


--
-- Name: mdu_floors id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_floors ALTER COLUMN id SET DEFAULT nextval('public.mdu_floors_id_seq'::regclass);


--
-- Name: mdu_unit_customers id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_unit_customers ALTER COLUMN id SET DEFAULT nextval('public.mdu_unit_customers_id_seq'::regclass);


--
-- Name: mdu_units id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_units ALTER COLUMN id SET DEFAULT nextval('public.mdu_units_id_seq'::regclass);


--
-- Name: mobile_login_banners id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mobile_login_banners ALTER COLUMN id SET DEFAULT nextval('public.mobile_login_banners_id_seq'::regclass);


--
-- Name: nas_compliance_deployments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nas_compliance_deployments ALTER COLUMN id SET DEFAULT nextval('public.nas_compliance_deployments_id_seq'::regclass);


--
-- Name: nas_devices id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nas_devices ALTER COLUMN id SET DEFAULT nextval('public.nas_devices_id_seq'::regclass);


--
-- Name: nat_configs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nat_configs ALTER COLUMN id SET DEFAULT nextval('public.nat_configs_id_seq'::regclass);


--
-- Name: nat_one_to_one_pairs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nat_one_to_one_pairs ALTER COLUMN id SET DEFAULT nextval('public.nat_one_to_one_pairs_id_seq'::regclass);


--
-- Name: network_fiber id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_fiber ALTER COLUMN id SET DEFAULT nextval('public.network_fiber_id_seq'::regclass);


--
-- Name: network_hardware id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_hardware ALTER COLUMN id SET DEFAULT nextval('public.network_hardware_id_seq'::regclass);


--
-- Name: network_seed_markers id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_seed_markers ALTER COLUMN id SET DEFAULT nextval('public.network_seed_markers_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: olt_alerts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olt_alerts ALTER COLUMN id SET DEFAULT nextval('public.olt_alerts_id_seq'::regclass);


--
-- Name: olt_polls id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olt_polls ALTER COLUMN id SET DEFAULT nextval('public.olt_polls_id_seq'::regclass);


--
-- Name: olts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olts ALTER COLUMN id SET DEFAULT nextval('public.olts_id_seq'::regclass);


--
-- Name: online_users id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.online_users ALTER COLUMN id SET DEFAULT nextval('public.online_users_id_seq'::regclass);


--
-- Name: onu_config_snapshots id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_config_snapshots ALTER COLUMN id SET DEFAULT nextval('public.onu_config_snapshots_id_seq'::regclass);


--
-- Name: onu_iptv_assignments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_iptv_assignments ALTER COLUMN id SET DEFAULT nextval('public.onu_iptv_assignments_id_seq'::regclass);


--
-- Name: onu_mac_table id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_mac_table ALTER COLUMN id SET DEFAULT nextval('public.onu_mac_table_id_seq'::regclass);


--
-- Name: onu_service_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_service_profiles ALTER COLUMN id SET DEFAULT nextval('public.onu_service_profiles_id_seq'::regclass);


--
-- Name: onu_signal_alerts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_signal_alerts ALTER COLUMN id SET DEFAULT nextval('public.onu_signal_alerts_id_seq'::regclass);


--
-- Name: onu_signal_samples id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_signal_samples ALTER COLUMN id SET DEFAULT nextval('public.onu_signal_samples_id_seq'::regclass);


--
-- Name: onu_traffic_samples id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_traffic_samples ALTER COLUMN id SET DEFAULT nextval('public.onu_traffic_samples_id_seq'::regclass);


--
-- Name: onu_voip_assignments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_voip_assignments ALTER COLUMN id SET DEFAULT nextval('public.onu_voip_assignments_id_seq'::regclass);


--
-- Name: onus id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onus ALTER COLUMN id SET DEFAULT nextval('public.onus_id_seq'::regclass);


--
-- Name: outage_event_onus_v2 id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_event_onus_v2 ALTER COLUMN id SET DEFAULT nextval('public.outage_event_onus_v2_id_seq'::regclass);


--
-- Name: outage_events id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_events ALTER COLUMN id SET DEFAULT nextval('public.outage_events_id_seq'::regclass);


--
-- Name: outage_events_v2 id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_events_v2 ALTER COLUMN id SET DEFAULT nextval('public.outage_events_v2_id_seq'::regclass);


--
-- Name: outage_notifications_v2 id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_notifications_v2 ALTER COLUMN id SET DEFAULT nextval('public.outage_notifications_v2_id_seq'::regclass);


--
-- Name: password_reset_captchas id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.password_reset_captchas ALTER COLUMN id SET DEFAULT nextval('public.password_reset_captchas_id_seq'::regclass);


--
-- Name: password_resets id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.password_resets ALTER COLUMN id SET DEFAULT nextval('public.password_resets_id_seq'::regclass);


--
-- Name: payment_gateways id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payment_gateways ALTER COLUMN id SET DEFAULT nextval('public.payment_gateways_id_seq'::regclass);


--
-- Name: payment_reminders id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payment_reminders ALTER COLUMN id SET DEFAULT nextval('public.payment_reminders_id_seq'::regclass);


--
-- Name: payments id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payments ALTER COLUMN id SET DEFAULT nextval('public.payments_id_seq'::regclass);


--
-- Name: pending_emails id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.pending_emails ALTER COLUMN id SET DEFAULT nextval('public.pending_emails_id_seq'::regclass);


--
-- Name: permissions id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.permissions ALTER COLUMN id SET DEFAULT nextval('public.permissions_id_seq'::regclass);


--
-- Name: plan_change_orders id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.plan_change_orders ALTER COLUMN id SET DEFAULT nextval('public.plan_change_orders_id_seq'::regclass);


--
-- Name: plans id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.plans ALTER COLUMN id SET DEFAULT nextval('public.plans_id_seq'::regclass);


--
-- Name: pon_ports id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.pon_ports ALTER COLUMN id SET DEFAULT nextval('public.pon_ports_id_seq'::regclass);


--
-- Name: profanity_violations id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.profanity_violations ALTER COLUMN id SET DEFAULT nextval('public.profanity_violations_id_seq'::regclass);


--
-- Name: public_ip_addresses id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.public_ip_addresses ALTER COLUMN id SET DEFAULT nextval('public.public_ip_addresses_id_seq'::regclass);


--
-- Name: push_tokens id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.push_tokens ALTER COLUMN id SET DEFAULT nextval('public.push_tokens_id_seq'::regclass);


--
-- Name: received_tracker id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.received_tracker ALTER COLUMN id SET DEFAULT nextval('public.received_tracker_id_seq'::regclass);


--
-- Name: referral_codes id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.referral_codes ALTER COLUMN id SET DEFAULT nextval('public.referral_codes_id_seq'::regclass);


--
-- Name: referrals id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.referrals ALTER COLUMN id SET DEFAULT nextval('public.referrals_id_seq'::regclass);


--
-- Name: renewal_logs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.renewal_logs ALTER COLUMN id SET DEFAULT nextval('public.renewal_logs_id_seq'::regclass);


--
-- Name: retention_runs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.retention_runs ALTER COLUMN id SET DEFAULT nextval('public.retention_runs_id_seq'::regclass);


--
-- Name: signal_degradation_events_v2 id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.signal_degradation_events_v2 ALTER COLUMN id SET DEFAULT nextval('public.signal_degradation_events_v2_id_seq'::regclass);


--
-- Name: smartnet_alerts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_alerts ALTER COLUMN id SET DEFAULT nextval('public.smartnet_alerts_id_seq'::regclass);


--
-- Name: smartnet_audit id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_audit ALTER COLUMN id SET DEFAULT nextval('public.smartnet_audit_id_seq'::regclass);


--
-- Name: smartnet_bandwidth id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_bandwidth ALTER COLUMN id SET DEFAULT nextval('public.smartnet_bandwidth_id_seq'::regclass);


--
-- Name: smartnet_catalog id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_catalog ALTER COLUMN id SET DEFAULT nextval('public.smartnet_catalog_id_seq'::regclass);


--
-- Name: smartnet_devices id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_devices ALTER COLUMN id SET DEFAULT nextval('public.smartnet_devices_id_seq'::regclass);


--
-- Name: smartnet_layouts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_layouts ALTER COLUMN id SET DEFAULT nextval('public.smartnet_layouts_id_seq'::regclass);


--
-- Name: smartnet_links id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_links ALTER COLUMN id SET DEFAULT nextval('public.smartnet_links_id_seq'::regclass);


--
-- Name: smartnet_notif_channels id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_notif_channels ALTER COLUMN id SET DEFAULT nextval('public.smartnet_notif_channels_id_seq'::regclass);


--
-- Name: smartnet_ports id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_ports ALTER COLUMN id SET DEFAULT nextval('public.smartnet_ports_id_seq'::regclass);


--
-- Name: sms_campaigns id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sms_campaigns ALTER COLUMN id SET DEFAULT nextval('public.sms_campaigns_id_seq'::regclass);


--
-- Name: sms_logs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sms_logs ALTER COLUMN id SET DEFAULT nextval('public.sms_logs_id_seq'::regclass);


--
-- Name: sub_lco_commissions id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_commissions ALTER COLUMN id SET DEFAULT nextval('public.sub_lco_commissions_id_seq'::regclass);


--
-- Name: sub_lco_locations id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_locations ALTER COLUMN id SET DEFAULT nextval('public.sub_lco_locations_id_seq'::regclass);


--
-- Name: sub_lco_locks id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_locks ALTER COLUMN id SET DEFAULT nextval('public.sub_lco_locks_id_seq'::regclass);


--
-- Name: sub_lco_payouts id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_payouts ALTER COLUMN id SET DEFAULT nextval('public.sub_lco_payouts_id_seq'::regclass);


--
-- Name: sub_lcos id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lcos ALTER COLUMN id SET DEFAULT nextval('public.sub_lcos_id_seq'::regclass);


--
-- Name: superadmin_packages id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmin_packages ALTER COLUMN id SET DEFAULT nextval('public.superadmin_packages_id_seq'::regclass);


--
-- Name: superadmin_settings id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmin_settings ALTER COLUMN id SET DEFAULT nextval('public.superadmin_settings_id_seq'::regclass);


--
-- Name: superadmins id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmins ALTER COLUMN id SET DEFAULT nextval('public.superadmins_id_seq'::regclass);


--
-- Name: support_responses id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.support_responses ALTER COLUMN id SET DEFAULT nextval('public.support_responses_id_seq'::regclass);


--
-- Name: support_tickets id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.support_tickets ALTER COLUMN id SET DEFAULT nextval('public.support_tickets_id_seq'::regclass);


--
-- Name: transactions id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.transactions ALTER COLUMN id SET DEFAULT nextval('public.transactions_id_seq'::regclass);


--
-- Name: url_logs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.url_logs ALTER COLUMN id SET DEFAULT nextval('public.url_logs_id_seq'::regclass);


--
-- Name: vlan_pool id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.vlan_pool ALTER COLUMN id SET DEFAULT nextval('public.vlan_pool_id_seq'::regclass);


--
-- Name: vlan_setup_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.vlan_setup_log ALTER COLUMN id SET DEFAULT nextval('public.vlan_setup_log_id_seq'::regclass);


--
-- Name: voip_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.voip_profiles ALTER COLUMN id SET DEFAULT nextval('public.voip_profiles_id_seq'::regclass);


--
-- Name: voucher_redemptions id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.voucher_redemptions ALTER COLUMN id SET DEFAULT nextval('public.voucher_redemptions_id_seq'::regclass);


--
-- Name: webhook_log id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.webhook_log ALTER COLUMN id SET DEFAULT nextval('public.webhook_log_id_seq'::regclass);


--
-- Name: website_block_targets id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.website_block_targets ALTER COLUMN id SET DEFAULT nextval('public.website_block_targets_id_seq'::regclass);


--
-- Name: website_blocks id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.website_blocks ALTER COLUMN id SET DEFAULT nextval('public.website_blocks_id_seq'::regclass);


--
-- Name: whatsapp_campaigns id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_campaigns ALTER COLUMN id SET DEFAULT nextval('public.whatsapp_campaigns_id_seq'::regclass);


--
-- Name: whatsapp_config id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_config ALTER COLUMN id SET DEFAULT nextval('public.whatsapp_config_id_seq'::regclass);


--
-- Name: whatsapp_message_logs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_message_logs ALTER COLUMN id SET DEFAULT nextval('public.whatsapp_message_logs_id_seq'::regclass);


--
-- Name: whatsapp_templates id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_templates ALTER COLUMN id SET DEFAULT nextval('public.whatsapp_templates_id_seq'::regclass);


--
-- Name: ztp_dhcp_option43_configs id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_dhcp_option43_configs ALTER COLUMN id SET DEFAULT nextval('public.ztp_dhcp_option43_configs_id_seq'::regclass);


--
-- Name: ztp_discovered_onus id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_discovered_onus ALTER COLUMN id SET DEFAULT nextval('public.ztp_discovered_onus_id_seq'::regclass);


--
-- Name: ztp_onu_customer_mapping id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_customer_mapping ALTER COLUMN id SET DEFAULT nextval('public.ztp_onu_customer_mapping_id_seq'::regclass);


--
-- Name: ztp_onu_profiles id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_profiles ALTER COLUMN id SET DEFAULT nextval('public.ztp_onu_profiles_id_seq'::regclass);


--
-- Name: ztp_state_audit id; Type: DEFAULT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_state_audit ALTER COLUMN id SET DEFAULT nextval('public.ztp_state_audit_id_seq'::regclass);


--
-- Name: access_request_logs access_request_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.access_request_logs
    ADD CONSTRAINT access_request_logs_pkey PRIMARY KEY (id);


--
-- Name: account_deletion_requests account_deletion_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.account_deletion_requests
    ADD CONSTRAINT account_deletion_requests_pkey PRIMARY KEY (id);


--
-- Name: acs_device_mapping acs_device_mapping_company_id_genieacs_device_id_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_device_mapping
    ADD CONSTRAINT acs_device_mapping_company_id_genieacs_device_id_key UNIQUE (company_id, genieacs_device_id);


--
-- Name: acs_device_mapping acs_device_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_device_mapping
    ADD CONSTRAINT acs_device_mapping_pkey PRIMARY KEY (id);


--
-- Name: acs_device_parameter_profiles acs_device_parameter_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_device_parameter_profiles
    ADD CONSTRAINT acs_device_parameter_profiles_pkey PRIMARY KEY (id);


--
-- Name: acs_push_log acs_push_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.acs_push_log
    ADD CONSTRAINT acs_push_log_pkey PRIMARY KEY (id);


--
-- Name: admin_activity_log admin_activity_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.admin_activity_log
    ADD CONSTRAINT admin_activity_log_pkey PRIMARY KEY (id);


--
-- Name: admins admins_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.admins
    ADD CONSTRAINT admins_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: captive_portal_settings captive_portal_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.captive_portal_settings
    ADD CONSTRAINT captive_portal_settings_pkey PRIMARY KEY (id);


--
-- Name: cms_mirror_config cms_mirror_config_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.cms_mirror_config
    ADD CONSTRAINT cms_mirror_config_pkey PRIMARY KEY (id);


--
-- Name: companies companies_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.companies
    ADD CONSTRAINT companies_pkey PRIMARY KEY (id);


--
-- Name: company_feature_flags company_feature_flags_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.company_feature_flags
    ADD CONSTRAINT company_feature_flags_pkey PRIMARY KEY (id);


--
-- Name: complaint_comments complaint_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaint_comments
    ADD CONSTRAINT complaint_comments_pkey PRIMARY KEY (id);


--
-- Name: complaint_responses complaint_responses_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaint_responses
    ADD CONSTRAINT complaint_responses_pkey PRIMARY KEY (id);


--
-- Name: complaints complaints_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.complaints
    ADD CONSTRAINT complaints_pkey PRIMARY KEY (id);


--
-- Name: compliance_ingest_tokens compliance_ingest_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.compliance_ingest_tokens
    ADD CONSTRAINT compliance_ingest_tokens_pkey PRIMARY KEY (id);


--
-- Name: connection_requests connection_requests_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.connection_requests
    ADD CONSTRAINT connection_requests_pkey PRIMARY KEY (id);


--
-- Name: customer_status_log customer_status_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.customer_status_log
    ADD CONSTRAINT customer_status_log_pkey PRIMARY KEY (id);


--
-- Name: customers customers_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.customers
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);


--
-- Name: data_mgmt_log data_mgmt_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.data_mgmt_log
    ADD CONSTRAINT data_mgmt_log_pkey PRIMARY KEY (id);


--
-- Name: db_backups db_backups_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.db_backups
    ADD CONSTRAINT db_backups_pkey PRIMARY KEY (id);


--
-- Name: dns_profiles dns_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.dns_profiles
    ADD CONSTRAINT dns_profiles_pkey PRIMARY KEY (id);


--
-- Name: dot_blocklist dot_blocklist_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.dot_blocklist
    ADD CONSTRAINT dot_blocklist_pkey PRIMARY KEY (id);


--
-- Name: employee_locality_assignments employee_locality_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_locality_assignments
    ADD CONSTRAINT employee_locality_assignments_pkey PRIMARY KEY (id);


--
-- Name: employee_location_history employee_location_history_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_location_history
    ADD CONSTRAINT employee_location_history_pkey PRIMARY KEY (id);


--
-- Name: employee_permissions employee_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_permissions
    ADD CONSTRAINT employee_permissions_pkey PRIMARY KEY (id);


--
-- Name: employee_sequences employee_sequences_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employee_sequences
    ADD CONSTRAINT employee_sequences_pkey PRIMARY KEY (id);


--
-- Name: employees employees_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_pkey PRIMARY KEY (id);


--
-- Name: expenses expenses_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_pkey PRIMARY KEY (id);


--
-- Name: fiber_cut_history fiber_cut_history_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.fiber_cut_history
    ADD CONSTRAINT fiber_cut_history_pkey PRIMARY KEY (id);


--
-- Name: fiber_splice fiber_splice_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.fiber_splice
    ADD CONSTRAINT fiber_splice_pkey PRIMARY KEY (id);


--
-- Name: geofence_events geofence_events_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.geofence_events
    ADD CONSTRAINT geofence_events_pkey PRIMARY KEY (id);


--
-- Name: geofences geofences_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.geofences
    ADD CONSTRAINT geofences_pkey PRIMARY KEY (id);


--
-- Name: hotspot_vouchers hotspot_vouchers_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.hotspot_vouchers
    ADD CONSTRAINT hotspot_vouchers_pkey PRIMARY KEY (id);


--
-- Name: invoice_reminder_log invoice_reminder_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoice_reminder_log
    ADD CONSTRAINT invoice_reminder_log_pkey PRIMARY KEY (id);


--
-- Name: invoice_sequences invoice_sequences_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoice_sequences
    ADD CONSTRAINT invoice_sequences_pkey PRIMARY KEY (id);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: ip_pools ip_pools_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ip_pools
    ADD CONSTRAINT ip_pools_pkey PRIMARY KEY (id);


--
-- Name: ipdr_records ipdr_records_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ipdr_records
    ADD CONSTRAINT ipdr_records_pkey PRIMARY KEY (id);


--
-- Name: iptv_profiles iptv_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.iptv_profiles
    ADD CONSTRAINT iptv_profiles_pkey PRIMARY KEY (id);


--
-- Name: li_orders li_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.li_orders
    ADD CONSTRAINT li_orders_pkey PRIMARY KEY (id);


--
-- Name: load_balancing_configs load_balancing_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.load_balancing_configs
    ADD CONSTRAINT load_balancing_configs_pkey PRIMARY KEY (id);


--
-- Name: locations locations_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.locations
    ADD CONSTRAINT locations_pkey PRIMARY KEY (id);


--
-- Name: lock_secrets lock_secrets_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.lock_secrets
    ADD CONSTRAINT lock_secrets_pkey PRIMARY KEY (id);


--
-- Name: login_events login_events_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.login_events
    ADD CONSTRAINT login_events_pkey PRIMARY KEY (id);


--
-- Name: mdu_buildings mdu_buildings_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_buildings
    ADD CONSTRAINT mdu_buildings_pkey PRIMARY KEY (id);


--
-- Name: mdu_floors mdu_floors_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_floors
    ADD CONSTRAINT mdu_floors_pkey PRIMARY KEY (id);


--
-- Name: mdu_unit_customers mdu_unit_customers_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_unit_customers
    ADD CONSTRAINT mdu_unit_customers_pkey PRIMARY KEY (id);


--
-- Name: mdu_units mdu_units_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mdu_units
    ADD CONSTRAINT mdu_units_pkey PRIMARY KEY (id);


--
-- Name: mobile_login_banners mobile_login_banners_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.mobile_login_banners
    ADD CONSTRAINT mobile_login_banners_pkey PRIMARY KEY (id);


--
-- Name: nas_compliance_deployments nas_compliance_deployments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nas_compliance_deployments
    ADD CONSTRAINT nas_compliance_deployments_pkey PRIMARY KEY (id);


--
-- Name: nas_devices nas_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nas_devices
    ADD CONSTRAINT nas_devices_pkey PRIMARY KEY (id);


--
-- Name: nat_configs nat_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nat_configs
    ADD CONSTRAINT nat_configs_pkey PRIMARY KEY (id);


--
-- Name: nat_one_to_one_pairs nat_one_to_one_pairs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.nat_one_to_one_pairs
    ADD CONSTRAINT nat_one_to_one_pairs_pkey PRIMARY KEY (id);


--
-- Name: network_fiber network_fiber_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_fiber
    ADD CONSTRAINT network_fiber_pkey PRIMARY KEY (id);


--
-- Name: network_hardware network_hardware_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_hardware
    ADD CONSTRAINT network_hardware_pkey PRIMARY KEY (id);


--
-- Name: network_seed_markers network_seed_markers_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.network_seed_markers
    ADD CONSTRAINT network_seed_markers_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: olt_alerts olt_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olt_alerts
    ADD CONSTRAINT olt_alerts_pkey PRIMARY KEY (id);


--
-- Name: olt_polls olt_polls_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olt_polls
    ADD CONSTRAINT olt_polls_pkey PRIMARY KEY (id);


--
-- Name: olts olts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.olts
    ADD CONSTRAINT olts_pkey PRIMARY KEY (id);


--
-- Name: online_users online_users_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.online_users
    ADD CONSTRAINT online_users_pkey PRIMARY KEY (id);


--
-- Name: onu_config_snapshots onu_config_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_config_snapshots
    ADD CONSTRAINT onu_config_snapshots_pkey PRIMARY KEY (id);


--
-- Name: onu_iptv_assignments onu_iptv_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_iptv_assignments
    ADD CONSTRAINT onu_iptv_assignments_pkey PRIMARY KEY (id);


--
-- Name: onu_mac_table onu_mac_table_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_mac_table
    ADD CONSTRAINT onu_mac_table_pkey PRIMARY KEY (id);


--
-- Name: onu_service_profiles onu_service_profiles_company_id_name_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_service_profiles
    ADD CONSTRAINT onu_service_profiles_company_id_name_key UNIQUE (company_id, name);


--
-- Name: onu_service_profiles onu_service_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_service_profiles
    ADD CONSTRAINT onu_service_profiles_pkey PRIMARY KEY (id);


--
-- Name: onu_signal_alerts onu_signal_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_signal_alerts
    ADD CONSTRAINT onu_signal_alerts_pkey PRIMARY KEY (id);


--
-- Name: onu_signal_samples onu_signal_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_signal_samples
    ADD CONSTRAINT onu_signal_samples_pkey PRIMARY KEY (id);


--
-- Name: onu_traffic_samples onu_traffic_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_traffic_samples
    ADD CONSTRAINT onu_traffic_samples_pkey PRIMARY KEY (id);


--
-- Name: onu_voip_assignments onu_voip_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onu_voip_assignments
    ADD CONSTRAINT onu_voip_assignments_pkey PRIMARY KEY (id);


--
-- Name: onus onus_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onus
    ADD CONSTRAINT onus_pkey PRIMARY KEY (id);


--
-- Name: outage_event_onus_v2 outage_event_onus_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_event_onus_v2
    ADD CONSTRAINT outage_event_onus_v2_pkey PRIMARY KEY (id);


--
-- Name: outage_events outage_events_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_events
    ADD CONSTRAINT outage_events_pkey PRIMARY KEY (id);


--
-- Name: outage_events_v2 outage_events_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_events_v2
    ADD CONSTRAINT outage_events_v2_pkey PRIMARY KEY (id);


--
-- Name: outage_notifications_v2 outage_notifications_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.outage_notifications_v2
    ADD CONSTRAINT outage_notifications_v2_pkey PRIMARY KEY (id);


--
-- Name: password_reset_captchas password_reset_captchas_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.password_reset_captchas
    ADD CONSTRAINT password_reset_captchas_pkey PRIMARY KEY (id);


--
-- Name: password_resets password_resets_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.password_resets
    ADD CONSTRAINT password_resets_pkey PRIMARY KEY (id);


--
-- Name: payment_gateways payment_gateways_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payment_gateways
    ADD CONSTRAINT payment_gateways_pkey PRIMARY KEY (id);


--
-- Name: payment_reminders payment_reminders_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payment_reminders
    ADD CONSTRAINT payment_reminders_pkey PRIMARY KEY (id);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (id);


--
-- Name: pending_emails pending_emails_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.pending_emails
    ADD CONSTRAINT pending_emails_pkey PRIMARY KEY (id);


--
-- Name: permissions permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.permissions
    ADD CONSTRAINT permissions_pkey PRIMARY KEY (id);


--
-- Name: plan_change_orders plan_change_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.plan_change_orders
    ADD CONSTRAINT plan_change_orders_pkey PRIMARY KEY (id);


--
-- Name: plans plans_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.plans
    ADD CONSTRAINT plans_pkey PRIMARY KEY (id);


--
-- Name: pon_ports pon_ports_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.pon_ports
    ADD CONSTRAINT pon_ports_pkey PRIMARY KEY (id);


--
-- Name: profanity_violations profanity_violations_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.profanity_violations
    ADD CONSTRAINT profanity_violations_pkey PRIMARY KEY (id);


--
-- Name: public_ip_addresses public_ip_addresses_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.public_ip_addresses
    ADD CONSTRAINT public_ip_addresses_pkey PRIMARY KEY (id);


--
-- Name: push_tokens push_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.push_tokens
    ADD CONSTRAINT push_tokens_pkey PRIMARY KEY (id);


--
-- Name: received_tracker received_tracker_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.received_tracker
    ADD CONSTRAINT received_tracker_pkey PRIMARY KEY (id);


--
-- Name: referral_codes referral_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.referral_codes
    ADD CONSTRAINT referral_codes_pkey PRIMARY KEY (id);


--
-- Name: referrals referrals_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.referrals
    ADD CONSTRAINT referrals_pkey PRIMARY KEY (id);


--
-- Name: renewal_logs renewal_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.renewal_logs
    ADD CONSTRAINT renewal_logs_pkey PRIMARY KEY (id);


--
-- Name: retention_runs retention_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.retention_runs
    ADD CONSTRAINT retention_runs_pkey PRIMARY KEY (id);


--
-- Name: signal_degradation_events_v2 signal_degradation_events_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.signal_degradation_events_v2
    ADD CONSTRAINT signal_degradation_events_v2_pkey PRIMARY KEY (id);


--
-- Name: smartnet_alerts smartnet_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_alerts
    ADD CONSTRAINT smartnet_alerts_pkey PRIMARY KEY (id);


--
-- Name: smartnet_audit smartnet_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_audit
    ADD CONSTRAINT smartnet_audit_pkey PRIMARY KEY (id);


--
-- Name: smartnet_bandwidth smartnet_bandwidth_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_bandwidth
    ADD CONSTRAINT smartnet_bandwidth_pkey PRIMARY KEY (id);


--
-- Name: smartnet_catalog smartnet_catalog_brand_model_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_catalog
    ADD CONSTRAINT smartnet_catalog_brand_model_key UNIQUE (brand, model);


--
-- Name: smartnet_catalog smartnet_catalog_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_catalog
    ADD CONSTRAINT smartnet_catalog_pkey PRIMARY KEY (id);


--
-- Name: smartnet_devices smartnet_devices_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_devices
    ADD CONSTRAINT smartnet_devices_pkey PRIMARY KEY (id);


--
-- Name: smartnet_layouts smartnet_layouts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_layouts
    ADD CONSTRAINT smartnet_layouts_pkey PRIMARY KEY (id);


--
-- Name: smartnet_links smartnet_links_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_links
    ADD CONSTRAINT smartnet_links_pkey PRIMARY KEY (id);


--
-- Name: smartnet_notif_channels smartnet_notif_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_notif_channels
    ADD CONSTRAINT smartnet_notif_channels_pkey PRIMARY KEY (id);


--
-- Name: smartnet_ports smartnet_ports_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_ports
    ADD CONSTRAINT smartnet_ports_pkey PRIMARY KEY (id);


--
-- Name: sms_campaigns sms_campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sms_campaigns
    ADD CONSTRAINT sms_campaigns_pkey PRIMARY KEY (id);


--
-- Name: sms_logs sms_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sms_logs
    ADD CONSTRAINT sms_logs_pkey PRIMARY KEY (id);


--
-- Name: sub_lco_commissions sub_lco_commissions_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_commissions
    ADD CONSTRAINT sub_lco_commissions_pkey PRIMARY KEY (id);


--
-- Name: sub_lco_locations sub_lco_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_locations
    ADD CONSTRAINT sub_lco_locations_pkey PRIMARY KEY (id);


--
-- Name: sub_lco_locks sub_lco_locks_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_locks
    ADD CONSTRAINT sub_lco_locks_pkey PRIMARY KEY (id);


--
-- Name: sub_lco_payouts sub_lco_payouts_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lco_payouts
    ADD CONSTRAINT sub_lco_payouts_pkey PRIMARY KEY (id);


--
-- Name: sub_lcos sub_lcos_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.sub_lcos
    ADD CONSTRAINT sub_lcos_pkey PRIMARY KEY (id);


--
-- Name: superadmin_packages superadmin_packages_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmin_packages
    ADD CONSTRAINT superadmin_packages_pkey PRIMARY KEY (id);


--
-- Name: superadmin_settings superadmin_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmin_settings
    ADD CONSTRAINT superadmin_settings_pkey PRIMARY KEY (id);


--
-- Name: superadmins superadmins_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.superadmins
    ADD CONSTRAINT superadmins_pkey PRIMARY KEY (id);


--
-- Name: support_responses support_responses_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.support_responses
    ADD CONSTRAINT support_responses_pkey PRIMARY KEY (id);


--
-- Name: support_tickets support_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.support_tickets
    ADD CONSTRAINT support_tickets_pkey PRIMARY KEY (id);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: url_logs url_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.url_logs
    ADD CONSTRAINT url_logs_pkey PRIMARY KEY (id);


--
-- Name: vlan_pool vlan_pool_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.vlan_pool
    ADD CONSTRAINT vlan_pool_pkey PRIMARY KEY (id);


--
-- Name: vlan_setup_log vlan_setup_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.vlan_setup_log
    ADD CONSTRAINT vlan_setup_log_pkey PRIMARY KEY (id);


--
-- Name: voip_profiles voip_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.voip_profiles
    ADD CONSTRAINT voip_profiles_pkey PRIMARY KEY (id);


--
-- Name: voucher_redemptions voucher_redemptions_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.voucher_redemptions
    ADD CONSTRAINT voucher_redemptions_pkey PRIMARY KEY (id);


--
-- Name: webhook_log webhook_log_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.webhook_log
    ADD CONSTRAINT webhook_log_pkey PRIMARY KEY (id);


--
-- Name: website_block_targets website_block_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.website_block_targets
    ADD CONSTRAINT website_block_targets_pkey PRIMARY KEY (id);


--
-- Name: website_blocks website_blocks_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.website_blocks
    ADD CONSTRAINT website_blocks_pkey PRIMARY KEY (id);


--
-- Name: whatsapp_campaigns whatsapp_campaigns_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_campaigns
    ADD CONSTRAINT whatsapp_campaigns_pkey PRIMARY KEY (id);


--
-- Name: whatsapp_config whatsapp_config_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_config
    ADD CONSTRAINT whatsapp_config_pkey PRIMARY KEY (id);


--
-- Name: whatsapp_message_logs whatsapp_message_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_message_logs
    ADD CONSTRAINT whatsapp_message_logs_pkey PRIMARY KEY (id);


--
-- Name: whatsapp_templates whatsapp_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.whatsapp_templates
    ADD CONSTRAINT whatsapp_templates_pkey PRIMARY KEY (id);


--
-- Name: ztp_dhcp_option43_configs ztp_dhcp_option43_configs_company_id_nas_id_vlan_id_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_dhcp_option43_configs
    ADD CONSTRAINT ztp_dhcp_option43_configs_company_id_nas_id_vlan_id_key UNIQUE (company_id, nas_id, vlan_id);


--
-- Name: ztp_dhcp_option43_configs ztp_dhcp_option43_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_dhcp_option43_configs
    ADD CONSTRAINT ztp_dhcp_option43_configs_pkey PRIMARY KEY (id);


--
-- Name: ztp_discovered_onus ztp_discovered_onus_company_id_olt_id_pon_port_onu_serial_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_discovered_onus
    ADD CONSTRAINT ztp_discovered_onus_company_id_olt_id_pon_port_onu_serial_key UNIQUE (company_id, olt_id, pon_port, onu_serial);


--
-- Name: ztp_discovered_onus ztp_discovered_onus_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_discovered_onus
    ADD CONSTRAINT ztp_discovered_onus_pkey PRIMARY KEY (id);


--
-- Name: ztp_onu_customer_mapping ztp_onu_customer_mapping_company_id_onu_serial_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_customer_mapping
    ADD CONSTRAINT ztp_onu_customer_mapping_company_id_onu_serial_key UNIQUE (company_id, onu_serial);


--
-- Name: ztp_onu_customer_mapping ztp_onu_customer_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_customer_mapping
    ADD CONSTRAINT ztp_onu_customer_mapping_pkey PRIMARY KEY (id);


--
-- Name: ztp_onu_profiles ztp_onu_profiles_company_id_profile_name_key; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_profiles
    ADD CONSTRAINT ztp_onu_profiles_company_id_profile_name_key UNIQUE (company_id, profile_name);


--
-- Name: ztp_onu_profiles ztp_onu_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_onu_profiles
    ADD CONSTRAINT ztp_onu_profiles_pkey PRIMARY KEY (id);


--
-- Name: ztp_state_audit ztp_state_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.ztp_state_audit
    ADD CONSTRAINT ztp_state_audit_pkey PRIMARY KEY (id);


--
-- Name: idx_acs_push_log_co; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_acs_push_log_co ON public.acs_push_log USING btree (company_id, id DESC);


--
-- Name: idx_acs_push_log_onu; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_acs_push_log_onu ON public.acs_push_log USING btree (onu_id, id DESC);


--
-- Name: idx_alerts_company_acked; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_alerts_company_acked ON public.olt_alerts USING btree (company_id, acked, created_at DESC);


--
-- Name: idx_alerts_company_open; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_alerts_company_open ON public.onu_signal_alerts USING btree (company_id, closed_at);


--
-- Name: idx_alerts_onu; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_alerts_onu ON public.onu_signal_alerts USING btree (onu_id);


--
-- Name: idx_api_keys_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_api_keys_company ON public.api_keys USING btree (company_id);


--
-- Name: idx_blocks_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_blocks_company ON public.website_blocks USING btree (company_id);


--
-- Name: idx_blocks_targets_block; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_blocks_targets_block ON public.website_block_targets USING btree (block_id);


--
-- Name: idx_blocks_targets_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_blocks_targets_company ON public.website_block_targets USING btree (company_id);


--
-- Name: idx_company_invoice_unique; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX idx_company_invoice_unique ON public.invoices USING btree (company_id, invoice_no);


--
-- Name: idx_cuthist_node; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_cuthist_node ON public.fiber_cut_history USING btree (company_id, node_hw_id);


--
-- Name: idx_expenses_company_category; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_expenses_company_category ON public.expenses USING btree (company_id, category);


--
-- Name: idx_expenses_company_date; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_expenses_company_date ON public.expenses USING btree (company_id, expense_date);


--
-- Name: idx_geofences_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_geofences_company ON public.geofences USING btree (company_id, is_active);


--
-- Name: idx_gevents_company_at; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_gevents_company_at ON public.geofence_events USING btree (company_id, recorded_at);


--
-- Name: idx_gevents_emp_geo; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_gevents_emp_geo ON public.geofence_events USING btree (employee_id, geofence_id, recorded_at);


--
-- Name: idx_ingest_token_hash; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX idx_ingest_token_hash ON public.compliance_ingest_tokens USING btree (token_hash);


--
-- Name: idx_ipdr_company_start; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_ipdr_company_start ON public.ipdr_records USING btree (company_id, start_ts);


--
-- Name: idx_ipdr_user_ip; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_ipdr_user_ip ON public.ipdr_records USING btree (company_id, user_ip);


--
-- Name: idx_lb_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_lb_company ON public.load_balancing_configs USING btree (company_id);


--
-- Name: idx_lb_company_nas; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX idx_lb_company_nas ON public.load_balancing_configs USING btree (company_id, nas_id);


--
-- Name: idx_lock_lookup; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_lock_lookup ON public.sub_lco_locks USING btree (company_id, sub_lco_id);


--
-- Name: idx_mac_onu; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mac_onu ON public.onu_mac_table USING btree (onu_id);


--
-- Name: idx_mdu_b_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mdu_b_company ON public.mdu_buildings USING btree (company_id);


--
-- Name: idx_mdu_f_building; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mdu_f_building ON public.mdu_floors USING btree (building_id);


--
-- Name: idx_mdu_u_floor; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mdu_u_floor ON public.mdu_units USING btree (floor_id);


--
-- Name: idx_mdu_uc_cust; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mdu_uc_cust ON public.mdu_unit_customers USING btree (customer_id);


--
-- Name: idx_mdu_uc_unit; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_mdu_uc_unit ON public.mdu_unit_customers USING btree (unit_id);


--
-- Name: idx_nasdep_lookup; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_nasdep_lookup ON public.nas_compliance_deployments USING btree (company_id, nas_id, kind);


--
-- Name: idx_nat_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_nat_company ON public.nat_configs USING btree (company_id);


--
-- Name: idx_nat_pairs_cfg; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_nat_pairs_cfg ON public.nat_one_to_one_pairs USING btree (nat_config_id);


--
-- Name: idx_nat_pairs_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_nat_pairs_company ON public.nat_one_to_one_pairs USING btree (company_id);


--
-- Name: idx_netfiber_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_netfiber_company ON public.network_fiber USING btree (company_id);


--
-- Name: idx_nethw_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_nethw_company ON public.network_hardware USING btree (company_id, kind);


--
-- Name: idx_onu_cfg_snap_onu; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onu_cfg_snap_onu ON public.onu_config_snapshots USING btree (company_id, onu_id, kind, id DESC);


--
-- Name: idx_onu_cfg_snap_serial; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onu_cfg_snap_serial ON public.onu_config_snapshots USING btree (company_id, serial, kind, id DESC);


--
-- Name: idx_onus_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onus_company ON public.onus USING btree (company_id, olt_id);


--
-- Name: idx_onus_mac_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onus_mac_company ON public.onus USING btree (mac, company_id);


--
-- Name: idx_onus_serial_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onus_serial_company ON public.onus USING btree (serial, company_id);


--
-- Name: idx_onus_status; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_onus_status ON public.onus USING btree (company_id, status);


--
-- Name: idx_outage_v2_company_open; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_outage_v2_company_open ON public.outage_events_v2 USING btree (company_id, closed_at);


--
-- Name: idx_outage_v2_event; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_outage_v2_event ON public.outage_event_onus_v2 USING btree (event_id);


--
-- Name: idx_outage_v2_onu; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_outage_v2_onu ON public.outage_event_onus_v2 USING btree (onu_id);


--
-- Name: idx_outnotif_v2_event; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_outnotif_v2_event ON public.outage_notifications_v2 USING btree (event_id);


--
-- Name: idx_polls_olt_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_polls_olt_ts ON public.olt_polls USING btree (olt_id, ts DESC);


--
-- Name: idx_retention_runs_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_retention_runs_company ON public.retention_runs USING btree (company_id);


--
-- Name: idx_sd_v2_company_open; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_sd_v2_company_open ON public.signal_degradation_events_v2 USING btree (company_id, closed_at);


--
-- Name: idx_signal_onu_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_signal_onu_ts ON public.onu_signal_samples USING btree (onu_id, ts);


--
-- Name: idx_smartnet_alerts_cid; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_alerts_cid ON public.smartnet_alerts USING btree (company_id, status, created_at DESC);


--
-- Name: idx_smartnet_bw_cid_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_bw_cid_ts ON public.smartnet_bandwidth USING btree (company_id, ts DESC);


--
-- Name: idx_smartnet_devices_cid; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_devices_cid ON public.smartnet_devices USING btree (company_id);


--
-- Name: idx_smartnet_devices_type; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_devices_type ON public.smartnet_devices USING btree (company_id, type);


--
-- Name: idx_smartnet_links_cid; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_links_cid ON public.smartnet_links USING btree (company_id);


--
-- Name: idx_smartnet_links_dst; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_links_dst ON public.smartnet_links USING btree (dst_device_id);


--
-- Name: idx_smartnet_links_src; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_links_src ON public.smartnet_links USING btree (src_device_id);


--
-- Name: idx_smartnet_notif_uniq; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX idx_smartnet_notif_uniq ON public.smartnet_notif_channels USING btree (company_id, channel);


--
-- Name: idx_smartnet_ports_dev; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_smartnet_ports_dev ON public.smartnet_ports USING btree (device_id);


--
-- Name: idx_smartnet_ports_uniq; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX idx_smartnet_ports_uniq ON public.smartnet_ports USING btree (device_id, port_name);


--
-- Name: idx_splice_node; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_splice_node ON public.fiber_splice USING btree (company_id, node_hw_id);


--
-- Name: idx_sub_lco_comm_company; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_sub_lco_comm_company ON public.sub_lco_commissions USING btree (company_id, sub_lco_id, created_at);


--
-- Name: idx_traffic_company_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_traffic_company_ts ON public.onu_traffic_samples USING btree (company_id, ts);


--
-- Name: idx_traffic_onu_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_traffic_onu_ts ON public.onu_traffic_samples USING btree (onu_id, ts);


--
-- Name: idx_url_company_ts; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_url_company_ts ON public.url_logs USING btree (company_id, ts);


--
-- Name: idx_url_host; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX idx_url_host ON public.url_logs USING btree (company_id, host);


--
-- Name: ix_acs_dev_serial; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_acs_dev_serial ON public.acs_device_mapping USING btree (company_id, upper(onu_serial));


--
-- Name: ix_acs_dev_status; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_acs_dev_status ON public.acs_device_mapping USING btree (company_id, status);


--
-- Name: ix_acs_paramprof_match; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_acs_paramprof_match ON public.acs_device_parameter_profiles USING btree (COALESCE(vendor, '*'::text), COALESCE(model, '*'::text), COALESCE(product_class, '*'::text), priority);


--
-- Name: ix_lev_login_at; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_lev_login_at ON public.login_events USING btree (login_at);


--
-- Name: ix_lev_role_at; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_lev_role_at ON public.login_events USING btree (actor_type, login_at);


--
-- Name: ix_sact_seen; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_sact_seen ON public.session_activity USING btree (last_seen_at);


--
-- Name: ix_ztp_audit_serial; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_ztp_audit_serial ON public.ztp_state_audit USING btree (company_id, onu_serial, created_at DESC);


--
-- Name: ix_ztp_disc_company_status; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_ztp_disc_company_status ON public.ztp_discovered_onus USING btree (company_id, status);


--
-- Name: ix_ztp_disc_serial; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_ztp_disc_serial ON public.ztp_discovered_onus USING btree (upper(onu_serial));


--
-- Name: ix_ztp_map_cust; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_ztp_map_cust ON public.ztp_onu_customer_mapping USING btree (company_id, customer_id);


--
-- Name: ix_ztp_map_status; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE INDEX ix_ztp_map_status ON public.ztp_onu_customer_mapping USING btree (company_id, status);


--
-- Name: uniq_inv_reminder_stage; Type: INDEX; Schema: public; Owner: ispbilling
--

CREATE UNIQUE INDEX uniq_inv_reminder_stage ON public.invoice_reminder_log USING btree (invoice_no, stage) WHERE (dry_run = 0);


--
-- Name: onus onus_service_profile_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.onus
    ADD CONSTRAINT onus_service_profile_id_fkey FOREIGN KEY (service_profile_id) REFERENCES public.onu_service_profiles(id) ON DELETE SET NULL;


--
-- Name: smartnet_alerts smartnet_alerts_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_alerts
    ADD CONSTRAINT smartnet_alerts_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.smartnet_devices(id) ON DELETE CASCADE;


--
-- Name: smartnet_alerts smartnet_alerts_port_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_alerts
    ADD CONSTRAINT smartnet_alerts_port_id_fkey FOREIGN KEY (port_id) REFERENCES public.smartnet_ports(id) ON DELETE CASCADE;


--
-- Name: smartnet_links smartnet_links_dst_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_links
    ADD CONSTRAINT smartnet_links_dst_device_id_fkey FOREIGN KEY (dst_device_id) REFERENCES public.smartnet_devices(id) ON DELETE CASCADE;


--
-- Name: smartnet_links smartnet_links_src_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_links
    ADD CONSTRAINT smartnet_links_src_device_id_fkey FOREIGN KEY (src_device_id) REFERENCES public.smartnet_devices(id) ON DELETE CASCADE;


--
-- Name: smartnet_ports smartnet_ports_device_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: ispbilling
--

ALTER TABLE ONLY public.smartnet_ports
    ADD CONSTRAINT smartnet_ports_device_id_fkey FOREIGN KEY (device_id) REFERENCES public.smartnet_devices(id) ON DELETE CASCADE;


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: ispbilling
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO PUBLIC;


--
-- PostgreSQL database dump complete
--

\unrestrict NDz18aRWxDGcwv2VXxHusSuxS4RIJBMyc2X5se27kF5dPlMESBtqKxDPcOLKUnS

