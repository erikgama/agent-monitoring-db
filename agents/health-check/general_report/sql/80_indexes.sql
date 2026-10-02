-- name: unused_indexes
SELECT object_schema, object_name, index_name
FROM sys.schema_unused_indexes
WHERE object_schema = 'sakila'
LIMIT {{indexes_limit}};

-- name: redundant_indexes
SELECT table_schema, table_name, redundant_index_name,
       redundant_index_columns, dominant_index_name,
       dominant_index_columns
FROM sys.schema_redundant_indexes
WHERE table_schema = 'sakila'
LIMIT {{indexes_limit}};

-- name: selectivity
SELECT
  s.TABLE_SCHEMA,
  s.TABLE_NAME,
  s.INDEX_NAME,
  GROUP_CONCAT(s.COLUMN_NAME ORDER BY s.SEQ_IN_INDEX) AS index_columns,
  MAX(s.CARDINALITY) AS cardinality,
  MAX(t.TABLE_ROWS) AS estimated_rows,
  ROUND(100 * MAX(s.CARDINALITY) / NULLIF(MAX(t.TABLE_ROWS), 0), 2)
    AS selectivity_pct
FROM information_schema.STATISTICS s
JOIN information_schema.TABLES t
  ON t.TABLE_SCHEMA = s.TABLE_SCHEMA
 AND t.TABLE_NAME = s.TABLE_NAME
WHERE s.TABLE_SCHEMA = 'sakila'
  AND t.TABLE_TYPE = 'BASE TABLE'
  AND t.ENGINE = 'InnoDB'
GROUP BY s.TABLE_SCHEMA, s.TABLE_NAME, s.INDEX_NAME
ORDER BY selectivity_pct ASC
LIMIT {{indexes_limit}};

-- name: large_without_secondary
SELECT
  t.TABLE_SCHEMA,
  t.TABLE_NAME,
  t.DATA_LENGTH + t.INDEX_LENGTH AS total_bytes
FROM information_schema.TABLES t
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND t.ENGINE = 'InnoDB'
  AND t.TABLE_SCHEMA = 'sakila'
  AND t.DATA_LENGTH + t.INDEX_LENGTH >= {{large_table_bytes}}
  AND NOT EXISTS (
    SELECT 1
    FROM information_schema.STATISTICS s
    WHERE s.TABLE_SCHEMA = t.TABLE_SCHEMA
      AND s.TABLE_NAME = t.TABLE_NAME
      AND s.INDEX_NAME <> 'PRIMARY'
  )
ORDER BY total_bytes DESC
LIMIT {{indexes_limit}};
