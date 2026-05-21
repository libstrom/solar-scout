-- Migration 001: add status tracking and David notes to scout_leads
-- Run once in Supabase SQL editor (Dashboard → SQL Editor → New query)

ALTER TABLE scout_leads
  ADD COLUMN IF NOT EXISTS status      TEXT    DEFAULT 'ej_kontaktad',
  ADD COLUMN IF NOT EXISTS david_note  TEXT    DEFAULT '';
