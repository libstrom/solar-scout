-- Add scan_session_id for grouping scan results and measuring precision
ALTER TABLE scout_leads ADD COLUMN IF NOT EXISTS scan_session_id UUID;
CREATE INDEX IF NOT EXISTS scout_leads_scan_session_id_idx ON scout_leads(scan_session_id);

-- View: precision per scan session
CREATE OR REPLACE VIEW scan_precision AS
SELECT
  scan_session_id,
  COUNT(*) AS total_leads,
  SUM(CASE WHEN status NOT IN ('ej_intresserad') AND reject_reason IS NULL THEN 1 ELSE 0 END) AS confirmed,
  SUM(CASE WHEN reject_reason IS NOT NULL THEN 1 ELSE 0 END) AS rejected,
  ROUND(
    100.0 * SUM(CASE WHEN reject_reason IS NOT NULL THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
    1
  ) AS false_positive_pct,
  MIN(created_at) AS scan_started_at
FROM scout_leads
WHERE scan_session_id IS NOT NULL
GROUP BY scan_session_id
ORDER BY scan_started_at DESC;
