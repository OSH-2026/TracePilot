-- TracePilot thermal_query.sql
-- Usage: trace_processor_shell trace -q thermal_query.sql > thermal_profile.txt
--
-- Output columns (whitespace separated):
--   timestamp_ns  temp_mc

WITH

thermal_from_slice AS (
  SELECT
    s.ts AS timestamp_ns,
    CAST(
      COALESCE(
        extract_arg(s.arg_set_id, 'temp'),
        extract_arg(s.arg_set_id, 'temperature')
      ) AS INT
    ) AS temp_mc
  FROM slice s
  WHERE (s.name GLOB '*thermal*' OR s.name GLOB '*Thermal*')
    AND s.ts > 0
    AND COALESCE(
      extract_arg(s.arg_set_id, 'temp'),
      extract_arg(s.arg_set_id, 'temperature')
    ) IS NOT NULL
),

thermal_from_counter AS (
  SELECT
    c.ts AS timestamp_ns,
    CAST(c.value AS INT) AS temp_mc
  FROM counter c
  JOIN counter_track t ON c.track_id = t.id
  WHERE (
    t.name = 'VIRTUAL-SKIN'
    OR t.name = 'VIRTUAL-SKIN-CPU-GPU'
    OR t.name = 'neutral_therm'
    OR t.name GLOB 'VIRTUAL-SKIN*-temp'
    OR t.name GLOB 'skin_therm*'
    OR t.name = 'charger_skin_therm'
    OR t.name = 'usb_pwr_therm2'
    OR t.name GLOB '*SKIN*temp*'
  )
    AND c.value >= 5000
    AND c.value <= 120000
),

all_temps AS (
  SELECT * FROM thermal_from_slice
  WHERE temp_mc >= 20000 AND temp_mc <= 120000
  UNION ALL
  SELECT * FROM thermal_from_counter
)

SELECT timestamp_ns, temp_mc
FROM all_temps
WHERE timestamp_ns > 0 AND temp_mc > 0
ORDER BY timestamp_ns ASC;
