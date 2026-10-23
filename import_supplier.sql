CREATE TEMP TABLE supplier_master_staging (
    supplier                    TEXT,
    name_1                      TEXT,
    tax_number_3                TEXT,
    permanent_account_number    TEXT,
    name                        TEXT,
    name_2                      TEXT,
    name_3                      TEXT,
    name_4                      TEXT,
    city                        TEXT,
    district                    TEXT,
    postal_code                 TEXT,
    street_2                    TEXT,
    street_3                    TEXT,
    street_4                    TEXT,
    street_5                    TEXT,
    building                    TEXT,
    floor                       TEXT,
    rg                          TEXT,
    trailing_blank              TEXT
);

\copy supplier_master_staging FROM 'C:/Users/ctn_suresh/Downloads/zvend_address_data.csv' WITH (FORMAT csv, DELIMITER ',', HEADER, QUOTE '"');

INSERT INTO supplier_master (supplier, name_1, tax_number_3, permanent_account_number,
    name, name_2, name_3, name_4, city, district, postal_code,
    street_2, street_3, street_4, street_5, building, floor, rg)
SELECT supplier, name_1, tax_number_3, permanent_account_number,
    name, name_2, name_3, name_4, city, district, postal_code,
    street_2, street_3, street_4, street_5, building, floor, rg
FROM supplier_master_staging
ON CONFLICT (supplier) DO UPDATE SET
    name_1 = EXCLUDED.name_1, tax_number_3 = EXCLUDED.tax_number_3,
    permanent_account_number = EXCLUDED.permanent_account_number,
    name = EXCLUDED.name, name_2 = EXCLUDED.name_2, name_3 = EXCLUDED.name_3,
    name_4 = EXCLUDED.name_4, city = EXCLUDED.city, district = EXCLUDED.district,
    postal_code = EXCLUDED.postal_code, street_2 = EXCLUDED.street_2,
    street_3 = EXCLUDED.street_3, street_4 = EXCLUDED.street_4,
    street_5 = EXCLUDED.street_5, building = EXCLUDED.building,
    floor = EXCLUDED.floor, rg = EXCLUDED.rg;

SELECT COUNT(*) FROM supplier_master;