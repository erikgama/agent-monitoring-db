-- name: schema_summary
SELECT
  TABLE_SCHEMA,
  COUNT(*) AS table_count,
  SUM(TABLE_ROWS) AS estimated_rows,
  SUM(DATA_LENGTH) AS data_bytes,
  SUM(INDEX_LENGTH) AS index_bytes,
  SUM(DATA_LENGTH + INDEX_LENGTH) AS total_bytes
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
  AND TABLE_SCHEMA = 'sakila'
  AND ENGINE = 'InnoDB'
GROUP BY TABLE_SCHEMA
ORDER BY total_bytes DESC;

-- name: largest_tables
SELECT
  TABLE_SCHEMA,
  TABLE_NAME,
  TABLE_ROWS AS estimated_rows,
  DATA_LENGTH AS data_bytes,
  INDEX_LENGTH AS index_bytes,
  DATA_FREE AS free_bytes,
  ROUND(100 * DATA_FREE /
        NULLIF(DATA_LENGTH + INDEX_LENGTH + DATA_FREE, 0), 2) AS fragmentation_pct
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
  AND TABLE_SCHEMA = 'sakila'
  AND ENGINE = 'InnoDB'
ORDER BY (DATA_LENGTH + INDEX_LENGTH) DESC
LIMIT {{tables_limit}};

-- name: missing_primary_keys
SELECT t.TABLE_SCHEMA, t.TABLE_NAME
FROM information_schema.TABLES t
LEFT JOIN information_schema.TABLE_CONSTRAINTS tc
  ON tc.TABLE_SCHEMA = t.TABLE_SCHEMA
 AND tc.TABLE_NAME = t.TABLE_NAME
 AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND t.ENGINE = 'InnoDB'
  AND t.TABLE_SCHEMA = 'sakila'
  AND tc.CONSTRAINT_NAME IS NULL
ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME;

-- name: auto_increment
SELECT
  t.TABLE_SCHEMA,
  t.TABLE_NAME,
  t.AUTO_INCREMENT,
  c.COLUMN_NAME,
  c.DATA_TYPE,
  c.COLUMN_TYPE
FROM information_schema.TABLES t
JOIN information_schema.COLUMNS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA
 AND c.TABLE_NAME = t.TABLE_NAME
 AND c.EXTRA LIKE '%auto_increment%'
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND t.ENGINE = 'InnoDB'
  AND t.TABLE_SCHEMA = 'sakila'
  AND t.AUTO_INCREMENT IS NOT NULL;
