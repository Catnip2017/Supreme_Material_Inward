--
-- PostgreSQL database dump
--

\restrict JXNs0A3EJAZoLM2ZeIFe7wMtquzIhXseOfrC6tjAvbZWwb2IbU5s6XcSJupU0o0

-- Dumped from database version 17.9
-- Dumped by pg_dump version 17.9

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: dblink; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS dblink WITH SCHEMA public;


--
-- Name: EXTENSION dblink; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION dblink IS 'connect to other PostgreSQL databases from within a database';


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_action_log; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.admin_action_log (
    id integer NOT NULL,
    history_id integer NOT NULL,
    action character varying(50) NOT NULL,
    details text,
    performed_by character varying(255) NOT NULL,
    performed_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.admin_action_log OWNER TO material_user;

--
-- Name: admin_action_log_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.admin_action_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admin_action_log_id_seq OWNER TO material_user;

--
-- Name: admin_action_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.admin_action_log_id_seq OWNED BY public.admin_action_log.id;


--
-- Name: dms_document_links; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.dms_document_links (
    id integer NOT NULL,
    history_id integer,
    filename text NOT NULL,
    document_link text NOT NULL,
    imported_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.dms_document_links OWNER TO material_user;

--
-- Name: dms_document_links_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.dms_document_links_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dms_document_links_id_seq OWNER TO material_user;

--
-- Name: dms_document_links_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.dms_document_links_id_seq OWNED BY public.dms_document_links.id;


--
-- Name: ewaybill_data; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.ewaybill_data (
    id integer NOT NULL,
    filename text,
    ewaybill_number text,
    generated_date text,
    validity_date text,
    invoice_number text,
    invoice_date text,
    po_number text,
    goods_description text,
    hsn_code text,
    quantity text,
    value_of_goods text,
    dispatch_from text,
    dispatch_to text,
    total_taxable_amount text,
    total_invoice_amount text,
    transport_mode text,
    vehicle_number text,
    transporter_name text,
    transporter_gstin text,
    transport_doc_no text,
    transport_doc_date text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.ewaybill_data OWNER TO material_user;

--
-- Name: COLUMN ewaybill_data.goods_description; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.goods_description IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';


--
-- Name: COLUMN ewaybill_data.hsn_code; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.hsn_code IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';


--
-- Name: COLUMN ewaybill_data.quantity; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.quantity IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';


--
-- Name: COLUMN ewaybill_data.value_of_goods; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.value_of_goods IS 'DEPRECATED v7 — goods info now lives in invoice_data.hsn_details. Frozen; scheduled for drop in Stage 2.';


--
-- Name: COLUMN ewaybill_data.total_taxable_amount; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.total_taxable_amount IS 'DEPRECATED v7 — totals shown from invoice_data. Frozen; scheduled for drop in Stage 2.';


--
-- Name: COLUMN ewaybill_data.total_invoice_amount; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.ewaybill_data.total_invoice_amount IS 'DEPRECATED v7 — totals shown from invoice_data. Frozen; scheduled for drop in Stage 2.';


--
-- Name: gate_in_entries; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.gate_in_entries (
    id integer NOT NULL,
    history_id integer NOT NULL,
    gate_in_date text,
    gate_in_time text,
    vendor_name text,
    transporter text,
    truck_no text,
    driver_name text,
    license_no text,
    num_persons text,
    container_no text,
    category text,
    material text,
    challan_no text,
    challan_qty text,
    boe_no text,
    purchase_order text,
    gate_pass_no text,
    note text,
    weight_option text,
    gate_in_number text,
    rf_status character varying(50) DEFAULT 'pending'::character varying,
    rf_error_message text,
    rf_executed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    submitted_by character varying(255)
);


ALTER TABLE public.gate_in_entries OWNER TO material_user;

--
-- Name: gate_in_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.gate_in_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.gate_in_entries_id_seq OWNER TO material_user;

--
-- Name: gate_in_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.gate_in_entries_id_seq OWNED BY public.gate_in_entries.id;


--
-- Name: gst_approval; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.gst_approval (
    id integer NOT NULL,
    history_id integer NOT NULL,
    einvoice_status character varying(150),
    einvoice_screenshot text,
    gstin_status character varying(50),
    legal_name character varying(200),
    taxpayer_type character varying(100),
    gstr3b_last_filed character varying(20),
    gstr3b_tax_period character varying(50),
    gstr3b_status character varying(50),
    gstr1_last_filed character varying(20),
    gstr1_tax_period character varying(50),
    gstr1_status character varying(50),
    taxpayer_screenshot text,
    approval_status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    approval_by character varying(100),
    approval_at timestamp without time zone,
    hold_reason text,
    bot_error text,
    checked_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    auto_retry_exhausted boolean DEFAULT false NOT NULL
);


ALTER TABLE public.gst_approval OWNER TO material_user;

--
-- Name: gst_approval_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.gst_approval_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.gst_approval_id_seq OWNER TO material_user;

--
-- Name: gst_approval_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.gst_approval_id_seq OWNED BY public.gst_approval.id;


--
-- Name: history; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.history (
    id integer NOT NULL,
    invoice_number text,
    ewaybill_number text,
    lr_number text,
    po_number text,
    mail_subject text,
    mail_received_at timestamp without time zone,
    gate_in smallint DEFAULT 0,
    migo_103 smallint DEFAULT 0,
    migo_105 smallint DEFAULT 0,
    miro smallint DEFAULT 0,
    gate_in_number text,
    material_doc_number text,
    bill_number text,
    gatein_done_at timestamp without time zone,
    migo_103_done_at timestamp without time zone,
    migo_105_done_at timestamp without time zone,
    miro_done_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    approval_status character varying(20) DEFAULT 'pending'::character varying,
    approval_by character varying(255),
    approval_at timestamp without time zone,
    hold_reason text,
    ocr_status character varying(20) DEFAULT 'success'::character varying,
    ocr_retry_count integer DEFAULT 0,
    ocr_failed_path text,
    po_flow_type character varying(30) DEFAULT 'truck_with_po'::character varying,
    dms_status character varying(20) DEFAULT NULL::character varying,
    dms_staged_at timestamp without time zone,
    consolidated_doc_path text,
    migo_105_doc_number text,
    miro_fi_doc_number text,
    gst_check integer DEFAULT 0,
    gst_check_done_at timestamp without time zone,
    goods_delivery_mode character varying(30),
    goods_delivery_mode_by character varying(255),
    goods_delivery_mode_at timestamp without time zone,
    ewb_exemption_reasons character varying(255),
    ewb_exemption_reasons_by character varying(255),
    ewb_exemption_reasons_at timestamp without time zone
);


ALTER TABLE public.history OWNER TO material_user;

--
-- Name: history_comments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.history_comments (
    id integer NOT NULL,
    history_id integer NOT NULL,
    role character varying(50) NOT NULL,
    comment_text text NOT NULL,
    created_by character varying(255),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.history_comments OWNER TO postgres;

--
-- Name: history_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.history_comments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.history_comments_id_seq OWNER TO postgres;

--
-- Name: history_comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.history_comments_id_seq OWNED BY public.history_comments.id;


--
-- Name: history_extras; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.history_extras (
    id integer NOT NULL,
    history_id integer NOT NULL,
    filename character varying(500) NOT NULL,
    original_filename character varying(500),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    doc_type character varying(20) DEFAULT 'extra'::character varying
);


ALTER TABLE public.history_extras OWNER TO material_user;

--
-- Name: history_extras_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.history_extras_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.history_extras_id_seq OWNER TO material_user;

--
-- Name: history_extras_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.history_extras_id_seq OWNED BY public.history_extras.id;


--
-- Name: history_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.history_id_seq OWNER TO material_user;

--
-- Name: history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.history_id_seq OWNED BY public.history.id;


--
-- Name: history_remarks; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.history_remarks (
    history_id integer NOT NULL,
    remark_text text,
    updated_by_role character varying(50),
    updated_by character varying(255),
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.history_remarks OWNER TO postgres;

--
-- Name: invoice_data; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.invoice_data (
    id integer NOT NULL,
    filename text,
    invoice_number text,
    invoice_date text,
    po_number text,
    buyer_name text,
    buyer_address text,
    buyer_gstin text,
    ship_to_name text,
    ship_to_address text,
    ship_to_state text,
    ship_to_code text,
    bill_to_state text,
    bill_to_code text,
    seller_name text,
    seller_address text,
    seller_gstin text,
    company_pan text,
    payment_terms text,
    amount_in_words text,
    total_taxable_amount text,
    cgst_rate text,
    cgst_amount text,
    sgst_rate text,
    sgst_amount text,
    igst_rate text,
    igst_amount text,
    total_tax_amount text,
    total_amount text,
    grand_total text,
    hsn_details jsonb,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    irn text
);


ALTER TABLE public.invoice_data OWNER TO material_user;

--
-- Name: lr_data; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.lr_data (
    id integer NOT NULL,
    filename text,
    lr_number text,
    lr_date text,
    consignor_name text,
    consignee_name text,
    vehicle_number text,
    material_description text,
    quantity text,
    weight text,
    delivery_address text,
    from_location text,
    to_location text,
    transporter_name text,
    freight_amount text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    uom character varying(20)
);


ALTER TABLE public.lr_data OWNER TO material_user;

--
-- Name: migo_entries; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.migo_entries (
    id integer NOT NULL,
    history_id integer NOT NULL,
    migo_doc_date text,
    migo_post_date text,
    migo_delivery_note text,
    migo_bill_of_lading text,
    migo_gr_slip_no text,
    migo_header_text text,
    migo_remarks text,
    items_data jsonb,
    material_doc_number text,
    migo_103_rf_status character varying(50) DEFAULT 'pending'::character varying,
    migo_103_rf_error text,
    migo_103_executed_at timestamp without time zone,
    migo_105_storage_loc text,
    migo_105_batch text,
    migo_105_vendor_invoice text,
    migo_105_remarks text,
    migo_105_rf_status character varying(50) DEFAULT 'pending'::character varying,
    migo_105_rf_error text,
    migo_105_executed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    migo_po_number text,
    miro_doc_number character varying(20),
    migo103_link_status character varying(50) DEFAULT 'pending'::character varying,
    migo103_link_error text,
    migo103_link_executed_at timestamp without time zone,
    migo105_link_status character varying(50) DEFAULT 'pending'::character varying,
    migo105_link_error text,
    migo105_link_executed_at timestamp without time zone
);


ALTER TABLE public.migo_entries OWNER TO material_user;

--
-- Name: migo_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.migo_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.migo_entries_id_seq OWNER TO material_user;

--
-- Name: migo_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.migo_entries_id_seq OWNED BY public.migo_entries.id;


--
-- Name: miro_entries; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.miro_entries (
    id integer NOT NULL,
    history_id integer NOT NULL,
    miro_transaction text DEFAULT '1'::text,
    miro_diff_posting text,
    miro_invoice_date text,
    miro_posting_date text,
    miro_reference text,
    miro_amount text,
    miro_tax_amount text,
    miro_tax_code text,
    miro_text text,
    miro_purchase_order text,
    items_data jsonb,
    rf_status character varying(50) DEFAULT 'pending'::character varying,
    rf_error_message text,
    rf_executed_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    miro_link_status character varying(50) DEFAULT 'pending'::character varying,
    miro_link_error text,
    miro_link_executed_at timestamp without time zone
);


ALTER TABLE public.miro_entries OWNER TO material_user;

--
-- Name: miro_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.miro_entries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.miro_entries_id_seq OWNER TO material_user;

--
-- Name: miro_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.miro_entries_id_seq OWNED BY public.miro_entries.id;


--
-- Name: notifications; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.notifications (
    id integer NOT NULL,
    user_target character varying(255),
    role_target character varying(50),
    history_id integer,
    title character varying(255),
    message text,
    type character varying(20),
    is_read boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.notifications OWNER TO material_user;

--
-- Name: notifications_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.notifications_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.notifications_id_seq OWNER TO material_user;

--
-- Name: notifications_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.notifications_id_seq OWNED BY public.notifications.id;


--
-- Name: pending_po_updates; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.pending_po_updates (
    id integer NOT NULL,
    history_id integer NOT NULL,
    po_number text NOT NULL,
    gate_in_number text NOT NULL,
    requested_by character varying(255),
    requested_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    target_username character varying(255),
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    resolved_by character varying(255),
    resolved_at timestamp without time zone,
    error_message text
);


ALTER TABLE public.pending_po_updates OWNER TO material_user;

--
-- Name: pending_po_updates_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.pending_po_updates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pending_po_updates_id_seq OWNER TO material_user;

--
-- Name: pending_po_updates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.pending_po_updates_id_seq OWNED BY public.pending_po_updates.id;


--
-- Name: po_line_items; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.po_line_items (
    id integer NOT NULL,
    history_id integer NOT NULL,
    item_no character varying(10),
    material character varying(50),
    short_text text,
    po_qty character varying(30),
    unit character varying(20),
    delivery_date character varying(20),
    fetched_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    rate character varying(30),
    amount character varying(30),
    hsn_sac character varying(30),
    open_qty character varying(30)
);


ALTER TABLE public.po_line_items OWNER TO material_user;

--
-- Name: po_line_items_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.po_line_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.po_line_items_id_seq OWNER TO material_user;

--
-- Name: po_line_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.po_line_items_id_seq OWNED BY public.po_line_items.id;


--
-- Name: rf_queue; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.rf_queue (
    id integer NOT NULL,
    history_id integer NOT NULL,
    step character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying,
    payload jsonb,
    result jsonb,
    error_message text,
    attempts integer DEFAULT 0,
    queued_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    started_at timestamp without time zone,
    completed_at timestamp without time zone
);


ALTER TABLE public.rf_queue OWNER TO material_user;

--
-- Name: rf_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.rf_queue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.rf_queue_id_seq OWNER TO material_user;

--
-- Name: rf_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.rf_queue_id_seq OWNED BY public.rf_queue.id;


--
-- Name: storage_locations; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.storage_locations (
    id integer NOT NULL,
    code character varying(10) NOT NULL,
    description character varying(100) NOT NULL,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.storage_locations OWNER TO material_user;

--
-- Name: storage_locations_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.storage_locations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.storage_locations_id_seq OWNER TO material_user;

--
-- Name: storage_locations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.storage_locations_id_seq OWNED BY public.storage_locations.id;


--
-- Name: supplier_master; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.supplier_master (
    supplier character varying(50) NOT NULL,
    name_1 character varying(255),
    tax_number_3 character varying(50),
    permanent_account_number character varying(100),
    name character varying(255),
    name_2 character varying(255),
    name_3 character varying(255),
    name_4 character varying(255),
    city character varying(255),
    district character varying(255),
    postal_code character varying(50),
    street_2 character varying(255),
    street_3 character varying(255),
    street_4 character varying(255),
    street_5 character varying(255),
    building character varying(255),
    floor character varying(100),
    rg character varying(50),
    last_synced_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


ALTER TABLE public.supplier_master OWNER TO material_user;

--
-- Name: users; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(255) NOT NULL,
    password character varying(255),
    role character varying(50) NOT NULL,
    name character varying(255) NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    email character varying(255),
    email_notifications_enabled boolean DEFAULT false,
    step_roles character varying(255) DEFAULT 'all'::character varying,
    admin_edit boolean DEFAULT true NOT NULL,
    auth_type character varying(10) DEFAULT 'local'::character varying NOT NULL
);


ALTER TABLE public.users OWNER TO material_user;

--
-- Name: COLUMN users.admin_edit; Type: COMMENT; Schema: public; Owner: material_user
--

COMMENT ON COLUMN public.users.admin_edit IS 'SuperAdmin only. TRUE = can edit/act on every tab + manage users. FALSE = view-only across every tab, cannot edit or act anywhere (including User Management itself). Ignored for role = ''User''.';


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO material_user;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: vehicle_master; Type: TABLE; Schema: public; Owner: material_user
--

CREATE TABLE public.vehicle_master (
    id integer NOT NULL,
    truck_number character varying(30) NOT NULL,
    driver_name character varying(255),
    transporter_name character varying(255),
    licence_number character varying(60),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.vehicle_master OWNER TO material_user;

--
-- Name: vehicle_master_id_seq; Type: SEQUENCE; Schema: public; Owner: material_user
--

CREATE SEQUENCE public.vehicle_master_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vehicle_master_id_seq OWNER TO material_user;

--
-- Name: vehicle_master_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: material_user
--

ALTER SEQUENCE public.vehicle_master_id_seq OWNED BY public.vehicle_master.id;


--
-- Name: admin_action_log id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.admin_action_log ALTER COLUMN id SET DEFAULT nextval('public.admin_action_log_id_seq'::regclass);


--
-- Name: dms_document_links id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.dms_document_links ALTER COLUMN id SET DEFAULT nextval('public.dms_document_links_id_seq'::regclass);


--
-- Name: gate_in_entries id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gate_in_entries ALTER COLUMN id SET DEFAULT nextval('public.gate_in_entries_id_seq'::regclass);


--
-- Name: gst_approval id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gst_approval ALTER COLUMN id SET DEFAULT nextval('public.gst_approval_id_seq'::regclass);


--
-- Name: history id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.history ALTER COLUMN id SET DEFAULT nextval('public.history_id_seq'::regclass);


--
-- Name: history_comments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.history_comments ALTER COLUMN id SET DEFAULT nextval('public.history_comments_id_seq'::regclass);


--
-- Name: history_extras id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.history_extras ALTER COLUMN id SET DEFAULT nextval('public.history_extras_id_seq'::regclass);


--
-- Name: migo_entries id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.migo_entries ALTER COLUMN id SET DEFAULT nextval('public.migo_entries_id_seq'::regclass);


--
-- Name: miro_entries id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.miro_entries ALTER COLUMN id SET DEFAULT nextval('public.miro_entries_id_seq'::regclass);


--
-- Name: notifications id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.notifications ALTER COLUMN id SET DEFAULT nextval('public.notifications_id_seq'::regclass);


--
-- Name: pending_po_updates id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.pending_po_updates ALTER COLUMN id SET DEFAULT nextval('public.pending_po_updates_id_seq'::regclass);


--
-- Name: po_line_items id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.po_line_items ALTER COLUMN id SET DEFAULT nextval('public.po_line_items_id_seq'::regclass);


--
-- Name: rf_queue id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.rf_queue ALTER COLUMN id SET DEFAULT nextval('public.rf_queue_id_seq'::regclass);


--
-- Name: storage_locations id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.storage_locations ALTER COLUMN id SET DEFAULT nextval('public.storage_locations_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: vehicle_master id; Type: DEFAULT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.vehicle_master ALTER COLUMN id SET DEFAULT nextval('public.vehicle_master_id_seq'::regclass);


--
-- Name: admin_action_log admin_action_log_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.admin_action_log
    ADD CONSTRAINT admin_action_log_pkey PRIMARY KEY (id);


--
-- Name: dms_document_links dms_document_links_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.dms_document_links
    ADD CONSTRAINT dms_document_links_pkey PRIMARY KEY (id);


--
-- Name: ewaybill_data ewaybill_data_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.ewaybill_data
    ADD CONSTRAINT ewaybill_data_pkey PRIMARY KEY (id);


--
-- Name: gate_in_entries gate_in_entries_history_id_key; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gate_in_entries
    ADD CONSTRAINT gate_in_entries_history_id_key UNIQUE (history_id);


--
-- Name: gate_in_entries gate_in_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gate_in_entries
    ADD CONSTRAINT gate_in_entries_pkey PRIMARY KEY (id);


--
-- Name: gst_approval gst_approval_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gst_approval
    ADD CONSTRAINT gst_approval_pkey PRIMARY KEY (id);


--
-- Name: history_comments history_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.history_comments
    ADD CONSTRAINT history_comments_pkey PRIMARY KEY (id);


--
-- Name: history_extras history_extras_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.history_extras
    ADD CONSTRAINT history_extras_pkey PRIMARY KEY (id);


--
-- Name: history history_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.history
    ADD CONSTRAINT history_pkey PRIMARY KEY (id);


--
-- Name: history_remarks history_remarks_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.history_remarks
    ADD CONSTRAINT history_remarks_pkey PRIMARY KEY (history_id);


--
-- Name: invoice_data invoice_data_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.invoice_data
    ADD CONSTRAINT invoice_data_pkey PRIMARY KEY (id);


--
-- Name: lr_data lr_data_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.lr_data
    ADD CONSTRAINT lr_data_pkey PRIMARY KEY (id);


--
-- Name: migo_entries migo_entries_history_id_key; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.migo_entries
    ADD CONSTRAINT migo_entries_history_id_key UNIQUE (history_id);


--
-- Name: migo_entries migo_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.migo_entries
    ADD CONSTRAINT migo_entries_pkey PRIMARY KEY (id);


--
-- Name: miro_entries miro_entries_history_id_key; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.miro_entries
    ADD CONSTRAINT miro_entries_history_id_key UNIQUE (history_id);


--
-- Name: miro_entries miro_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.miro_entries
    ADD CONSTRAINT miro_entries_pkey PRIMARY KEY (id);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (id);


--
-- Name: pending_po_updates pending_po_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.pending_po_updates
    ADD CONSTRAINT pending_po_updates_pkey PRIMARY KEY (id);


--
-- Name: po_line_items po_line_items_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.po_line_items
    ADD CONSTRAINT po_line_items_pkey PRIMARY KEY (id);


--
-- Name: rf_queue rf_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.rf_queue
    ADD CONSTRAINT rf_queue_pkey PRIMARY KEY (id);


--
-- Name: storage_locations storage_locations_code_key; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.storage_locations
    ADD CONSTRAINT storage_locations_code_key UNIQUE (code);


--
-- Name: storage_locations storage_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.storage_locations
    ADD CONSTRAINT storage_locations_pkey PRIMARY KEY (id);


--
-- Name: supplier_master supplier_master_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.supplier_master
    ADD CONSTRAINT supplier_master_pkey PRIMARY KEY (supplier);


--
-- Name: dms_document_links uq_dms_document_links_filename; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.dms_document_links
    ADD CONSTRAINT uq_dms_document_links_filename UNIQUE (filename);


--
-- Name: gst_approval uq_gst_approval_history; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gst_approval
    ADD CONSTRAINT uq_gst_approval_history UNIQUE (history_id);


--
-- Name: pending_po_updates uq_pending_po_updates_history; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.pending_po_updates
    ADD CONSTRAINT uq_pending_po_updates_history UNIQUE (history_id);


--
-- Name: vehicle_master uq_vehicle_driver; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.vehicle_master
    ADD CONSTRAINT uq_vehicle_driver UNIQUE (truck_number, driver_name);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: vehicle_master vehicle_master_pkey; Type: CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.vehicle_master
    ADD CONSTRAINT vehicle_master_pkey PRIMARY KEY (id);


--
-- Name: idx_admin_action_log_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_admin_action_log_history_id ON public.admin_action_log USING btree (history_id);


--
-- Name: idx_admin_action_log_performed_at; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_admin_action_log_performed_at ON public.admin_action_log USING btree (performed_at);


--
-- Name: idx_dms_document_links_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_dms_document_links_history_id ON public.dms_document_links USING btree (history_id);


--
-- Name: idx_gate_in_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_gate_in_history_id ON public.gate_in_entries USING btree (history_id);


--
-- Name: idx_gst_approval_gstin_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_gst_approval_gstin_status ON public.gst_approval USING btree (gstin_status);


--
-- Name: idx_gst_approval_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_gst_approval_history_id ON public.gst_approval USING btree (history_id);


--
-- Name: idx_gst_approval_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_gst_approval_status ON public.gst_approval USING btree (approval_status);


--
-- Name: idx_history_approval_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_approval_status ON public.history USING btree (approval_status);


--
-- Name: idx_history_comments_history_role_created; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_history_comments_history_role_created ON public.history_comments USING btree (history_id, role, created_at DESC);


--
-- Name: idx_history_created_at; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_created_at ON public.history USING btree (created_at DESC);


--
-- Name: idx_history_dms_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_dms_status ON public.history USING btree (dms_status);


--
-- Name: idx_history_extras_doc_type; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_extras_doc_type ON public.history_extras USING btree (history_id, doc_type);


--
-- Name: idx_history_extras_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_extras_history_id ON public.history_extras USING btree (history_id);


--
-- Name: idx_history_gst_check; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_gst_check ON public.history USING btree (gst_check);


--
-- Name: idx_history_invoice_number; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_invoice_number ON public.history USING btree (invoice_number);


--
-- Name: idx_history_ocr_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_ocr_status ON public.history USING btree (ocr_status);


--
-- Name: idx_history_po_flow; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_po_flow ON public.history USING btree (po_flow_type);


--
-- Name: idx_history_po_number; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_history_po_number ON public.history USING btree (po_number);


--
-- Name: idx_invoice_number; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_invoice_number ON public.invoice_data USING btree (invoice_number);


--
-- Name: idx_migo_entries_migo103_link_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_migo_entries_migo103_link_status ON public.migo_entries USING btree (migo103_link_status);


--
-- Name: idx_migo_entries_migo105_link_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_migo_entries_migo105_link_status ON public.migo_entries USING btree (migo105_link_status);


--
-- Name: idx_migo_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_migo_history_id ON public.migo_entries USING btree (history_id);


--
-- Name: idx_miro_entries_miro_link_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_miro_entries_miro_link_status ON public.miro_entries USING btree (miro_link_status);


--
-- Name: idx_miro_history_id; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_miro_history_id ON public.miro_entries USING btree (history_id);


--
-- Name: idx_notif_created; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_notif_created ON public.notifications USING btree (created_at);


--
-- Name: idx_notif_role; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_notif_role ON public.notifications USING btree (role_target);


--
-- Name: idx_notif_user_unread; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_notif_user_unread ON public.notifications USING btree (user_target, is_read) WHERE (is_read = false);


--
-- Name: idx_pending_po_updates_history; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_pending_po_updates_history ON public.pending_po_updates USING btree (history_id);


--
-- Name: idx_pending_po_updates_target; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_pending_po_updates_target ON public.pending_po_updates USING btree (target_username, status);


--
-- Name: idx_po_line_items_history; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_po_line_items_history ON public.po_line_items USING btree (history_id);


--
-- Name: idx_rf_queue_history; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_rf_queue_history ON public.rf_queue USING btree (history_id);


--
-- Name: idx_rf_queue_status; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_rf_queue_status ON public.rf_queue USING btree (status);


--
-- Name: idx_supplier_master_city; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_supplier_master_city ON public.supplier_master USING btree (city);


--
-- Name: idx_supplier_master_district; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_supplier_master_district ON public.supplier_master USING btree (district);


--
-- Name: idx_supplier_master_name_1_trgm; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_supplier_master_name_1_trgm ON public.supplier_master USING gin (name_1 public.gin_trgm_ops);


--
-- Name: idx_supplier_master_name_trgm; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_supplier_master_name_trgm ON public.supplier_master USING gin (name public.gin_trgm_ops);


--
-- Name: idx_users_auth_type; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_users_auth_type ON public.users USING btree (auth_type);


--
-- Name: idx_vehicle_truck_number; Type: INDEX; Schema: public; Owner: material_user
--

CREATE INDEX idx_vehicle_truck_number ON public.vehicle_master USING btree (truck_number);


--
-- Name: dms_document_links dms_document_links_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.dms_document_links
    ADD CONSTRAINT dms_document_links_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: ewaybill_data ewaybill_data_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.ewaybill_data
    ADD CONSTRAINT ewaybill_data_id_fkey FOREIGN KEY (id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: gate_in_entries gate_in_entries_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gate_in_entries
    ADD CONSTRAINT gate_in_entries_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: gst_approval gst_approval_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.gst_approval
    ADD CONSTRAINT gst_approval_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: history_comments history_comments_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.history_comments
    ADD CONSTRAINT history_comments_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: history_extras history_extras_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.history_extras
    ADD CONSTRAINT history_extras_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: history_remarks history_remarks_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.history_remarks
    ADD CONSTRAINT history_remarks_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: invoice_data invoice_data_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.invoice_data
    ADD CONSTRAINT invoice_data_id_fkey FOREIGN KEY (id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: lr_data lr_data_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.lr_data
    ADD CONSTRAINT lr_data_id_fkey FOREIGN KEY (id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: migo_entries migo_entries_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.migo_entries
    ADD CONSTRAINT migo_entries_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: miro_entries miro_entries_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.miro_entries
    ADD CONSTRAINT miro_entries_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: notifications notifications_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT notifications_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: pending_po_updates pending_po_updates_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.pending_po_updates
    ADD CONSTRAINT pending_po_updates_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: po_line_items po_line_items_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.po_line_items
    ADD CONSTRAINT po_line_items_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: rf_queue rf_queue_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: material_user
--

ALTER TABLE ONLY public.rf_queue
    ADD CONSTRAINT rf_queue_history_id_fkey FOREIGN KEY (history_id) REFERENCES public.history(id) ON DELETE CASCADE;


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: pg_database_owner
--

GRANT ALL ON SCHEMA public TO material_user;


--
-- Name: TABLE history_comments; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.history_comments TO material_user;


--
-- Name: SEQUENCE history_comments_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON SEQUENCE public.history_comments_id_seq TO material_user;


--
-- Name: TABLE history_remarks; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.history_remarks TO material_user;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON SEQUENCES TO material_user;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO material_user;


--
-- PostgreSQL database dump complete
--

\unrestrict JXNs0A3EJAZoLM2ZeIFe7wMtquzIhXseOfrC6tjAvbZWwb2IbU5s6XcSJupU0o0

