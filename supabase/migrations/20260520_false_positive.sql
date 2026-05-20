-- Feedback-kolumner för false positive-märkning och bildlagring
ALTER TABLE scout_leads
  ADD COLUMN IF NOT EXISTS false_positive     BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS confirmed_image_url TEXT;

-- Index för att snabbt hämta false positives (framtida fine-tuning pipeline)
CREATE INDEX IF NOT EXISTS idx_scout_leads_false_positive
  ON scout_leads (user_id, false_positive)
  WHERE false_positive = TRUE;
