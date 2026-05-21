-- Migration 003: add reject_reason for lead rejection tracking
-- Tracks WHY a lead was rejected (Inga solceller, Granntomt, Solfångare, Eternite)
-- Granntomt rejections auto-trigger a nearby-building scan

ALTER TABLE public.scout_leads
  ADD COLUMN IF NOT EXISTS reject_reason TEXT;
