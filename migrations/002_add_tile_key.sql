-- Migration 002: add tile_key for scan deduplication
-- Applied automatically via Supabase MCP 2026-05-21
-- Enables dedup: existing tile_keys are fetched before scan to skip already-scanned buildings

ALTER TABLE public.scout_leads
  ADD COLUMN IF NOT EXISTS tile_key TEXT DEFAULT '';
