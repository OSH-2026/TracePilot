-- TracePilot frame_query.sql
-- Usage: trace_processor_shell trace.perfetto-trace -q frame_query.sql > frames.txt
--
-- Output: one line per frame, space-separated fields:
--   frame_number intended_vsync_ns expected_start_ns expected_end_ns actual_present_ns is_jank delay_ms
--
-- Reconstructs frame timeline from raw SurfaceFlinger atrace slices
-- (beginFrame / presentFrameAndReleaseLayers / incrementJankyFrames).

WITH sf_track AS (
  SELECT DISTINCT track_id FROM slice WHERE name LIKE 'beginFrame %' LIMIT 1
),
raw_events AS (
  SELECT
    s.ts,
    s.name,
    CASE WHEN s.name LIKE 'beginFrame %' THEN 1 ELSE 0 END AS is_begin
  FROM slice s, sf_track t
  WHERE s.track_id = t.track_id
    AND (s.name LIKE 'beginFrame %'
         OR s.name LIKE 'presentFrameAndReleaseLayers for Common Panel%'
         OR s.name = 'finishFrame'
         OR s.name = 'incrementJankyFrames')
),
frame_groups AS (
  SELECT
    ts,
    name,
    SUM(is_begin) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS frame_group
  FROM raw_events
),
frame_data AS (
  SELECT
    frame_group,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN
      CAST(SUBSTR(name, 12, INSTR(SUBSTR(name, 12), ' ') - 1) AS INT)
    END) AS frame_number,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN ts END) AS begin_ts,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN
      CAST(SUBSTR(name, INSTR(name, 'vsyncIn ') + 8,
        INSTR(SUBSTR(name, INSTR(name, 'vsyncIn ') + 8), 'ms') - 1) AS REAL)
    END) AS vsync_in_ms,
    MAX(CASE WHEN name LIKE 'presentFrameAndReleaseLayers for Common Panel%' THEN ts END) AS present_ts,
    MAX(CASE WHEN name = 'finishFrame' THEN ts END) AS finish_ts,
    MAX(CASE WHEN name = 'incrementJankyFrames' THEN 1 ELSE 0 END) AS is_jank
  FROM frame_groups
  GROUP BY frame_group
)
SELECT
  frame_number,
  intended_vsync_ns,
  intended_vsync_ns AS expected_start_ns,
  CAST(intended_vsync_ns + 16666666 AS INT) AS expected_end_ns,
  COALESCE(present_ts, finish_ts) AS actual_present_ns,
  is_jank,
  CASE WHEN is_jank = 1
    THEN (COALESCE(present_ts, finish_ts) - intended_vsync_ns) / 1000000.0
    ELSE 0.0
  END AS delay_ms
FROM (
  SELECT
    frame_number,
    CAST(begin_ts + vsync_in_ms * 1000000 AS INT) AS intended_vsync_ns,
    present_ts,
    finish_ts,
    is_jank
  FROM frame_data
  WHERE frame_number IS NOT NULL
    AND (present_ts IS NOT NULL OR finish_ts IS NOT NULL)
)
ORDER BY intended_vsync_ns ASC;
