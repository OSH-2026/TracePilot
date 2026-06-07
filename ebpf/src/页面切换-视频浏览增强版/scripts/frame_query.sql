-- TracePilot frame_query.sql
-- Usage: trace_processor_shell trace -q frame_query.sql > frames.txt
--
-- Output: frame_type|frame_token|intended_vsync|expected_start|expected_end|actual_end|is_jank|delay_ms
--   SF = SurfaceFlinger UI frame
--   VD = Video Decode frame
--   VF = Video fallback (setLayerBuffer)
--   GS = GPU stall (gpu_work_period)
--   AP = Audio position (getTimestamp)

-- ═══════════════════════════════════════════════════════════════════════
-- Common Table Expressions (defined once, used by all SELECTs below)
-- ═══════════════════════════════════════════════════════════════════════

WITH

-- ── Part A: SurfaceFlinger frames ──
sf_track AS (
  SELECT DISTINCT track_id FROM slice WHERE name LIKE 'beginFrame %' LIMIT 1
),
sf_events AS (
  SELECT s.ts, s.name,
    CASE WHEN s.name LIKE 'beginFrame %' THEN 1 ELSE 0 END AS is_begin
  FROM slice s, sf_track t
  WHERE s.track_id = t.track_id
    AND (s.name LIKE 'beginFrame %'
         OR s.name LIKE 'presentFrameAndReleaseLayers for Common Panel%'
         OR s.name = 'finishFrame'
         OR s.name = 'incrementJankyFrames')
),
sf_groups AS (
  SELECT ts, name,
    SUM(is_begin) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS grp
  FROM sf_events
),
sf_frames AS (
  SELECT
    grp,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN
      CAST(SUBSTR(name, 12, INSTR(SUBSTR(name, 12), ' ') - 1) AS INT)
    END) AS frame_number,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN ts END) AS begin_ts,
    MAX(CASE WHEN name LIKE 'beginFrame %' THEN
      CAST(SUBSTR(name, INSTR(name, 'vsyncIn ') + 8,
        INSTR(SUBSTR(name, INSTR(name, 'vsyncIn ') + 8), 'ms') - 1) AS REAL)
    END) AS vsync_in_ms,
    MAX(CASE WHEN name LIKE 'presentFrameAndReleaseLayers%' THEN ts END) AS present_ts,
    MAX(CASE WHEN name = 'finishFrame' THEN ts END) AS finish_ts,
    MAX(CASE WHEN name = 'incrementJankyFrames' THEN 1 ELSE 0 END) AS is_jank
  FROM sf_groups GROUP BY grp
),

-- ── Part B: Video decoder frames ──
has_video AS (
  SELECT COUNT(*) > 0 AS v FROM slice
  WHERE (name LIKE '%ACodec%' OR name LIKE '%CCodec%'
         OR name LIKE '%onOutputBufferDrained%' OR name LIKE '%onFrameAvailable%')
    AND dur > 0
),
decoder_events AS (
  SELECT s.ts, s.ts + s.dur AS ts_end, s.name
  FROM slice s
  WHERE s.name LIKE '%onOutputBufferDrained%'
     OR s.name LIKE '%outputBuffer%'
     OR s.name LIKE '%CCodec::onFrameRendered%'
     OR s.name = 'dequeueOutputBuffer done'
),
video_frames AS (
  SELECT ts AS frame_start,
    LEAD(ts, 1, ts + 33333333) OVER (ORDER BY ts) AS frame_end_ideal,
    ts_end
  FROM decoder_events
  WHERE (SELECT v FROM has_video) = 1
),
-- Detect if primary decoder found anything (for fallback trigger)
decoder_cnt AS (
  SELECT COUNT(*) AS c FROM decoder_events
)

-- ═══════════════════════════════════════════════════════════════════════
-- Main SELECT: UNION ALL of all frame types
-- ═══════════════════════════════════════════════════════════════════════

-- PART A: SurfaceFlinger UI frames
SELECT
  'SF' AS frame_type,
  frame_number AS frame_token,
  CAST(begin_ts + vsync_in_ms * 1000000 AS INT) AS intended_vsync,
  CAST(begin_ts + vsync_in_ms * 1000000 AS INT) AS expected_start,
  CAST(begin_ts + vsync_in_ms * 1000000 + 16666666 AS INT) AS expected_end,
  COALESCE(present_ts, finish_ts) AS actual_end,
  is_jank,
  CASE WHEN is_jank = 1
    THEN (COALESCE(present_ts, finish_ts) - (begin_ts + vsync_in_ms * 1000000)) / 1e6
    ELSE 0.0
  END AS delay_ms
FROM sf_frames
WHERE frame_number IS NOT NULL
  AND (present_ts IS NOT NULL OR finish_ts IS NOT NULL)

UNION ALL

-- PART B: Video decoder frames
SELECT
  'VD' AS frame_type,
  CAST(ROW_NUMBER() OVER (ORDER BY frame_start) AS INT) AS frame_token,
  (frame_start + frame_end_ideal) / 2 AS intended_vsync,
  frame_start AS expected_start,
  frame_end_ideal AS expected_end,
  ts_end AS actual_end,
  CASE WHEN (frame_end_ideal - frame_start) > 33000000 THEN 1 ELSE 0 END AS is_jank,
  CASE WHEN (frame_end_ideal - frame_start) > 33000000
    THEN (frame_end_ideal - frame_start - 16666666) / 1e6
    ELSE 0.0
  END AS delay_ms
FROM video_frames

UNION ALL

-- PART C: Video fallback (setLayerBuffer)
-- Only triggered when primary decoder query found < 10 events
SELECT
  'VF' AS frame_type,
  CAST(ROW_NUMBER() OVER (ORDER BY s.ts) AS INT) AS frame_token,
  s.ts AS intended_vsync,
  s.ts AS expected_start,
  s.ts + 16666666 AS expected_end,
  s.ts + s.dur AS actual_end,
  0 AS is_jank,
  0.0 AS delay_ms
FROM slice s
WHERE s.name LIKE 'setLayerBuffer%'
  AND s.dur > 0
  AND (SELECT c FROM decoder_cnt) < 10

-- UNION ALL

-- -- PART D: GPU stall events (disabled — table gpu_work_period not present in this trace)
-- SELECT
--   'GS' AS frame_type,
--   CAST(ROW_NUMBER() OVER (ORDER BY ts) AS INT) AS frame_token,
--   ts AS intended_vsync,
--   ts AS expected_start,
--   ts + dur AS expected_end,
--   ts + dur AS actual_end,
--   CASE WHEN dur > 20000000 THEN 1 ELSE 0 END AS is_jank,
--   CAST(dur / 1000000.0 AS REAL) AS delay_ms
-- FROM gpu_work_period
-- WHERE dur > 1000000

UNION ALL

-- PART E: Audio position events
SELECT
  'AP' AS frame_type,
  CAST(ROW_NUMBER() OVER (ORDER BY ts) AS INT) AS frame_token,
  ts AS intended_vsync,
  ts AS expected_start,
  ts AS expected_end,
  ts AS actual_end,
  0 AS is_jank,
  CAST(pos / 1000000.0 AS REAL) AS delay_ms
FROM (
  SELECT ts,
    CAST(SUBSTR(name, INSTR(name, 'pos=') + 4) AS INT) AS pos
  FROM slice
  WHERE (name LIKE '%getTimestamp%' OR name LIKE '%onMoreDataConsumed%')
    AND name LIKE '%pos=%'
)
WHERE pos > 0

ORDER BY intended_vsync ASC;