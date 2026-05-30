-- TracePilot frame_query.sql (extended for video scenario)
--
-- Usage:
--   trace_processor_shell trace -q frame_query.sql > frames.txt    (page_switch, default)
--   trace_processor_shell trace -q frame_query.sql > frames.txt    (video: auto-detected)
--
-- Output columns (tab-separated for SQL export):
--   frame_type|frame_token|intended_vsync_ns|expected_start|expected_end|actual_end|is_jank|delay_ms
--   frame_type = 'SF' (SurfaceFlinger UI frame) or 'VD' (Video Decode frame)
--
-- ======================================================================
-- PART A: SurfaceFlinger UI frames (page_switch + video UI layer)
-- ======================================================================

WITH sf_track AS (
  SELECT DISTINCT track_id FROM slice WHERE name LIKE 'beginFrame %' LIMIT 1
),
sf_events AS (
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
sf_groups AS (
  SELECT
    ts, name,
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
  FROM sf_groups
  GROUP BY grp
)
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

-- ======================================================================
-- PART B: Video decoder frames (video scenario)
--   Detects ACodec/CCodec output buffer events as "video frames".
--   Each decoded frame: queue → decode → render
-- ======================================================================

WITH
-- Detect if there are video decoder events
has_video AS (
  SELECT COUNT(*) > 0 AS v FROM slice
  WHERE (name LIKE '%ACodec%' OR name LIKE '%CCodec%'
         OR name LIKE '%onOutputBufferDrained%' OR name LIKE '%onFrameAvailable%')
    AND dur > 0
  LIMIT 1
),
-- Identify decoder track: find the thread track with "onOutputBufferDrained" events
decoder_track AS (
  SELECT track_id FROM slice
  WHERE name LIKE '%onOutputBufferDrained%'
  GROUP BY track_id
  ORDER BY COUNT(*) DESC LIMIT 1
),
-- Collect all video decode events: when decoder outputs a frame
decoder_events AS (
  SELECT
    s.ts,
    s.ts + s.dur AS ts_end,
    s.name,
    -- Extract frame index from the name if possible
    CAST(COALESCE(
      CAST(SUBSTR(s.name, INSTR(s.name, '(') + 1) AS INT),
      0
    ) AS INT) AS frame_idx
  FROM slice s
  WHERE s.name LIKE '%onOutputBufferDrained%'
     OR s.name LIKE '%outputBuffer%'
     OR s.name LIKE '%CCodec::onFrameRendered%'
     OR s.name = 'dequeueOutputBuffer done'
  ORDER BY s.ts
),
-- Generate video "frame windows": each decoded frame spans from
-- output to the next output or max window of ~33ms (30fps)
video_frames AS (
  SELECT
    ROW_NUMBER() OVER (ORDER BY ts) AS frame_token,
    ts AS frame_start,
    LEAD(ts, 1, ts + 33333333) OVER (ORDER BY ts) AS frame_end_ideal,
    ts_end
  FROM decoder_events
  WHERE (SELECT v FROM has_video) = 1
)
SELECT
  'VD' AS frame_type,
  frame_token,
  (frame_start + frame_end_ideal) / 2 AS intended_vsync,
  frame_start AS expected_start,
  frame_end_ideal AS expected_end,
  ts_end AS actual_end,
  -- is_jank = frame interval > 1.5x target period (30fps = 33ms, 60fps = 16.7ms)
  CASE WHEN (frame_end_ideal - frame_start) > 33000000 THEN 1 ELSE 0 END AS is_jank,
  CASE WHEN (frame_end_ideal - frame_start) > 33000000
    THEN (frame_end_ideal - frame_start - 16666666) / 1e6
    ELSE 0.0
  END AS delay_ms
FROM video_frames

UNION ALL

-- ======================================================================
-- PART C: Video frame fallback (setLayerBuffer events)
--   Alternative when onOutputBufferDrained is not available.
--   setLayerBuffer fires when SurfaceFlinger receives a new buffer
--   from any Surface (including video decoder output).
-- ======================================================================

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
  AND s.ts > (SELECT MIN(ts) FROM decoder_events)
  AND (SELECT COUNT(*) FROM decoder_events) < 10  -- Only if primary decoder query found nothing
  AND s.ts < (SELECT MAX(ts) FROM decoder_events) + 1000000000

ORDER BY intended_vsync ASC

UNION ALL

-- ======================================================================
-- PART D: GPU stall events (gpu_work_period)
--   Fires when a GPU work item completes. dur > 16.67ms = GPU stalled this frame.
--   frame_type='GS', is_jank=1 if GPU took >1 frame time.
-- ======================================================================
SELECT
  'GS' AS frame_type,
  CAST(ROW_NUMBER() OVER (ORDER BY ts) AS INT) AS frame_token,
  ts AS intended_vsync,
  ts AS expected_start,
  ts + dur AS expected_end,
  ts + dur AS actual_end,
  CASE WHEN dur > 20000000 THEN 1 ELSE 0 END AS is_jank,
  CAST(dur / 1000000.0 AS REAL) AS delay_ms
FROM gpu_work_period
WHERE dur > 1000000  -- >1ms, filter noise
  AND (SELECT COUNT(*) FROM gpu_work_period WHERE dur > 16666666) > 0  -- has GPU stall

UNION ALL

-- ======================================================================
-- PART E: Audio sync drift
--   AudioTrack::getTimestamp reports audio presentation position.
--   Compare with video frame timestamps to detect AV sync drift.
--   frame_type='AP', value in delay_ms = estimated drift from video.
-- ======================================================================
SELECT
  'AP' AS frame_type,
  CAST(ROW_NUMBER() OVER (ORDER BY ts) AS INT) AS frame_token,
  ts AS intended_vsync,
  ts AS expected_start,
  ts AS expected_end,  -- placeholder
  ts AS actual_end,    -- placeholder
  0 AS is_jank,
  CAST(pos / 1000000.0 AS REAL) AS delay_ms  -- audio position in ms
FROM (
  SELECT ts,
    CAST(SUBSTR(name, INSTR(name, 'pos=') + 4) AS INT) AS pos
  FROM slice
  WHERE (name LIKE '%getTimestamp%' OR name LIKE '%onMoreDataConsumed%')
    AND name LIKE '%pos=%'
    AND (SELECT COUNT(*) FROM slice WHERE name LIKE '%getTimestamp%') > 0
)
WHERE pos > 0
ORDER BY intended_vsync ASC;